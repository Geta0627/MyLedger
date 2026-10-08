"""Kivy UI 层。

界面结构（用 ScreenManager 管理）：
  home        首页 —— 本月概览 + 最近账目 + 醒目「拍图记账」按钮
  confirm     识别结果确认页 —— 识别后必过此页，用户可改
  add         手动记账页
  list        全部账目 + 筛选
  stats       统计图表页
  settings    设置页 —— OCR 引擎与 Key 配置

设计原则：识别结果一律经过确认页，绝不静默入库。
OCR 一定会有错，让用户一眼看到并改掉，比事后查账强。
"""
from __future__ import annotations

import os
import threading

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen, ScreenManager, SlideTransition
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from ..core.models import (
    CATEGORIES_EXPENSE,
    CATEGORIES_INCOME,
    SOURCE_MANUAL,
    SOURCE_RECEIPT,
    SOURCE_SCREENSHOT,
    TYPE_EXPENSE,
    TYPE_INCOME,
    Transaction,
    cents_to_yuan,
    yuan_to_cents,
)
from ..core.service import LedgerService, month_range, today_str, week_range

# 主题色
C_PRIMARY = (0.16, 0.50, 0.94, 1)      # 主蓝
C_EXPENSE = (0.90, 0.30, 0.28, 1)      # 支出红
C_INCOME = (0.18, 0.66, 0.40, 1)       # 收入绿
C_TEXT = (0.12, 0.14, 0.18, 1)
C_MUTED = (0.55, 0.58, 0.63, 1)
C_BG = (0.96, 0.97, 0.98, 1)
C_CARD = (1, 1, 1, 1)

# 注册中文字体。
# Kivy 默认的 Roboto 不含 CJK 字形，中文会显示成方块（tofu），
# 必须在加载 KV 前注册并在 KV 里把 font_name 指向它。
# 字体文件随 APK 打包（见 buildozer.spec 的 source.include_exts 里的 ttc/ttf）。
FONT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "fonts",
    "wqy-microhei.ttc"
)
FONT_NAME = "CJK"

try:
    from kivy.core.text import LabelBase
    if os.path.exists(FONT_PATH):
        LabelBase.register(name=FONT_NAME, fn_regular=FONT_PATH)
    else:
        # 打包后路径由 buildozer 管理，仍指向 assets；找不到时退回默认字体
        FONT_NAME = "Roboto"
except Exception:
    FONT_NAME = "Roboto"


# ------------------------------------------------------------------ 自定义控件

class Card(BoxLayout):
    """带圆角阴影感的卡片容器。"""

    def __init__(self, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("padding", dp(14))
        kw.setdefault("spacing", dp(8))
        super().__init__(**kw)
        with self.canvas.before:
            Color(*C_CARD)
            self._rect = RoundedRectangle(radius=[dp(12)])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size


def make_label(text, size=14, bold=False, color=C_TEXT, halign="left", **kw):
    lbl = Label(
        text=text, font_size=dp(size), bold=bold, color=color,
        halign=halign, valign="middle", **kw
    )
    lbl.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, w.height)))
    return lbl


class AmountInput(TextInput):
    """只允许输入金额的输入框。"""

    def insert_text(self, substring, from_undo=False):
        # 只保留数字和一个小数点，且小数不超过两位
        cleaned = "".join(c for c in substring if c.isdigit() or c == ".")
        if cleaned.count(".") > 1:
            return
        if "." in self.text and "." in cleaned:
            return
        if "." in self.text:
            int_part, dot, dec = (self.text + cleaned).partition(".")
            if len(dec) > 2:
                return
        return super().insert_text(cleaned, from_undo=from_undo)


# ------------------------------------------------------------------ 屏幕

class HomeScreen(Screen):
    """首页。"""

    def on_pre_enter(self, *_):
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        svc = app.service

        s, e = month_range()
        st = svc.stats(s, e)
        self.ids.month_label.text = f"{s[:7].replace('-', ' 年 ')} 月"
        self.ids.expense_label.text = f"¥{st['expense_yuan']}"
        self.ids.income_label.text = f"¥{st['income_yuan']}"
        self.ids.balance_label.text = f"¥{st['balance_yuan']}"
        self.ids.count_label.text = f"本月 {st['count']} 笔 · 日均 ¥{st['daily_avg_yuan']}"

        # 最近 20 条
        box = self.ids.recent_list
        box.clear_widgets()
        txs = svc.list_transactions(limit=20)
        if not txs:
            box.add_widget(
                make_label("还没有账目\n点下面的「拍图记账」试试", size=14,
                           color=C_MUTED, halign="center")
            )
            return
        for tx in txs:
            box.add_widget(TxRow(tx=tx))


