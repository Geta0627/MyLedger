"""OCR 引擎抽象层。

设计要点：引擎可插拔。换供应商只需新增一个子类并在 registry 注册，
上层业务代码完全不用改。
"""
from __future__ import annotations

import base64
import json
import time
from typing import Dict, List, Optional, Tuple

import requests


class OcrError(Exception):
    """OCR 调用失败，message 面向用户可读。"""


class BaseOcrEngine:
    """所有 OCR 引擎的基类。

    子类必须实现 recognize()，返回 (纯文本列表, 原始响应)。
    可选实现 health_check()。
    """

    name = "base"
    display_name = "未命名引擎"

    def __init__(self, **config):
        self.config = config

    def recognize(self, image_path: str) -> Tuple[List[str], dict]:
        """识别图片，返回 (文本行列表, 原始响应)。

        失败时抛 OcrError。
        """
        raise NotImplementedError

    def health_check(self) -> Tuple[bool, str]:
        """检查配置是否可用。返回 (是否可用, 说明)。"""
        return True, "ok"

    # ---- 工具方法 ----

    @staticmethod
    def _read_image_b64(image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    @staticmethod
    def _read_image_bytes(image_path: str) -> bytes:
        with open(image_path, "rb") as f:
            return f.read()

    @staticmethod
    def _post(url: str, *, params=None, data=None, headers=None,
              files=None, timeout: int = 20, retries: int = 2) -> dict:
        """带重试的 POST。网络抖动时自动重试。"""
        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = requests.post(
                    url, params=params, data=data,
                    headers=headers, files=files, timeout=timeout,
                )
                # 4xx 通常是配置/参数问题，重试没意义，直接给可读提示
                if 400 <= resp.status_code < 500:
                    detail = ""
                    try:
                        body = resp.json()
                        detail = (body.get("error_description")
                                  or body.get("error_msg")
                                  or body.get("message")
                                  or "")
                    except ValueError:
                        detail = (resp.text or "")[:120]
                    if resp.status_code in (401, 403):
                        raise OcrError(
                            f"鉴权失败（HTTP {resp.status_code}）："
                            f"请检查 API Key / Secret Key 是否填写正确。{detail}"
                        )
                    raise OcrError(
                        f"OCR 服务拒绝了请求（HTTP {resp.status_code}）：{detail or '请检查参数'}"
                    )
                resp.raise_for_status()
                return resp.json()
            except OcrError:
                raise
            except requests.exceptions.Timeout as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.2 * (attempt + 1))
            except requests.exceptions.ConnectionError as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.2 * (attempt + 1))
            except requests.exceptions.RequestException as e:
                last_err = e
                if attempt < retries:
                    time.sleep(1.2 * (attempt + 1))
            except ValueError as e:
                # 返回的不是合法 JSON
                raise OcrError(f"OCR 服务返回了非 JSON 响应：{e}") from e

        # 重试耗尽——区分「没网」和「服务不可达」，给出不同指引
        if isinstance(last_err, requests.exceptions.ConnectionError):
            raise OcrError(
                "网络连接失败：请检查手机是否联网，或稍后再试。"
                "（若长期失败，可能是 OCR 服务地址被网络限制）"
            )
        raise OcrError(f"网络请求失败（已重试 {retries} 次）：{last_err}")


