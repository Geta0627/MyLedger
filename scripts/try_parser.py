#!/usr/bin/env python3
"""OCR 解析器快速验证工具 —— 不用手机、不用 API Key，直接在命令行试识别效果。

用途：调试解析规则时快速验证，或拿真实截图确认能不能识别对。

用法：
  # 用内置样例跑一遍
  python scripts/try_parser.py --demo

  # 用真实图片 + 百度 API Key
  python scripts/try_parser.py 截图.png --engine baidu \
      --api-key xxx --secret-key yyy

  # 直接喂文本（把 OCR 结果复制进来）
  python scripts/try_parser.py --text "合计：￥83.40"
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.models import cents_to_yuan  # noqa: E402
from app.ocr.parser import parse_receipt_text, parse_receipt_image  # noqa: E402
from app.ocr.engines import create_engine  # noqa: E402

DEMOS = [
    ("支付宝付款截图", [
        "支付宝", "账单详情", "麦当劳（国贸店）",
        "-¥ 35.50", "2026-10-07 12:33:21", "付款方式 余额宝",
    ]),
    ("微信收款截图", [
        "微信支付", "账单详情", "收款方 张三", "收款 ￥200.00",
        "2026-10-06 09:15:00", "微信红包",
    ]),
    ("超市纸质小票", [
        "华联超市", "欢迎光临", "鸡蛋   12.80", "牛奶   25.60",
        "洗发水 45.00", "合计：￥83.40", "2026/10/05 18:22", "谢谢惠顾",
    ]),
    ("银行消费提醒", [
        "招商银行", "交易提醒", "消费 ￥1,234.56",
        "可用余额 ￥88,000.00", "2026-10-04 20:01:33",
    ]),
    ("滴滴打车", [
        "支付宝", "滴滴出行", "-¥ 28.60", "2026-10-07 08:12:00",
    ]),
    ("无金额风景照", ["山顶风景", "拍摄于黄山"]),
]


def show(name: str, lines):
    print(f"\n{'=' * 62}")
    print(f"  {name}")
    print("=" * 62)
    print("  OCR 文本：")
    for ln in lines:
        print(f"    | {ln}")
    print("  " + "-" * 58)

    r = parse_receipt_text(lines)
    if r.ok:
        t = r.parsed
        type_label = "支出" if t.is_expense else "收入"
        print(f"  金额  : ¥{cents_to_yuan(t.amount)}")
        print(f"  类型  : {type_label}")
        print(f"  分类  : {t.category}")
        print(f"  商户  : {t.merchant}")
        print(f"  时间  : {t.occurred_at}")
        print(f"  置信度: {r.confidence}")
    else:
        print(f"  ✗ 识别失败: {r.error}")
    for w in r.warnings:
        print(f"  ⚠ {w}")


def main():
    ap = argparse.ArgumentParser(description="OCR 解析器验证工具")
    ap.add_argument("image", nargs="?", help="图片路径")
    ap.add_argument("--demo", action="store_true", help="跑内置样例")
    ap.add_argument("--text", help="直接传入文本（用 \\n 分隔行）")
    ap.add_argument("--engine", default="mock", choices=["mock", "baidu", "tencent"])
    ap.add_argument("--api-key", default="")
    ap.add_argument("--secret-key", default="")
    ap.add_argument("--secret-id", default="")
    ap.add_argument("--kind", default="screenshot",
                    choices=["screenshot", "receipt"])
    args = ap.parse_args()

    if args.demo:
        for name, lines in DEMOS:
            show(name, lines)
        print(f"\n{'=' * 62}")
        print("  全部样例跑完")
        print("=" * 62)
        return 0

    if args.text:
        lines = [l for l in args.text.replace("\\n", "\n").split("\n") if l.strip()]
        show("手动输入文本", lines)
        return 0

    if not args.image:
        ap.print_help()
        return 1

    if not os.path.exists(args.image):
        print(f"✗ 文件不存在：{args.image}")
        return 1

    cfg = {}
    if args.engine == "baidu":
        cfg = {"api_key": args.api_key, "secret_key": args.secret_key}
        if not all(cfg.values()):
            print("✗ 百度引擎需要 --api-key 和 --secret-key")
            return 1
    elif args.engine == "tencent":
        cfg = {"secret_id": args.secret_id, "secret_key": args.secret_key}
        if not all(cfg.values()):
            print("✗ 腾讯引擎需要 --secret-id 和 --secret-key")
            return 1

    print(f"→ 使用 {args.engine} 引擎识别 {args.image} …")
    engine = create_engine(args.engine, cfg)
    r = parse_receipt_image(args.image, engine, source=args.kind)
    show(os.path.basename(args.image), r.raw_text.split("\n") if r.raw_text else [])

    if not r.raw_text and r.error:
        print(f"\n  ✗ {r.error}")
    return 0 if r.ok else 2


if __name__ == "__main__":
    sys.exit(main())