class TxRow(BoxLayout):
    """账目列表的一行。"""

    def __init__(self, tx: Transaction, on_tap=None, **kw):
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(58))
        kw.setdefault("padding", [dp(4), dp(2)])
        super().__init__(**kw)
        self.tx = tx

        if on_tap:
            self._on_tap = on_tap
        else:
            self._on_tap = self._default_tap
        self.bind(on_touch_down=self._touch)

        icon = {"screenshot": "截图", "receipt": "小票"}.get(tx.source, "手动")
        src_color = C_MUTED if tx.source == SOURCE_MANUAL else C_PRIMARY

        left = BoxLayout(orientation="vertical", size_hint_x=0.72)
        title = f"{tx.merchant or tx.category}"
        left.add_widget(make_label(title, size=15, bold=True))
        sub = f"[{icon}] {tx.category} · {tx.occurred_at[5:16]}"
        left.add_widget(make_label(sub, size=11, color=src_color))
        self.add_widget(left)

        sign = "-" if tx.is_expense else "+"
        color = C_EXPENSE if tx.is_expense else C_INCOME
        amt = make_label(f"{sign}¥{tx.amount_yuan}", size=16, bold=True,
                         color=color, halign="right")
        amt.size_hint_x = 0.28
        self.add_widget(amt)

    def _touch(self, _w, touch):
        if self.collide_point(*touch.pos):
            self._on_tap(self.tx)
            return True
        return False

    def _default_tap(self, tx):
        App.get_running_app().open_edit(tx)


class ConfirmScreen(Screen):
    """识别结果确认页——APP 的核心交互。

    展示 OCR 原文 + 解析出的字段，全部可编辑。
    """

    def load_result(self, result, image_path: str = ""):
        """把识别结果填充到表单。"""
        self._result = result
        self._image_path = image_path

        self.ids.raw_text.text = result.raw_text or "（无识别文本）"

        if result.ok:
            tx = result.parsed
            self.ids.amount_input.text = tx.amount_yuan
            self.ids.merchant_input.text = (
                "" if tx.merchant == "未识别商户" else tx.merchant
            )
            self.ids.date_input.text = tx.occurred_at[:10]
            self.ids.time_input.text = tx.occurred_at[11:16]
            self.ids.type_spinner.text = "支出" if tx.is_expense else "收入"
            self._sync_categories(tx.category)

            conf = int(result.confidence * 100)
            color = C_INCOME if conf >= 80 else ((1, 0.65, 0.1, 1) if conf >= 60 else C_EXPENSE)
            self.ids.conf_label.text = f"识别置信度 {conf}%"
            self.ids.conf_label.color = color
        else:
            # 识别失败也要能继续——用户手动补齐即可
            self.ids.amount_input.text = ""
            self.ids.merchant_input.text = ""
            self.ids.date_input.text = today_str()
            self.ids.time_input.text = "12:00"
            self.ids.type_spinner.text = "支出"
            self._sync_categories("其他")
            self.ids.conf_label.text = "识别失败"
            self.ids.conf_label.color = C_EXPENSE

        # 提示信息
        warns = list(result.warnings)
        if result.error:
            warns.insert(0, result.error)
        self.ids.warn_label.text = "\n".join(f"· {w}" for w in warns)
        self.ids.warn_label.height = dp(18) * max(len(warns), 1)

    def _sync_categories(self, selected="其他"):
        app = App.get_running_app()
        is_expense = self.ids.type_spinner.text == "支出"
        cats = CATEGORIES_EXPENSE if is_expense else CATEGORIES_INCOME
        self.ids.category_spinner.values = cats
        self.ids.category_spinner.text = selected if selected in cats else cats[-1]

    def on_type_change(self, value):
        self._sync_categories()

    def on_source_change(self, value):
        """用户切换「截图/小票」后重新识别。"""
        if not getattr(self, "_image_path", ""):
            return
        app = App.get_running_app()
        kind = "receipt" if self.ids.source_spinner.text == "纸质小票" else "screenshot"
        app.start_recognition(self._image_path, kind, self)

    def save(self):
        """校验并入库。"""
        app = App.get_running_app()
        amount_text = self.ids.amount_input.text.strip()
        if not amount_text:
            app.toast("请填写金额")
            return
        try:
            cents = yuan_to_cents(amount_text)
        except Exception:
            app.toast("金额格式不正确")
            return
        if cents <= 0:
            app.toast("金额必须大于 0")
            return

        merchant = self.ids.merchant_input.text.strip()
        date_s = self.ids.date_input.text.strip()
        time_s = self.ids.time_input.text.strip() or "12:00"
        if len(time_s) == 5:
            time_s += ":00"
        occurred = f"{date_s} {time_s}"

        tx = getattr(self._result, "parsed", None) if self._result else None
        if tx is None:
            tx = Transaction(amount=cents)

        tx.amount = cents
        tx.type = TYPE_EXPENSE if self.ids.type_spinner.text == "支出" else TYPE_INCOME
        tx.category = self.ids.category_spinner.text
        tx.merchant = merchant or "未识别商户"
        tx.occurred_at = occurred
        tx.note = self.ids.note_input.text.strip()
        tx.image_path = self._image_path

        src = self.ids.source_spinner.text
        if tx.source == SOURCE_MANUAL:
            tx.source = SOURCE_RECEIPT if src == "纸质小票" else SOURCE_SCREENSHOT

        if getattr(self, "_editing", False):
            app.service.update_transaction(tx)
        else:
            app.service.add_transaction(tx)

        app.toast("已保存")
        sm = self.ids.sm
        sm.current = "home"
        # 通过 sm 取回首页并刷新；比 app.root.get_screen 更可靠
        # （app.root 在 ScreenManager 尚未挂载时会是 None）
        sm.get_screen("home").refresh()


