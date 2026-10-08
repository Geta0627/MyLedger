"""核心逻辑测试。

不依赖 Kivy，可在任何环境跑（CI 里也会执行）。
运行：python tests/run_tests.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.models import (  # noqa: E402
    Transaction,
    cents_to_yuan,
    yuan_to_cents,
    TYPE_EXPENSE,
    TYPE_INCOME,
)
from app.core.service import LedgerService, month_range, week_range  # noqa: E402
from app.ocr.parser import parse_receipt_text  # noqa: E402
from app.ocr.engines import create_engine, MockOcrEngine, ENGINE_REGISTRY  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


# ------------------------------------------------------------ 1. 金额换算

def test_money():
    print("\n[1] 金额换算（分 <-> 元）")
    check("35.50 -> 3550", yuan_to_cents("35.50") == 3550)
    check("0.1 + 0.2 无浮点误差", yuan_to_cents("0.1") + yuan_to_cents("0.2") == yuan_to_cents("0.3"))
    check("100 -> 10000", yuan_to_cents("100") == 10000)
    check("3 位小数四舍五入", yuan_to_cents("1.005") == 101)
    check("输出补零 35.5 -> '35.50'", cents_to_yuan(3550) == "35.50")
    check("大额千分位不参与", cents_to_yuan(123456) == "1234.56")
    check("负数保留符号", cents_to_yuan(-350) == "-3.50")
    check("带逗号输入", yuan_to_cents("1,234.56") == 123456)


# ------------------------------------------------------------ 2. 解析器

def test_parser():
    print("\n[2] 票据解析器")

    r = parse_receipt_text([
        "支付宝", "账单详情", "麦当劳（国贸店）",
        "-¥ 35.50", "2026-10-07 12:33:21", "付款方式 余额宝",
    ])
    check("支付宝截图-识别成功", r.ok)
    check("支付宝截图-金额 35.50", r.ok and r.parsed.amount == 3550)
    check("支付宝截图-支出", r.ok and r.parsed.type == TYPE_EXPENSE)
    check("支付宝截图-分类餐饮", r.ok and r.parsed.category == "餐饮")
    check("支付宝截图-商户", r.ok and "麦当劳" in r.parsed.merchant)
    check("支付宝截图-时间", r.ok and r.parsed.occurred_at.startswith("2026-10-07"))

    # 平台名含「支付」二字，不能因此误判为支出
    r = parse_receipt_text([
        "微信支付", "账单详情", "收款方 张三", "收款 ￥200.00",
        "2026-10-06 09:15:00", "微信红包",
    ])
    check("微信收款-判定为收入", r.ok and r.parsed.type == TYPE_INCOME,
          f"实际={r.parsed.type if r.ok else 'FAIL'}")
    check("微信收款-金额 200", r.ok and r.parsed.amount == 20000)

    # 余额干扰
    r = parse_receipt_text([
        "招商银行", "交易提醒", "消费 ￥1,234.56",
        "可用余额 ￥88,000.00", "2026-10-04 20:01:33",
    ])
    check("千分位金额解析", r.ok and r.parsed.amount == 123456,
          f"实际={r.parsed.amount if r.ok else 'FAIL'}")
    check("余额未被误取", r.ok and r.parsed.amount != 8800000)

    # 纸质小票：多商品取合计
    r = parse_receipt_text([
        "华联超市", "鸡蛋 12.80", "牛奶 25.60", "洗发水 45.00",
        "合计：￥83.40", "2026/10/05 18:22",
    ])
    check("小票-取合计而非单项", r.ok and r.parsed.amount == 8340,
          f"实际={r.parsed.amount if r.ok else 'FAIL'}")

    # 识别失败要优雅返回
    r = parse_receipt_text(["风景照", "没有金额"])
    check("无金额-返回失败而非异常", not r.ok and bool(r.error))

    r = parse_receipt_text([])
    check("空输入-返回失败", not r.ok)

    # 分类推断
    for text, expect in (
        (["滴滴出行", "-¥28.60"], "交通"),
        (["星巴克", "-¥35.00"], "餐饮"),
        (["京东商城", "-¥299.00"], "购物"),
        (["房租", "-¥3000.00"], "居住"),
    ):
        r = parse_receipt_text(text)
        check(f"分类推断 {text[0]} -> {expect}",
              r.ok and r.parsed.category == expect,
              f"实际={r.parsed.category if r.ok else 'FAIL'}")

    # 日期格式兼容
    r = parse_receipt_text(["测试商户", "合计 ￥50.00", "2026/09/28"])
    check("斜杠日期格式", r.ok and r.parsed.occurred_at.startswith("2026-09-28"))


# ------------------------------------------------------------ 3. 数据库

def test_db(tmpdir):
    print("\n[3] 数据库 CRUD 与统计")
    svc = LedgerService(tmpdir)

    t1 = svc.add_transaction(Transaction(amount=3500, category="餐饮", merchant="星巴克",
                                         occurred_at="2026-10-05 12:00:00"))
    svc.add_transaction(Transaction(amount=1200000, type=TYPE_INCOME, category="工资",
                                    merchant="公司", occurred_at="2026-10-01 09:00:00"))
    svc.add_transaction(Transaction(amount=5800, category="餐饮", merchant="肯德基",
                                    occurred_at="2026-10-07 19:00:00"))

    check("新增后可查", svc.get_transaction(t1.id) is not None)
    check("总数 3", svc.db.count() == 3, f"实际={svc.db.count()}")

    t1.amount = 4000
    check("更新生效", svc.update_transaction(t1) and svc.get_transaction(t1.id).amount == 4000)

    s, e = month_range(2026, 10)
    inc, exp = svc.db.summary(s, e)
    check("收入汇总", inc == 1200000, f"实际={inc}")
    check("支出汇总", exp == 4000 + 5800, f"实际={exp}")

    st = svc.stats(s, e)
    check("统计-笔数", st["count"] == 3, f"实际={st['count']}")
    check("统计-结余", st["balance"] == 1200000 - 9800)
    check("统计-分类数", len(st["categories"]) == 1)
    check("统计-百分比合计约100", abs(sum(c["percent"] for c in st["categories"]) - 100) < 0.5)

    # 筛选与搜索
    check("按类型筛选", len(svc.list_transactions(tx_type=TYPE_INCOME)) == 1)
    check("关键词搜索", len(svc.list_transactions(keyword="星巴克")) == 1)
    check("日期区间筛选", len(svc.list_transactions(start="2026-10-06", end="2026-10-08")) == 1)

    # 导出
    csv_path = os.path.join(tmpdir, "t.csv")
    svc.export_csv(csv_path)
    content = open(csv_path, encoding="utf-8-sig").read()
    check("CSV 有表头", "金额(元)" in content)
    check("CSV 行数正确", len(content.strip().splitlines()) == 4,
          f"实际={len(content.strip().splitlines())}")

    # 删除
    check("删除成功", svc.delete_transaction(t1.id))
    check("删除后总数 2", svc.db.count() == 2)
    svc.close()


# ------------------------------------------------------------ 4. 引擎

def test_engine(tmpdir):
    print("\n[4] OCR 引擎")
    check("注册表含 3 个引擎", len(ENGINE_REGISTRY) == 3)
    check("工厂可创建 mock", isinstance(create_engine("mock", {}), MockOcrEngine))
    check("未知引擎抛错", _raises(lambda: create_engine("nope", {})))

    # Mock 引擎端到端
    svc = LedgerService(os.path.join(tmpdir, "eng"))
    svc.settings.set("ocr_engine", "mock")
    svc.settings.save()

    img = os.path.join(tmpdir, "eng", "x.png")
    os.makedirs(os.path.dirname(img), exist_ok=True)
    open(img, "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 200)

    MockOcrEngine.fixture = ["肯德基", "-¥ 58.00", "2026-10-07 19:20:00"]
    r = svc.recognize_image(img)
    check("mock 引擎端到端识别", r.ok, r.error)
    check("mock 识别金额", r.ok and r.parsed.amount == 5800)

    # 坏图片不应崩溃
    bad = os.path.join(tmpdir, "eng", "bad.txt")
    open(bad, "w").write("not an image")
    r = svc.recognize_image(bad)
    check("坏文件返回错误而非崩溃", not r.ok and bool(r.error))
    svc.close()


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


# ------------------------------------------------------------ 5. 配置

def test_config(tmpdir):
    print("\n[5] 配置管理")
    svc = LedgerService(os.path.join(tmpdir, "cfg"))
    s = svc.settings

    check("默认引擎百度", s.get("ocr_engine") == "baidu")
    check("无凭据时 has_credentials=False", not s.has_credentials())

    s.set("baidu_api_key", "k")
    s.set("baidu_secret_key", "s")
    check("填齐后 has_credentials=True", s.has_credentials())

    s.save()
    check("配置可持久化", os.path.exists(os.path.join(tmpdir, "cfg", "settings.json")))

    # 损坏的配置不应导致崩溃
    with open(os.path.join(tmpdir, "cfg", "settings.json"), "w") as f:
        f.write("{坏掉的JSON")
    s.load()
    check("配置损坏时回落默认值", s.get("ocr_engine") == "baidu")

    # 切换引擎配置读取正确
    s.set("ocr_engine", "tencent")
    s.set("tencent_secret_id", "id1")
    s.set("tencent_secret_key", "key1")
    cfg = s.engine_config()
    check("腾讯配置字段正确", cfg == {"secret_id": "id1", "secret_key": "key1"})
    svc.close()


# ------------------------------------------------------------ 6. 时间区间

def test_ranges():
    print("\n[6] 时间区间")
    s, e = month_range(2026, 2)
    check("2月区间正确", s == "2026-02-01" and e == "2026-02-28",
          f"{s}~{e}")
    s, e = month_range(2026, 12)
    check("12月跨年处理", e.startswith("2026-12-31"), e)
    s, e = week_range()
    from datetime import datetime
    check("周起点是周一", datetime.strptime(s, "%Y-%m-%d").weekday() == 0)


# ------------------------------------------------------------ main

def main():
    print("=" * 60)
    print("轻记账 —— 核心逻辑测试")
    print("=" * 60)
    tmpdir = tempfile.mkdtemp(prefix="ledger_test_")
    try:
        test_money()
        test_parser()
        test_db(os.path.join(tmpdir, "db"))
        test_engine(tmpdir)
        test_config(tmpdir)
        test_ranges()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("\n失败详情：")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
