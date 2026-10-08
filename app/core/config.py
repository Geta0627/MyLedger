"""配置管理。

API Key 等敏感信息存储在应用私有目录的 settings.json，
安卓上该目录其他 APP 无法访问。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    "ocr_engine": "baidu",          # baidu / tencent / mock
    "baidu_api_key": "",
    "baidu_secret_key": "",
    "tencent_secret_id": "",
    "tencent_secret_key": "",
    "auto_save_threshold": 0.85,    # 置信度高于此值时可选「静默保存」
    "currency_symbol": "¥",
    "compress_before_upload": True, # 上传前压缩，省流量
    "max_image_side": 1600,         # 压缩后最长边像素
}


class Settings:
    """读写 settings.json。"""

    def __init__(self, path: str):
        self.path = path
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> Dict[str, Any]:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    self._data.update(saved)
            except (json.JSONDecodeError, OSError):
                # 配置文件损坏时不阻断启动，用默认值
                pass
        return self._data

    def save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        # 先写临时文件再替换，避免写一半断电导致配置损坏
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, mapping: dict) -> None:
        self._data.update(mapping)

    def engine_config(self, engine_name: str = None) -> dict:
        """按引擎名取出对应的凭据配置。"""
        name = engine_name or self.get("ocr_engine")
        if name == "baidu":
            return {
                "api_key": self.get("baidu_api_key"),
                "secret_key": self.get("baidu_secret_key"),
            }
        if name == "tencent":
            return {
                "secret_id": self.get("tencent_secret_id"),
                "secret_key": self.get("tencent_secret_key"),
            }
        return {}

    def has_credentials(self) -> bool:
        """是否已配置至少一个可用引擎的凭据。"""
        name = self.get("ocr_engine")
        if name == "mock":
            return True
        cfg = self.engine_config(name)
        return all(bool(v) for v in cfg.values()) and bool(cfg)