class AddScreen(Screen):
    """手动记账页。"""

    def on_pre_enter(self, *_):
        import datetime as _dt
        self.ids.amount_input.text = ""
        self.ids.merchant_input.text = ""
        self.ids.note_input.text = ""
        self.ids.date_input.text = today_str()
        self.ids.time_input.text = _dt.datetime.now().strftime("%H:%M")
        self.ids.type_spinner.text = "支出"
        self.on_type_change("支出")

    def on_type_change(self, value):
        app = App.get_running_app()
        cats = CATEGORIES_EXPENSE if value == "支出" else CATEGORIES_INCOME
        self.ids.category_spinner.values = cats
        self.ids.category_spinner.text = cats[0]

    def save(self):
        app = App.get_running_app()
        txt = self.ids.amount_input.text.strip()
        if not txt:
            app.toast("请填写金额")
            return
        try:
            cents = yuan_to_cents(txt)
        except Exception:
            app.toast("金额格式不正确")
            return
        if cents <= 0:
            app.toast("金额必须大于 0")
            return

        time_s = self.ids.time_input.text.strip() or "12:00"
        if len(time_s) == 5:
            time_s += ":00"

        tx = Transaction(
            amount=cents,
            type=TYPE_EXPENSE if self.ids.type_spinner.text == "支出" else TYPE_INCOME,
            category=self.ids.category_spinner.text,
            merchant=self.ids.merchant_input.text.strip(),
            note=self.ids.note_input.text.strip(),
            occurred_at=f"{self.ids.date_input.text.strip()} {time_s}",
            source=SOURCE_MANUAL,
        )
        app.service.add_transaction(tx)
        app.toast("已保存")
        sm = self.ids.sm
        sm.current = "home"
        # 通过 sm 取回首页并刷新；比 app.root.get_screen 更可靠
        # （app.root 在 ScreenManager 尚未挂载时会是 None）
        sm.get_screen("home").refresh()


