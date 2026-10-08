"""UI 交互流程测试（需要图形环境）。

在无显示器环境下用 xvfb 运行：
    xvfb-run -a python tests/test_ui_flow.py

CI 中若不装图形库，此测试会自动跳过，不影响主测试套件。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail if not cond else ''}")


def main():
    # 无图形环境时跳过，不判定为失败
    if not os.environ.get("DISPLAY") and not os.environ.get("KIVY_WINDOW"):
        print("跳过：未检测到图形环境（设置 DISPLAY 或用 xvfb-run 运行）")
        return 0

    try:
        from kivy.config import Config
        Config.set("graphics", "width", "400")
        Config.set("graphics", "height", "760")
        from app.ui.main import LedgerApp
        from app.core.models import ParseResult, Transaction
    except Exception as e:
        print(f"跳过：Kivy 不可用（{e}）")
        return 0

    tmp = tempfile.mkdtemp(prefix="ui_flow_")
    app = LedgerApp()
    app.get_data_dir = lambda: tmp
    toasts = []
    app.toast = lambda m: toasts.append(m)

    print("=" * 60)
    print("UI 交互流程测试")
    print("=" * 60)

    print("\n[构建]")
    root = app.build()
    app.root = root                      # 模拟 Kivy 运行时行为
    names = sorted(s.name for s in root.screens)
    check("6 个屏幕全部注册",
          names == ["add", "confirm", "home", "list", "settings", "stats"], str(names))

    # ---------- 各页面渲染 ----------
    print("\n[页面渲染]")
    for name in ["home", "add", "list", "stats", "settings"]:
        sc = root.get_screen(name)
        try:
            if hasattr(sc, "on_pre_enter"):
                sc.on_pre_enter()
            check(f"{name} 页渲染", True)
        except Exception as e:
            check(f"{name} 页渲染", False, f"{type(e).__name__}: {e}")

    # ---------- 控件 id 完整性 ----------
    print("\n[控件 id 完整性]")
    required = {
        "home": ["month_label", "expense_label", "income_label", "balance_label",
                 "count_label", "recent_list"],
        "add": ["amount_input", "merchant_input", "note_input", "date_input",
                "time_input", "type_spinner", "category_spinner"],
        "list": ["search_input", "tx_list", "summary_label",
                 "filt_all", "filt_exp", "filt_inc"],
        "stats": ["range_title", "big_expense", "big_income", "big_balance",
                  "stats_sub", "cat_list", "rng_week", "rng_month", "rng_year"],
        "settings": ["engine_spinner", "baidu_key", "baidu_secret",
                     "tc_id", "tc_key", "status_label"],
        "confirm": ["amount_input", "merchant_input", "date_input", "time_input",
                    "type_spinner", "category_spinner", "source_spinner",
                    "note_input", "raw_text", "conf_label", "warn_label", "save_btn"],
    }
    for sname, ids in required.items():
        sc = root.get_screen(sname)
        missing = [i for i in ids if i not in sc.ids]
        check(f"{sname} 页 {len(ids)} 个 id", not missing, f"缺失 {missing}")

    # ---------- 流程 1：手动记账 ----------
    print("\n[流程1] 手动记账")
    add = root.get_screen("add")
    add.on_pre_enter()
    add.ids.amount_input.text = "88.50"
    add.ids.merchant_input.text = "测试餐厅"
    add.ids.category_spinner.text = "餐饮"
    add.save()
    check("手动记账入库", app.service.db.count() == 1, f"实际 {app.service.db.count()}")
    t = app.service.list_transactions(keyword="测试餐厅")[0]
    check("金额正确 ¥88.50", t.amount == 8850, f"实际 {t.amount}")
    check("来源标记 manual", t.source == "manual", t.source)

    # ---------- 流程 2：识别 -> 确认 -> 保存 ----------
    print("\n[流程2] 识别结果确认后保存")
    conf = root.get_screen("confirm")
    conf._editing = False
    conf.load_result(ParseResult(
        parsed=Transaction(amount=8340, category="购物", merchant="华联超市",
                           occurred_at="2026-10-05 18:22:00"),
        raw_text="华联超市\n合计：￥83.40", confidence=0.85))
    check("表单自动填充金额", conf.ids.amount_input.text == "83.40",
          conf.ids.amount_input.text)
    check("表单自动填充商户", conf.ids.merchant_input.text == "华联超市")
    check("表单自动填充日期", conf.ids.date_input.text == "2026-10-05")
    check("表单自动填充分类", conf.ids.category_spinner.text == "购物")

    # 用户纠正 OCR 结果
    conf.ids.amount_input.text = "84.00"
    conf.save()
    check("确认后入库", app.service.db.count() == 2, f"实际 {app.service.db.count()}")
    t2 = [x for x in app.service.list_transactions() if x.merchant == "华联超市"]
    check("用户修改后的金额生效", t2 and t2[0].amount == 8400,
          f"实际 {t2[0].amount if t2 else 'N/A'}")
    check("来源标记 screenshot", t2 and t2[0].source == "screenshot")

    # ---------- 流程 3：编辑 ----------
    print("\n[流程3] 编辑已有账目")
    old = app.service.list_transactions(keyword="测试餐厅")[0]
    app.open_edit(old)
    conf.ids.amount_input.text = "99.00"
    conf.save()
    check("编辑后金额更新", app.service.get_transaction(old.id).amount == 9900)
    check("编辑不产生新记录", app.service.db.count() == 2, f"实际 {app.service.db.count()}")

    # ---------- 流程 4：输入校验 ----------
    print("\n[流程4] 非法输入校验")
    add.on_pre_enter()
    toasts.clear()
    add.ids.amount_input.text = ""
    add.save()
    check("空金额被拦截", app.service.db.count() == 2 and toasts, str(toasts))

    toasts.clear()
    add.ids.amount_input.text = "0"
    add.save()
    check("零金额被拦截", app.service.db.count() == 2 and toasts, str(toasts))

    add.ids.amount_input.text = "12.999"
    add.ids.merchant_input.text = "四舍五入"
    add.save()
    t3 = app.service.list_transactions(keyword="四舍五入")
    check("12.999 进位为 13.00", t3 and t3[0].amount == 1300,
          f"实际 {t3[0].amount_yuan if t3 else 'N/A'}")

    # ---------- 流程 5：删除 ----------
    print("\n[流程5] 删除账目")
    before = app.service.db.count()
    app.service.delete_transaction(t3[0].id)
    check("删除生效", app.service.db.count() == before - 1)

    # ---------- 流程 6：统计与筛选 ----------
    print("\n[流程6] 统计与筛选")
    st = root.get_screen("stats")
    st.set_range("month")
    check("统计页支出金额非空", bool(st.ids.big_expense.text), st.ids.big_expense.text)
    check("统计页收入金额非空", bool(st.ids.big_income.text), st.ids.big_income.text)

    ls = root.get_screen("list")
    ls.set_filter("expense")
    check("支出筛选生效", "支出" in ls.ids.summary_label.text, ls.ids.summary_label.text)
    ls.ids.search_input.text = "华联"
    check("搜索结果正确", "共 1 笔" in ls.ids.summary_label.text,
          ls.ids.summary_label.text)

    app.service.close()
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