class BaiduOcrEngine(BaseOcrEngine):
    """百度智能云 OCR —— 通用文字识别（高精度版）。

    申请地址：https://console.bce.baidu.com/ai/#/ai/ocr/overview/index
    需要两个凭据：API Key 和 Secret Key。
    免费额度：通用文字识别高精度版每月 1000 次（以官网最新政策为准）。

    已针对「支付宝/微信账单截图」做参数优化：
    - 开启 detect_direction 处理截图旋转
    - 开启 paragraph 让同一行文本合并，便于金额匹配
    """

    name = "baidu"
    display_name = "百度智能云 OCR"

    TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"

    def __init__(self, api_key: str = "", secret_key: str = "", **kw):
        super().__init__(api_key=api_key, secret_key=secret_key, **kw)
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self._token: Optional[str] = None
        self._token_expire_at: float = 0.0

    def health_check(self) -> Tuple[bool, str]:
        if not self.api_key or not self.secret_key:
            return False, "未配置百度 API Key / Secret Key"
        try:
            self._get_token()
            return True, "百度 OCR 凭据有效"
        except OcrError as e:
            return False, str(e)

    def _get_token(self) -> str:
        """获取 access_token，带内存缓存（有效期 30 天，这里按 25 天刷新）。"""
        now = time.time()
        if self._token and now < self._token_expire_at:
            return self._token

        data = self._post(
            self.TOKEN_URL,
            params={
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self.secret_key,
            },
        )
        if "access_token" not in data:
            err = data.get("error_description") or data.get("error") or json.dumps(
                data, ensure_ascii=False
            )
            raise OcrError(f"百度鉴权失败：{err}")

        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 2592000))
        self._token_expire_at = now + max(expires_in - 432000, 3600)
        return self._token

    def recognize(self, image_path: str) -> Tuple[List[str], dict]:
        token = self._get_token()
        b64 = self._read_image_b64(image_path)

        # accurate_basic 接口限制：图片 base64 后不超过 4MB
        if len(b64) > 4 * 1024 * 1024:
            raise OcrError("图片过大（超过 4MB），请先压缩后再识别")

        payload = {
            "image": b64,
            "detect_direction": "true",
            "paragraph": "true",
            "probability": "true",
        }
        data = self._post(
            self.OCR_URL,
            params={"access_token": token},
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if "error_code" in data:
            code = data.get("error_code")
            msg = data.get("error_msg", "")
            hint = {
                17: "今日调用量已超限，请明天再试",
                18: "QPS 超限，请稍后重试",
                110: "access_token 无效，请检查 API Key",
                111: "access_token 过期，请重新识别",
                216630: "识别失败，请换一张更清晰的图片",
            }.get(code, msg)
            raise OcrError(f"百度 OCR 错误 [{code}]：{hint}")

        words = [item.get("words", "") for item in data.get("words_result", [])]
        return words, data


class TencentOcrEngine(BaseOcrEngine):
    """腾讯云 OCR —— 通用印刷体识别。

    申请地址：https://console.cloud.tencent.com/ocr
    需要 SecretId + SecretKey。
    免费额度：每月 1000 次（以官网最新政策为准）。

    腾讯云 v3 签名较复杂，这里手写实现 TC3-HMAC-SHA256，
    避免额外引入 SDK（能显著减小 APK 体积）。
    """

    name = "tencent"
    display_name = "腾讯云 OCR"

    HOST = "ocr.tencentcloudapi.com"
    SERVICE = "ocr"
    VERSION = "2018-11-19"
    ACTION = "GeneralAccurateOCR"
    REGION = "ap-guangzhou"

    def __init__(self, secret_id: str = "", secret_key: str = "", **kw):
        super().__init__(secret_id=secret_id, secret_key=secret_key, **kw)
        self.secret_id = (secret_id or "").strip()
        self.secret_key = (secret_key or "").strip()

    def health_check(self) -> Tuple[bool, str]:
        if not self.secret_id or not self.secret_key:
            return False, "未配置腾讯云 SecretId / SecretKey"
        return True, "腾讯云 OCR 凭据已填写"

    # ---- TC3-HMAC-SHA256 签名 ----

    def _sign(self, payload: str) -> dict:
        import hashlib
        import hmac
        from datetime import datetime, timezone

        timestamp = int(time.time())
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        date = dt.strftime("%Y-%m-%d")

        content_type = "application/json; charset=utf-8"
        canonical_headers = f"content-type:{content_type}\nhost:{self.HOST}\n"
        signed_headers = "content-type;host"
        hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        canonical_request = "\n".join([
            "POST", "/", "",
            canonical_headers, signed_headers, hashed_payload,
        ])

        credential_scope = f"{date}/{self.SERVICE}/tc3_request"
        hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        string_to_sign = "\n".join([
            "TC3-HMAC-SHA256", str(timestamp), credential_scope, hashed_canonical,
        ])

        def _hmac(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        secret_date = _hmac(f"TC3{self.secret_key}".encode("utf-8"), date)
        secret_service = _hmac(secret_date, self.SERVICE)
        secret_signing = _hmac(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        authorization = (
            f"TC3-HMAC-SHA256 Credential={self.secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return {
            "Authorization": authorization,
            "Content-Type": content_type,
            "Host": self.HOST,
            "X-TC-Action": self.ACTION,
            "X-TC-Version": self.VERSION,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Region": self.REGION,
        }

    def recognize(self, image_path: str) -> Tuple[List[str], dict]:
        b64 = self._read_image_b64(image_path)
        if len(b64) > 7 * 1024 * 1024:
            raise OcrError("图片过大（超过 7MB），请先压缩后再识别")

        payload = json.dumps({"ImageBase64": b64}, ensure_ascii=False)
        headers = self._sign(payload)
        data = self._post(
            f"https://{self.HOST}", data=payload.encode("utf-8"), headers=headers
        )

        resp = data.get("Response", {})
        if "Error" in resp:
            err = resp["Error"]
            code = err.get("Code", "")
            hint = {
                "AuthFailure.SignatureFailure": "签名错误，请检查 SecretKey",
                "AuthFailure.SecretIdNotFound": "SecretId 不存在，请检查配置",
                "FailedOperation.ImageDecodeFailed": "图片解码失败，请换一张图片",
                "FailedOperation.OcrFailed": "识别失败，请重试",
                "LimitExceeded": "调用量超限，请稍后重试",
            }.get(code, err.get("Message", ""))
            raise OcrError(f"腾讯云 OCR 错误 [{code}]：{hint}")

        texts = resp.get("TextDetections", [])
        # 置信度低于 60 的行直接丢弃，减少噪声干扰金额匹配
        words = [t.get("DetectedText", "") for t in texts if t.get("Confidence", 100) >= 60]
        return words, resp


class MockOcrEngine(BaseOcrEngine):
    """离线测试用引擎——不联网，返回固定文本。

    用途：
    1. 没配 API Key 时跑通整条链路
    2. 单元测试中作为稳定桩数据
    """

    name = "mock"
    display_name = "离线模拟（仅供测试）"

    #: 测试时可以覆盖这个属性来模拟不同场景
    fixture: List[str] = [
        "支付宝",
        "账单详情",
        "麦当劳（国贸店）",
        "-¥ 35.50",
        "2026-10-07 12:33:21",
        "付款方式 余额宝",
    ]

    def recognize(self, image_path: str) -> Tuple[List[str], dict]:
        if not image_path or not image_path.lower().endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        ):
            raise OcrError("不支持的图片格式")
        return list(self.fixture), {"mock": True, "lines": len(self.fixture)}


#: 引擎注册表。新增供应商在这里登记即可。
ENGINE_REGISTRY: Dict[str, type] = {
    BaiduOcrEngine.name: BaiduOcrEngine,
    TencentOcrEngine.name: TencentOcrEngine,
    MockOcrEngine.name: MockOcrEngine,
}


def create_engine(name: str, config: dict) -> BaseOcrEngine:
    """工厂方法。name 见 ENGINE_REGISTRY。"""
    if name not in ENGINE_REGISTRY:
        raise OcrError(f"未知的 OCR 引擎：{name}")
    return ENGINE_REGISTRY[name](**config)