class ListScreen(Screen):
    """全部账目 + 筛选。"""

    _filter_type = None

    def on_pre_enter(self, *_):
        self.refresh()

    def set_filter(self, tx_type):
        self._filter_type = tx_type
        for key, val in (("all", None), ("exp", TYPE_EXPENSE), ("inc", TYPE_INCOME)):
            btn = self.ids.get(f"filt_{key}")
            if btn:
                btn.background_color = C_PRIMARY if val == tx_type else (0.85, 0.87, 0.90, 1)
                btn.color = (1, 1, 1, 1) if val == tx_type else C_TEXT
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        box = self.ids.tx_list
        box.clear_widgets()

        kw = {"limit": 300}
        if self._filter_type:
            kw["tx_type"] = self._filter_type
        kw_s = self.ids.search_input.text.strip()
        if kw_s:
            kw["keyword"] = kw_s

        txs = app.service.list_transactions(**kw)
        if not txs:
            box.add_widget(make_label("没有符合条件的账目", color=C_MUTED, halign="center"))
            return

        total_exp = sum(t.amount for t in txs if t.is_expense)
        total_inc = sum(t.amount for t in txs if not t.is_expense)
        self.ids.summary_label.text = (
            f"共 {len(txs)} 笔 | 支出 ¥{cents_to_yuan(total_exp)} | 收入 ¥{cents_to_yuan(total_inc)}"
        )
        for tx in txs:
            box.add_widget(TxRow(tx=tx))


class StatsScreen(Screen):
    """统计页：分类构成条形图（纯 Kivy 绘制，不依赖 matplotlib）。"""

    def on_pre_enter(self, *_):
        self._range = "month"
        self.refresh()

    def set_range(self, rng):
        self._range = rng
        for r, label in (("week", "本周"), ("month", "本月"), ("year", "本年")):
            btn = self.ids.get(f"rng_{r}")
            if btn:
                btn.background_color = C_PRIMARY if r == rng else (0.85, 0.87, 0.90, 1)
                btn.color = (1, 1, 1, 1) if r == rng else C_TEXT
        self.refresh()

    def refresh(self):
        import datetime as _dt
        app = App.get_running_app()
        svc = app.service

        if self._range == "week":
            s, e = week_range()
            title = "本周"
        elif self._range == "year":
            y = _dt.datetime.now().year
            s, e = f"{y}-01-01", f"{y}-12-31"
            title = f"{y} 年"
        else:
            s, e = month_range()
            title = s[:7].replace("-", " 年 ") + " 月"

        st = svc.stats(s, e)
        self.ids.range_title.text = title
        self.ids.big_expense.text = f"¥{st['expense_yuan']}"
        self.ids.big_income.text = f"¥{st['income_yuan']}"
        self.ids.big_balance.text = f"¥{st['balance_yuan']}"
        self.ids.stats_sub.text = (
            f"{st['count']} 笔 · 日均支出 ¥{st['daily_avg_yuan']}"
        )

        box = self.ids.cat_list
        box.clear_widgets()
        cats = st["categories"]
        if not cats:
            box.add_widget(make_label("该时间段没有支出记录", color=C_MUTED, halign="center"))
            return
        for item in cats[:12]:
            box.add_widget(CatBar(item))


class CatBar(BoxLayout):
    """单个分类的横向条形图。"""

    def __init__(self, item: dict, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(52))
        kw.setdefault("spacing", dp(2))
        super().__init__(**kw)

        top = BoxLayout(size_hint_y=None, height=dp(20))
        top.add_widget(make_label(
            f"{item['category']}  ({item['count']}笔)", size=13, bold=True
        ))
        right = make_label(f"¥{item['amount_yuan']}  {item['percent']}%",
                           size=13, halign="right", color=C_TEXT)
        right.size_hint_x = 0.55
        top.add_widget(right)
        self.add_widget(top)

        bar_bg = Widget(size_hint_y=None, height=dp(8))
        with bar_bg.canvas:
            Color(0.90, 0.92, 0.95, 1)
            self._bg = RoundedRectangle(radius=[dp(4)])
        with bar_bg.canvas.after:
            Color(*C_EXPENSE)
            self._fg = RoundedRectangle(radius=[dp(4)])

        def _sync(*_):
            self._bg.pos = bar_bg.pos
            self._bg.size = bar_bg.size
            w = max(bar_bg.width * item["percent"] / 100.0, dp(4))
            self._fg.pos = bar_bg.pos
            self._fg.size = (w, bar_bg.height)

        bar_bg.bind(pos=_sync, size=_sync)
        self.add_widget(bar_bg)


