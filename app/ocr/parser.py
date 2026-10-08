"""票据解析器：把 OCR 出来的文本行，解析成结构化的 Transaction。

这是整个 APP 的核心难点。设计思路：

1. 先判断图片属于哪一类票据（支付宝截图 / 微信截图 / 银行短信截图 / 纸质小票）
2. 每类票据走各自的提取策略（金额怎么找、商户名怎么找）
3. 统一做金额清洗、日期规整、分类推断
4. 返回 ParseResult，附带置信度和警告，让用户二次确认

金额解析的关键坑：
- 千分位逗号：¥1,234.56
- 负数/减号：-35.50 / 支出 35.50
- 中文字符混入：金额前的 ¥ 常被识别成全角或半角
- 一张截图里可能有多个金额（余额、优惠、实付），要挑「实付」那个
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from ..core.models import (
    ParseResult,
    Transaction,
    SOURCE_RECEIPT,
    SOURCE_SCREENSHOT,
    TYPE_EXPENSE,
    TYPE_INCOME,
)

# ---------------------------------------------------------------- 正则表

#: 金额：可选币种符 + 千分位 + 小数。捕获组 1 是数字部分
_AMOUNT_RE = re.compile(
    r"[-−]?\s*[¥￥$]\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
)
#: 无币种符但有明确关键字引导的金额
_AMOUNT_KW_RE = re.compile(
    r"(?:实付|实收|付款|支付|消费|合计|总计|金额|应收|找零|优惠后)"
    r"\s*[:：]?\s*[¥￥]?\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
)
#: 纯数字金额（兜底，风险高，仅在无其他线索时用）
_BARE_AMOUNT_RE = re.compile(r"^\s*[¥￥]?\s*(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,6}\.\d{2})\s*$")

#: 日期时间。覆盖 2026-10-07 12:33:21 / 2026/10/07 / 10月07日 等
_DATETIME_PATTERNS = [
    (re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?"), "ymd_hms"),
    (re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?"), "ymd"),
    (re.compile(r"(\d{1,2})[-/月](\d{1,2})日?\s*(\d{1,2}):(\d{2})"), "md_hm"),
    (re.compile(r"(\d{1,2})月(\d{1,2})日"), "md"),
]

#: 需要排除的「非交易金额」上下文（余额、优惠、积分等）
_EXCLUDE_CONTEXT = [
    "余额", "优惠", "折扣", "立减", "积分", "可用", "额度", "红包", "返现",
    "减免", "满减", "券", "免单", "累计", "共计省", "节省",
]

#: 商户名提取的线索关键词
_MERCHANT_HINTS = ["收款方", "商户", "商家", "付款给", "转账给", "收款人", "店铺", "门店"]

#: 收入线索
_INCOME_HINTS = ["收款", "收入", "退款", "返款", "到账", "转入", "工资", "奖金", "报销", "红包收"]

#: 支出线索
_EXPENSE_HINTS = ["付款", "支出", "消费", "支付", "扣款", "转出", "购买"]

#: 分类关键词映射。命中即归入该分类。
_CATEGORY_RULES: List[Tuple[str, List[str]]] = [
    ("餐饮", ["餐", "饭", "食", "美团", "饿了么", "肯德基", "麦当劳", "星巴克", "瑞幸",
              "奶茶", "咖啡", "烧烤", "火锅", "面", "粥", "厨", "饮", "喜茶", "蜜雪"]),
    ("交通", ["地铁", "公交", "打车", "滴滴", "出租", "高铁", "火车", "机票", "航空",
              "加油", "停车", "车费", "出行", "共享单车", "哈啰", "青桔"]),
    ("购物", ["淘宝", "天猫", "京东", "拼多多", "超市", "便利店", "商场", "百货",
              "优衣库", "服装", "红旗", "沃尔玛", "永辉", "7-11", "全家", "罗森"]),
    ("居住", ["房租", "租金", "物业", "水费", "电费", "燃气", "暖气", "宽带", "家政"]),
    ("娱乐", ["电影", "影院", "KTV", "游戏", "视频会员", "腾讯视频", "爱奇艺",
              "哔哩", "音乐", "健身", "游泳", "旅游", "景点", "门票"]),
    ("医疗", ["医院", "药", "诊所", "体检", "挂号", "口腔", "眼科", "医疗"]),
    ("教育", ["书", "课程", "培训", "学费", "教育", "文具", "考试", "网校"]),
    ("通讯", ["话费", "流量", "中国移动", "联通", "电信", "通信"]),
]


# ---------------------------------------------------------------- 工具函数

def _to_cents(num_str: str) -> Optional[int]:
    """把金额字符串（可能含千分位逗号）转成分。"""
    try:
        cleaned = num_str.replace(",", "").replace(" ", "")
        from decimal import Decimal, ROUND_HALF_UP
        d = Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        cents = int(d * 100)
        # 过滤明显不合理的金额：大于 100 万元或小于 0
        if cents < 0 or cents > 100_000_000:
            return None
        return cents
    except Exception:
        return None


def _has_exclude_context(line: str) -> bool:
    return any(k in line for k in _EXCLUDE_CONTEXT)


def _extract_datetime(lines: List[str]) -> Optional[str]:
    """从文本行里找交易时间，返回 'YYYY-MM-DD HH:MM:SS'。"""
    now = datetime.now()
    for line in lines:
        for pattern, kind in _DATETIME_PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            try:
                g = m.groups()
                if kind == "ymd_hms":
                    dt = datetime(int(g[0]), int(g[1]), int(g[2]),
                                  int(g[3]), int(g[4]), int(g[5] or 0))
                elif kind == "ymd":
                    dt = datetime(int(g[0]), int(g[1]), int(g[2]))
                elif kind == "md_hm":
                    dt = datetime(now.year, int(g[0]), int(g[1]), int(g[2]), int(g[3]))
                    # 跨年修正：如果解析出的日期比现在晚很多，说明是去年的
                    if dt - now > timedelta(days=2):
                        dt = dt.replace(year=now.year - 1)
                elif kind == "md":
                    dt = datetime(now.year, int(g[0]), int(g[1]))
                    if dt - now > timedelta(days=2):
                        dt = dt.replace(year=now.year - 1)
                else:
                    continue
                # 合理性校验：不能是未来，也不早于 2000 年
                if dt > now + timedelta(days=1) or dt.year < 2000:
                    continue
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, IndexError):
                continue
    return None


def _extract_merchant(lines: List[str]) -> Tuple[str, float]:
    """提取商户名。返回 (名称, 该结果的置信度)。"""
    # 策略 1：带明确线索词的行，如「收款方：麦当劳」
    for i, line in enumerate(lines):
        for hint in _MERCHANT_HINTS:
            if hint in line:
                after = line.split(hint, 1)[1].lstrip("：: 　")
                if after and len(after) <= 30:
                    return after.strip(), 0.9
                # 线索词独占一行时，取下一行
                if not after and i + 1 < len(lines):
                    nxt = lines[i + 1].strip()
                    if nxt and len(nxt) <= 30:
                        return nxt, 0.85

    # 策略 2：靠「公司/店/行/超市」等后缀识别
    suffix_re = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9（）()·\-]{2,28}"
                           r"(?:有限公司|有限责任公司|店|超市|商场|餐厅|酒店|"
                           r"快餐|药房|医院|银行|营业部|分店|门店)$")
    for line in lines:
        s = line.strip()
        if suffix_re.match(s):
            return s, 0.75

    # 策略 3：跳过平台名/表头，取第一个像名字的中文短行
    skip = {"支付宝", "微信", "微信支付", "账单", "账单详情", "交易详情", "详情",
            "收付款", "余额宝", "花呗", "银行卡", "支付成功", "交易成功", "订单"}
    for line in lines:
        s = line.strip()
        if s in skip or not s:
            continue
        if _has_exclude_context(s):
            continue
        if _AMOUNT_RE.search(s) or _BARE_AMOUNT_RE.match(s):
            continue
        if re.search(r"\d{2}:\d{2}", s):     # 时间行
            continue
        # 2~20 个字符、以中文为主
        if 2 <= len(s) <= 20 and len(re.findall(r"[\u4e00-\u9fa5]", s)) >= 2:
            return s, 0.55

    return "", 0.0


def _detect_type(lines: List[str], amount_lines: List[str]) -> str:
    """判断是收入还是支出。

    判定优先级（高 -> 低）：
      1. 金额符号：带负号/减号 => 支出；带加号 => 收入
      2. 强收入词（收款/到账/退款/转入）出现且无强支出词 => 收入
      3. 支出词多于收入词 => 支出
      4. 默认支出
    注意「收款」和「付款」只差一个字，必须优先看强信号，不能只数字数。
    """
    joined = " ".join(lines)

    # --- 1. 金额符号是最强信号 ---
    if any(re.search(r"[-−]\s*[¥￥]?\s*\d", ln) for ln in amount_lines):
        return TYPE_EXPENSE
    if any(re.search(r"\+\s*[¥￥]?\s*\d", ln) for ln in amount_lines):
        return TYPE_INCOME

    # --- 2. 强收入词 ---
    strong_income = ["收款", "到账", "退款", "转入", "收入", "已收",
                     "工资", "奖金", "报销", "红包"]
    strong_expense = ["付款", "支付", "消费", "扣款", "转出", "支出", "购买"]

    has_income = any(k in joined for k in strong_income)
    has_expense = any(k in joined for k in strong_expense)

    # 平台名/渠道名里嵌了「支付」「付款」等词（如「微信支付」「支付宝」），
    # 它们描述的是 App 本身而不是交易行为，必须先剔除，否则会误判类型。
    sanitized = joined
    for plat in ("微信支付", "支付宝", "云闪付", "银联", "付款方式",
                 "支付方式", "收付款", "微信", "支付平台"):
        sanitized = sanitized.replace(plat, "")

    has_income = any(k in sanitized for k in strong_income)
    has_expense = any(k in sanitized for k in strong_expense)

    if has_income and not has_expense:
        return TYPE_INCOME

    # --- 3. 计词数 ---
    income_score = sum(1 for k in _INCOME_HINTS if k in sanitized)
    expense_score = sum(1 for k in _EXPENSE_HINTS if k in sanitized)
    if income_score > expense_score:
        return TYPE_INCOME

    return TYPE_EXPENSE


def _guess_category(text: str) -> str:
    """基于关键词推断消费分类。"""
    for category, keywords in _CATEGORY_RULES:
        for kw in keywords:
            if kw in text:
                return category
    return "其他"


def _extract_amount(lines: List[str]) -> Tuple[Optional[int], float, List[str]]:
    """提取金额。返回 (金额分, 置信度, 警告列表)。

    优先级：
      1. 关键字引导的金额（实付/付款/合计）—— 最可信
      2. 带币种符的金额，排除余额/优惠等上下文
      3. 纯数字行兜底
    同一优先级内取最大值（账单截图里主金额通常最显眼）。
    """
    warnings: List[str] = []
    candidates: List[Tuple[int, float]] = []

    # --- 优先级 1：关键字引导 ---
    for line in lines:
        if _has_exclude_context(line) and not any(
            k in line for k in ("实付", "付款", "支付", "合计", "总计")
        ):
            continue
        for m in _AMOUNT_KW_RE.finditer(line):
            cents = _to_cents(m.group(1))
            if cents is not None:
                candidates.append((cents, 0.92))

    if candidates:
        best = max(candidates, key=lambda x: x[0])
        return best[0], best[1], warnings

    # --- 优先级 2：带币种符 ---
    for line in lines:
        if _has_exclude_context(line):
            continue
        for m in _AMOUNT_RE.finditer(line):
            cents = _to_cents(m.group(1))
            if cents is not None and cents > 0:
                candidates.append((cents, 0.8))

    if candidates:
        best = max(candidates, key=lambda x: x[0])
        if len(candidates) > 1:
            warnings.append(
                f"图片中有 {len(candidates)} 个金额候选，已选最大的一项，请核对"
            )
        return best[0], best[1], warnings

    # --- 优先级 3：纯数字行兜底 ---
    for line in lines:
        if _has_exclude_context(line):
            continue
        m = _BARE_AMOUNT_RE.match(line)
        if m:
            cents = _to_cents(m.group(1))
            if cents is not None and cents > 0:
                candidates.append((cents, 0.5))

    if candidates:
        best = max(candidates, key=lambda x: x[0])
        warnings.append("未能定位明确的金额字段，结果可能不准确，请务必核对")
        return best[0], best[1], warnings

    return None, 0.0, warnings


# ---------------------------------------------------------------- 主入口

def parse_receipt_text(
    lines: List[str],
    *,
    source: str = SOURCE_SCREENSHOT,
    image_path: str = "",
) -> ParseResult:
    """把 OCR 文本行解析成 Transaction。

    参数：
        lines       —— OCR 返回的文本行
        source      —— 记录来源（截图 or 小票）
        image_path  —— 原始图片路径，会存进账目便于回溯
    """
    lines = [ln.strip() for ln in (lines or []) if ln and ln.strip()]
    if not lines:
        return ParseResult(error="图片中没有识别到任何文字，请换一张更清晰的图片")

    warnings: List[str] = []
    raw_text = "\n".join(lines)

    amount, amount_conf, amount_warnings = _extract_amount(lines)
    warnings.extend(amount_warnings)

    if amount is None:
        return ParseResult(
            raw_text=raw_text,
            error="未能从图片中识别出金额，请手动补充或换一张截图",
            warnings=warnings,
        )

    merchant, merchant_conf = _extract_merchant(lines)
    occurred_at = _extract_datetime(lines)
    if not occurred_at:
        occurred_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        warnings.append("未识别到交易时间，已使用当前时间")

    tx_type = _detect_type(lines, [ln for ln in lines if _AMOUNT_RE.search(ln)])
    category = _guess_category(raw_text + " " + merchant)

    if not merchant:
        merchant = "未识别商户"
        warnings.append("未识别到商户名，可手动补充")

    # 综合置信度：金额权重最高
    confidence = round(amount_conf * 0.6 + merchant_conf * 0.4, 2)
    if amount_conf < 0.6:
        warnings.append("金额识别置信度较低，请重点核对")

    tx = Transaction(
        amount=amount,
        type=tx_type,
        category=category,
        merchant=merchant,
        note="",
        occurred_at=occurred_at,
        source=source,
        image_path=image_path,
        ocr_confidence=confidence,
    )
    return ParseResult(
        parsed=tx, raw_text=raw_text, confidence=confidence, warnings=warnings
    )


def parse_receipt_image(
    image_path: str,
    engine,
    *,
    source: str = SOURCE_SCREENSHOT,
) -> ParseResult:
    """端到端：图片 -> OCR -> 解析。engine 是 BaseOcrEngine 实例。"""
    from .engines import OcrError

    try:
        lines, _raw = engine.recognize(image_path)
    except OcrError as e:
        return ParseResult(error=str(e))
    except Exception as e:  # 兜底，防止 APP 崩溃
        return ParseResult(error=f"图片识别异常：{e}")

    if source == SOURCE_RECEIPT:
        return parse_receipt_text(lines, source=SOURCE_RECEIPT, image_path=image_path)
    return parse_receipt_text(lines, source=SOURCE_SCREENSHOT, image_path=image_path)
