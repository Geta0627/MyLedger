"""数据模型定义。

所有金额一律以「分」为单位存整数，避免浮点数精度误差。
展示时再除以 100 转成元。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

# 账目类型：支出 / 收入
TYPE_EXPENSE = "expense"
TYPE_INCOME = "income"

# 记录来源：手动录入 / 截图识别 / 小票拍照
SOURCE_MANUAL = "manual"
SOURCE_SCREENSHOT = "screenshot"
SOURCE_RECEIPT = "receipt"

CATEGORIES_EXPENSE = ["餐饮", "交通", "购物", "居住", "娱乐", "医疗", "教育", "通讯", "其他"]
CATEGORIES_INCOME = ["工资", "奖金", "兼职", "投资", "红包", "报销", "其他"]


def now_iso() -> str:
    """当前本地时间，精确到秒。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def yuan_to_cents(value) -> int:
    """元 -> 分。用 Decimal 规避 float 精度问题。

    容忍用户粘贴进来的各种格式：
    '1,234.56' / '¥ 35.50' / '35。5' / ' 100 ' / '−3.5'
    """
    from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

    if isinstance(value, (int, float, Decimal)):
        cleaned = str(value)
    else:
        cleaned = str(value)

    # 全角符号 -> 半角，去掉币种符、千分位逗号、空格
    cleaned = (
        cleaned.replace("，", ",")
        .replace("。", ".")
        .replace("−", "-")      # U+2212 数学减号
        .replace("－", "-")     # 全角减号
        .replace("¥", "").replace("￥", "").replace("$", "")
        .replace(",", "")
        .replace(" ", "").replace("\u3000", "")
    )
    if not cleaned:
        raise ValueError("金额为空")

    try:
        d = Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as e:
        raise ValueError(f"无法解析的金额：{value}") from e
    return int(d * 100)


def cents_to_yuan(cents: int) -> str:
    """分 -> 元，返回两位小数字符串。"""
    if cents < 0:
        return "-" + cents_to_yuan(-cents)
    return f"{cents // 100}.{cents % 100:02d}"


@dataclass
class Transaction:
    """一条账目。"""

    amount: int                      # 金额，单位「分」，恒为正数
    type: str = TYPE_EXPENSE         # expense / income
    category: str = "其他"
    merchant: str = ""               # 商户名
    note: str = ""                   # 备注
    occurred_at: str = field(default_factory=now_iso)   # 交易发生时间
    source: str = SOURCE_MANUAL      # 来源：手动/截图/小票
    image_path: str = ""             # 关联的原始图片路径
    ocr_confidence: float = 1.0      # OCR 置信度，手动录入恒为 1.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=now_iso)

    @property
    def amount_yuan(self) -> str:
        return cents_to_yuan(self.amount)

    @property
    def is_expense(self) -> bool:
        return self.type == TYPE_EXPENSE

    @property
    def signed_amount(self) -> int:
        """带符号金额：支出为负，收入为正。用于汇总。"""
        return -self.amount if self.is_expense else self.amount

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Transaction":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class ParseResult:
    """OCR 解析结果，交给 UI 层做二次确认。

    parsed 为 None 表示识别失败，需要看 error 字段。
    """

    parsed: Optional[Transaction] = None
    raw_text: str = ""
    confidence: float = 0.0
    error: str = ""
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.parsed is not None and not self.error