class SettingsScreen(Screen):
    """设置页。"""

    def on_pre_enter(self, *_):
        s = App.get_running_app().service.settings
        self.ids.engine_spinner.text = {
            "baidu": "百度智能云", "tencent": "腾讯云", "mock": "离线模拟（测试）"
        }.get(s.get("ocr_engine"), "百度智能云")
        self.ids.baidu_key.text = s.get("baidu_api_key")
        self.ids.baidu_secret.text = s.get("baidu_secret_key")
        self.ids.tc_id.text = s.get("tencent_secret_id")
        self.ids.tc_key.text = s.get("tencent_secret_key")
        self.ids.status_label.text = ""
        self._sync_engine_fields()

    def _sync_engine_fields(self):
        engine = self.ids.engine_spinner.text
        show_baidu = engine == "百度智能云"
        show_tc = engine == "腾讯云"
        for w in (self.ids.baidu_key, self.ids.baidu_secret):
            w.disabled = not show_baidu
            w.opacity = 1 if show_baidu else 0.4
        for w in (self.ids.tc_id, self.ids.tc_key):
            w.disabled = not show_tc
            w.opacity = 1 if show_tc else 0.4

    def on_engine_change(self, value):
        self._sync_engine_fields()

    def save(self):
        app = App.get_running_app()
        s = app.service.settings
        mapping = {
            "百度智能云": "baidu", "腾讯云": "tencent", "离线模拟（测试）": "mock",
        }
        s.set("ocr_engine", mapping.get(self.ids.engine_spinner.text, "baidu"))
        s.set("baidu_api_key", self.ids.baidu_key.text.strip())
        s.set("baidu_secret_key", self.ids.baidu_secret.text.strip())
        s.set("tencent_secret_id", self.ids.tc_id.text.strip())
        s.set("tencent_secret_key", self.ids.tc_key.text.strip())
        s.save()
        app.service._engine_cache.clear()

        self.ids.status_label.text = "已保存"
        self.ids.status_label.color = C_INCOME

    def test_conn(self):
        app = App.get_running_app()
        self.save()
        self.ids.status_label.text = "正在检测…"
        self.ids.status_label.color = C_MUTED

        def _run():
            ok, msg = app.service.test_engine()
            Clock.schedule_once(lambda *_: self._show_test(ok, msg))

        threading.Thread(target=_run, daemon=True).start()

    @mainthread
    def _show_test(self, ok, msg):
        self.ids.status_label.text = ("✓ " if ok else "✗ ") + msg
        self.ids.status_label.color = C_INCOME if ok else C_EXPENSE


# ------------------------------------------------------------------ App

class LedgerApp(App):
    """应用主类。"""

    title = "轻记账"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.service: LedgerService = None
        self._busy_popup = None

    # ---------- 生命周期 ----------

    def get_data_dir(self) -> str:
        """安卓上返回 APP 私有目录；桌面端返回当前目录下的 data/。"""
        try:
            from android.storage import app_storage_path  # type: ignore
            return os.path.join(app_storage_path(), "ledgerdata")
        except Exception:
            return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "data")

    def build(self):
        # Window 在极少数环境（无图形库、CI 无头模式）下会是 None，
        # 这里做保护，保证业务逻辑仍可被测试与调用。
        if Window is not None:
            Window.clearcolor = C_BG
        self.service = LedgerService(self.get_data_dir())

        kv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.kv")
        Builder.load_file(kv_path)

        sm = ScreenManager(transition=SlideTransition(duration=0.18))
        for name, cls in (
            ("home", HomeScreen),
            ("add", AddScreen),
            ("list", ListScreen),
            ("stats", StatsScreen),
            ("settings", SettingsScreen),
            ("confirm", ConfirmScreen),
        ):
            sc = cls(name=name)
            sc.ids.sm = sm
            sm.add_widget(sc)
        return sm

    def on_start(self):
        # build() 时数据库刚初始化还是空的，首页已经用空数据渲染过了；
        # 真正的数据要等启动完成后才能查，这里强制刷新一次
        self.root.get_screen("home").refresh()

    def on_stop(self):
        if self.service:
            self.service.close()

    # ---------- 交互动作 ----------

    def toast(self, message: str):
        """轻提示。"""
        popup = Popup(
            title="", content=make_label(message, halign="center", size=15),
            size_hint=(0.6, 0.18), auto_dismiss=True,
            background_color=(0, 0, 0, 0.85), separator_height=0,
        )
        popup.open()
        Clock.schedule_once(lambda *_: popup.dismiss(), 1.4)

    def pick_and_recognize(self, kind: str):
        """打开系统图片选择器，选中后进入识别流程。"""
        if not self.service.settings.has_credentials():
            self._ask_config()
            return
        try:
            from plyer import filechooser
            filechooser.open_file(
                on_selection=lambda sel: self._on_picked(sel, kind),
                filters=[["*.png", "*.jpg", "*.jpeg", "*.webp"]],
            )
        except Exception as e:
            self.toast(f"无法打开相册：{e}")

    def _on_picked(self, selection, kind):
        if not selection:
            return
        src = selection[0] if isinstance(selection, (list, tuple)) else selection
        if not src or not os.path.exists(src):
            self.toast("读取图片失败")
            return
        # 复制进私有目录后再识别
        local = self.service.import_image(src)
        self.start_recognition(local, kind)

    def start_recognition(self, image_path: str, kind: str, screen=None):
        """异步识别，避免阻塞 UI 线程。"""
        self._show_busy("正在识别图片…")
        target = screen or self.root.get_screen("confirm")

        def _work():
            result = self.service.recognize_image(image_path, kind)
            Clock.schedule_once(lambda *_: self._finish_recognition(result, image_path, target))

        threading.Thread(target=_work, daemon=True).start()

    @mainthread
    def _finish_recognition(self, result, image_path, target):
        self._hide_busy()
        target.load_result(result, image_path)
        self.root.current = "confirm"

    def open_edit(self, tx: Transaction):
        """点击已有账目 -> 复用确认页编辑。"""
        from ..core.models import ParseResult
        sc = self.root.get_screen("confirm")
        res = ParseResult(parsed=Transaction.from_dict(tx.to_dict()),
                          raw_text="（编辑已有账目）", confidence=tx.ocr_confidence)
        sc.load_result(res, tx.image_path)
        sc._editing = True
        sc.ids.source_spinner.text = {
            SOURCE_RECEIPT: "纸质小票", SOURCE_MANUAL: "手动录入"
        }.get(tx.source, "截图")
        # 覆盖保存逻辑：编辑态走 update
        sc.ids.save_btn.text = "保存修改"
        self.root.current = "confirm"

    def _ask_config(self):
        # Popup 标题不走全局字体规则，中文会变方块，
        # 所以 title 留空，标题用内容区的 Label 承担
        content = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        content.add_widget(make_label("还没配置 OCR", size=17, bold=True,
                                      halign="center"))
        content.add_widget(make_label(
            "图片自动记账需要 OCR 服务。\n\n"
            "请到「设置」里填入百度或腾讯云的 API Key\n"
            "（两家都有每月免费额度）。\n\n"
            "想先试用，可把引擎切成「离线模拟」。",
            size=13, halign="center"
        ))
        popup = Popup(title="", content=content, size_hint=(0.85, 0.5))
        popup.open()
        Clock.schedule_once(lambda *_: setattr(self.root, "current", "settings"), 0.1)

    def _show_busy(self, msg):
        self._busy_popup = Popup(
            title="", content=make_label(msg, halign="center"),
            size_hint=(0.6, 0.18), auto_dismiss=False,
        )
        self._busy_popup.open()

    def _hide_busy(self):
        if self._busy_popup:
            self._busy_popup.dismiss()
            self._busy_popup = None

    def goto(self, screen_name):
        """底部导航切换。"""
        self.root.current = screen_name

    def export_data(self):
        """导出 CSV 到下载目录。"""
        import datetime as _dt
        name = f"ledger_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        out = os.path.join(self.get_data_dir(), name)
        self.service.export_csv(out)
        self.toast(f"已导出：{name}")


if __name__ == "__main__":
    LedgerApp().run()
