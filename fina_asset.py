"""資産管理プログラム。取引を手入力して、保有・評価額・含み損益・実現損益を確かめる。

設計は fina_asset_design.md、仕様は fina_asset_spec.md を参照。いまは Phase 1(日本株現物)。

取引(約定日・銘柄・証券会社・口座区分・売買・数量・単価・手数料)を記録すると、
保有は (銘柄 × 証券会社 × 口座区分) ごとに自動で集計される。平均取得単価は移動平均法。
評価は前日終値ベースで、stock_database フォルダの保存済み日足データの終値を使う
(株価は取得しないので、先に core_gather.py で株価を取得しておくこと)。
税は反映しない(実現損益は税引前)。現金(預り金)は管理しない。

記録はルートディレクトリの asset.json に保存し、次回の起動時に自動で読み込む。
起動した日の資産の合計を、1 日 1 回 asset.json に記録する(資産推移のグラフ用。Phase 2 で表示)。

使い方:
    python fina_asset.py

操作:
    取引の入力    「取引を入力」ボタン。銘柄は、銘柄コードまたは銘柄名の一部を入力して候補から選ぶ
    絞り込み      上の欄で証券会社・口座区分を選ぶ(すべてのタブと合計に効く)
    保有一覧      ダブルクリック・「チャート」ボタンでチャートを開く(自分の売買を ▲▼ で表示)。
                  右クリック・ボタンで、その保有の買い増し・売却を入力する。
                  「銘柄ごとにまとめる」で、証券会社・口座をまたいで合算する
    取引履歴      ダブルクリックで編集、Delete キー・「削除」ボタンで削除
    実現損益      年を選ぶと、その年の決済と銘柄ごとの合計を表示する
    並べ替え      一覧の見出しをクリック(もう一度押すと逆順)
"""

import math
import os
import re
import sys
import tkinter as tk
import unicodedata
from datetime import date, datetime
from tkinter import messagebox, simpledialog, ttk

import pandas as pd

from core_asset import (
    ACCOUNT_TYPES, ACTIONS, ASSET_PATH, AssetClass, AssetStore, HoldingKey, Instrument, Trade,
    build_ledger, changed_problems, jp_stock_id, validate_trade,
)
from core_asset_value import PriceBook, Valuation, summarize, take_snapshot_if_needed, value_all
from core_gather import csv_path_for
from core_stocks import load_listed, normalize, stock_index
from gui_chart import ChartFrame, TradeMark
from gui_widgets import StockSearchBox

APP_NAME = "fina_asset"
ALL = "すべて"  # 絞り込みで「すべて」を表す項目

# 損益の色(日本式: 利益は赤、損失は緑。設定で反転できる)
RED = "#d32f2f"
GREEN = "#2e7d32"
PROBLEM_BACKGROUND = "#fff3cd"  # 集計できなかった取引の背景

# 一覧の列(列の名前 -> (見出し、幅、文字の寄せ方))
HOLDING_COLUMNS = {
    "code": ("コード", 60, "center"),
    "name": ("銘柄名", 200, "w"),
    "broker": ("証券会社", 100, "w"),
    "account": ("口座", 90, "center"),
    "quantity": ("数量", 80, "e"),
    "average": ("平均取得単価", 100, "e"),
    "close": ("現在値", 90, "e"),
    "change": ("前日比", 70, "e"),
    "value": ("評価額", 110, "e"),
    "unrealized": ("含み損益", 110, "e"),
    "unrealized_pct": ("損益率", 70, "e"),
}
TRADE_COLUMNS = {
    "date": ("約定日", 90, "center"),
    "code": ("コード", 60, "center"),
    "name": ("銘柄名", 180, "w"),
    "broker": ("証券会社", 100, "w"),
    "account": ("口座", 90, "center"),
    "action": ("売買", 50, "center"),
    "quantity": ("数量", 80, "e"),
    "price": ("単価", 90, "e"),
    "fee": ("手数料", 70, "e"),
    "amount": ("受渡金額", 110, "e"),
    "memo": ("メモ", 160, "w"),
    "source": ("入力元", 70, "center"),
}
REALIZED_COLUMNS = {
    "date": ("決済日", 90, "center"),
    "code": ("コード", 60, "center"),
    "name": ("銘柄名", 180, "w"),
    "broker": ("証券会社", 100, "w"),
    "account": ("口座", 90, "center"),
    "quantity": ("数量", 80, "e"),
    "proceeds": ("売却額", 110, "e"),
    "cost": ("取得原価", 110, "e"),
    "profit": ("損益", 110, "e"),
}
REALIZED_BY_STOCK_COLUMNS = {
    "code": ("コード", 60, "center"),
    "name": ("銘柄名", 160, "w"),
    "count": ("回数", 50, "e"),
    "profit": ("損益", 110, "e"),
}
SOURCE_LABELS = {"manual": "手入力"}


# ---- 入力と表示の整形 ----

def parse_number(text: str) -> float | None:
    """入力欄の数を読む。全角・カンマ・「円」「株」は許す。読めなければ None。"""
    text = unicodedata.normalize("NFKC", text).replace(",", "").replace("円", "").replace("株", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_date(text: str) -> str | None:
    """入力欄の日付を "YYYY-MM-DD" に直す。"2026/11/18" や "20261118" も読む。読めなければ None。"""
    text = unicodedata.normalize("NFKC", text).strip().replace("/", "-").replace(".", "-")
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_time(text: str) -> str | None:
    """入力欄の時刻を "HH:MM" に直す。空なら空文字。読めなければ None。"""
    text = unicodedata.normalize("NFKC", text).strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text, "%H:%M").strftime("%H:%M")
    except ValueError:
        return None


def fmt_quantity(value: float) -> str:
    return f"{value:,.10g}"


def fmt_price(value: float | None) -> str:
    """単価の表示(小数点以下は必要な分だけ、2 桁まで)。"""
    if value is None:
        return "―"
    text = f"{value:,.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def fmt_yen(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "―"
    return f"{value:+,.0f}" if signed else f"{value:,.0f}"


def fmt_pct(value: float | None) -> str:
    return "―" if value is None else f"{value:+.2f}%"


def profit_tag(value: float | None) -> tuple[str, ...]:
    """損益の符号に合わせた行の色の名前。"""
    if value is None or abs(value) < 0.5:
        return ()
    return ("plus",) if value > 0 else ("minus",)


# ---- 並べ替えのできる一覧 ----

class SortableTree(ttk.Frame):
    """見出しをクリックして並べ替えのできる一覧(ttk.Treeview とスクロールバー)。

    行は (iid, 表示する値, 並べ替えに使う値, タグ) で渡す。並べ替えに使う値が None の行は、常に最後に置く。
    """

    def __init__(self, master, columns: dict, stretch: str, height: int = 10):
        super().__init__(master)
        self.columns = list(columns)
        self.rows: list[tuple] = []
        self.sort_column: str | None = None
        self.sort_reverse = False
        self.tree = ttk.Treeview(self, columns=self.columns, show="headings", selectmode="browse", height=height)
        for column, (heading, width, anchor) in columns.items():
            self.tree.heading(column, text=heading, command=lambda c=column: self._on_heading(c))
            self.tree.column(column, width=width, anchor=anchor, stretch=column == stretch)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

    def set_rows(self, rows: list[tuple]) -> None:
        """行を入れ替える。選んでいた行が残っていれば、選んだままにする。"""
        selected = self.selected()
        self.rows = rows
        self._render()
        if selected is not None and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.see(selected)

    def selected(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def set_colors(self, plus: str, minus: str) -> None:
        self.tree.tag_configure("plus", foreground=plus)
        self.tree.tag_configure("minus", foreground=minus)
        self.tree.tag_configure("problem", background=PROBLEM_BACKGROUND)

    def _on_heading(self, column: str):
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column, self.sort_reverse = column, False
        self._render()

    def _render(self):
        rows = self.rows
        if self.sort_column is not None:
            i = self.columns.index(self.sort_column)
            known = [r for r in rows if r[2][i] is not None]
            unknown = [r for r in rows if r[2][i] is None]
            rows = sorted(known, key=lambda r: r[2][i], reverse=self.sort_reverse) + unknown
        for column in self.columns:
            heading = self.tree.heading(column, "text").rstrip(" ▲▼")
            mark = (" ▼" if self.sort_reverse else " ▲") if column == self.sort_column else ""
            self.tree.heading(column, text=heading + mark)
        self.tree.delete(*self.tree.get_children())
        for iid, values, _keys, tags in rows:
            self.tree.insert("", "end", iid=iid, values=values, tags=tags)


# ---- タブ: 保有一覧 ----

class HoldingsTab(ttk.Frame):
    """保有一覧。(銘柄 × 証券会社 × 口座区分) ごと、または銘柄ごとにまとめて表示する。"""

    def __init__(self, master, app: "AssetApp"):
        super().__init__(master)
        self.app = app
        self.targets: dict[str, tuple[str, HoldingKey | None]] = {}  # 行 -> (商品 ID、保有のキー。まとめた行は None)

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=4)
        self.merge_var = tk.BooleanVar(value=bool(app.store.ui.get("merge", False)))
        ttk.Checkbutton(bar, text="銘柄ごとにまとめる", variable=self.merge_var, command=app.refresh).pack(side="left")
        for text, command in (("売却", self.sell), ("買い増し", self.buy), ("チャート", self.open_chart)):
            ttk.Button(bar, text=text, command=command).pack(side="right", padx=(2, 0))

        self.list = SortableTree(self, HOLDING_COLUMNS, stretch="name")
        self.list.pack(side="top", fill="both", expand=True, padx=4)
        tree = self.list.tree
        tree.bind("<Double-Button-1>", lambda e: tree.identify_row(e.y) and self.open_chart())
        tree.bind("<Return>", lambda e: self.open_chart())
        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="チャートを開く", command=self.open_chart)
        self.menu.add_command(label="買い増しを入力", command=self.buy)
        self.menu.add_command(label="売却を入力", command=self.sell)
        tree.bind("<Button-3>", self._on_right_click)

    def show(self, valuations: list[Valuation]) -> None:
        self.targets = {}
        rows = []
        if self.merge_var.get():
            groups: dict[str, list[Valuation]] = {}
            for v in valuations:
                groups.setdefault(v.instrument.id, []).append(v)
            for n, (instrument_id, group) in enumerate(groups.items()):
                first = group[0]
                quantity = sum(v.holding.quantity for v in group)
                cost = sum(v.cost_jpy for v in group)
                values = [v.value_jpy for v in group]
                value = None if None in values else sum(values)
                unrealized = None if value is None else value - cost
                brokers = sorted({v.holding.broker for v in group})
                accounts = sorted({v.holding.account for v in group})
                average = cost / (quantity * first.holding.multiplier) if quantity else None
                iid = f"m{n}"
                self.targets[iid] = (instrument_id, None)
                rows.append(self._row(
                    iid, first, " / ".join(brokers), " / ".join(accounts), quantity, average, value, unrealized,
                    unrealized / cost * 100 if unrealized is not None and cost else None,
                ))
        else:
            for n, v in enumerate(valuations):
                iid = f"h{n}"
                self.targets[iid] = (v.instrument.id, v.holding.key)
                rows.append(self._row(
                    iid, v, v.holding.broker, v.holding.account, v.holding.quantity, v.holding.average_price,
                    v.value_jpy, v.unrealized_jpy, v.unrealized_pct,
                ))
        # 最初は評価額の大きい順に並べる
        rows.sort(key=lambda r: -(r[2][8] or 0))
        self.list.set_rows(rows)

    @staticmethod
    def _row(iid, v: Valuation, broker, account, quantity, average, value, unrealized, unrealized_pct) -> tuple:
        close = v.quote.close if v.quote else None
        change = v.change_pct
        display = (
            v.instrument.code, v.instrument.name, broker, account, fmt_quantity(quantity), fmt_price(average),
            fmt_price(close), fmt_pct(change), fmt_yen(value), fmt_yen(unrealized, signed=True),
            fmt_pct(unrealized_pct),
        )
        keys = (
            v.instrument.code, v.instrument.name, broker, account, quantity, average, close, change, value,
            unrealized, unrealized_pct,
        )
        return (iid, display, keys, profit_tag(unrealized))

    def _target(self) -> tuple[str, HoldingKey | None] | None:
        iid = self.list.selected()
        if iid is None:
            self.app.set_message("保有一覧で銘柄を選んでください。")
            return None
        return self.targets[iid]

    def _on_right_click(self, event):
        iid = self.list.tree.identify_row(event.y)
        if iid:
            self.list.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def open_chart(self) -> None:
        target = self._target()
        if target:
            self.app.open_chart(*target)

    def buy(self) -> None:
        self._open_dialog("buy")

    def sell(self) -> None:
        self._open_dialog("sell")

    def _open_dialog(self, action: str) -> None:
        target = self._target()
        if target is None:
            return
        instrument_id, key = target
        preset = {"instrument_id": instrument_id, "action": action}
        if key is not None:
            preset.update(broker=key[1], account=key[2])
            holding = self.app.ledger.holdings.get(key)
            if action == "sell" and holding is not None:
                preset["quantity"] = fmt_quantity(holding.quantity)  # 売却は全数量を初期値にする
        self.app.open_trade_dialog(preset=preset)


# ---- タブ: 取引履歴 ----

class TradesTab(ttk.Frame):
    """取引履歴。期間・銘柄で絞り込み、編集・削除する。"""

    def __init__(self, master, app: "AssetApp"):
        super().__init__(master)
        self.app = app
        self.trades: list[Trade] = []

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=4)
        self.start_var, self.end_var, self.query_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(bar, text="期間").pack(side="left")
        ttk.Entry(bar, textvariable=self.start_var, width=11).pack(side="left", padx=(4, 0))
        ttk.Label(bar, text="〜").pack(side="left")
        ttk.Entry(bar, textvariable=self.end_var, width=11).pack(side="left")
        ttk.Label(bar, text="銘柄").pack(side="left", padx=(12, 0))
        ttk.Entry(bar, textvariable=self.query_var, width=20).pack(side="left", padx=(4, 0))
        for var in (self.start_var, self.end_var, self.query_var):
            var.trace_add("write", lambda *_: self._render())
        for text, command in (("削除", self.delete), ("編集", self.edit)):
            ttk.Button(bar, text=text, command=command).pack(side="right", padx=(2, 0))

        self.list = SortableTree(self, TRADE_COLUMNS, stretch="memo")
        self.list.pack(side="top", fill="both", expand=True, padx=4)
        tree = self.list.tree
        tree.bind("<Double-Button-1>", lambda e: tree.identify_row(e.y) and self.edit())
        tree.bind("<Return>", lambda e: self.edit())
        tree.bind("<Delete>", lambda e: self.delete())

    def show(self, trades: list[Trade]) -> None:
        self.trades = trades
        self._render()

    def _render(self):
        start, end = parse_date(self.start_var.get()), parse_date(self.end_var.get())
        words = normalize(self.query_var.get()).split()
        problems = self.app.problem_ids
        instruments = self.app.store.instruments
        rows = []
        for t in self.trades:
            instrument = instruments.get(t.instrument_id)
            code, name = (instrument.code, instrument.name) if instrument else (t.instrument_id, "")
            if (start and t.date < start) or (end and t.date > end):
                continue
            if words and not all(w in normalize(code) or w in normalize(name) for w in words):
                continue
            multiplier = instrument.multiplier if instrument else 1
            amount = None
            if t.action == "buy":
                amount = t.quantity * t.price * multiplier + t.fee
            elif t.action == "sell":
                amount = t.quantity * t.price * multiplier - t.fee
            is_split = t.action == "split"
            display = (
                f"{t.date} {t.time}".strip(), code, name, t.broker, t.account, ACTIONS.get(t.action, t.action),
                "" if is_split else fmt_quantity(t.quantity),
                f"1→{fmt_quantity(t.price)}" if is_split else fmt_price(t.price),
                "" if is_split else fmt_yen(t.fee), fmt_yen(amount) if amount is not None else "",
                t.memo, SOURCE_LABELS.get(t.source, t.source),
            )
            keys = (
                t.order, code, name, t.broker, t.account, t.action, t.quantity, t.price, t.fee, amount, t.memo,
                t.source,
            )
            rows.append((str(t.id), display, keys, ("problem",) if t.id in problems else ()))
        rows.sort(key=lambda r: r[2][0], reverse=True)  # 最初は新しい順
        self.list.set_rows(rows)

    def _selected_trade(self) -> Trade | None:
        iid = self.list.selected()
        if iid is None:
            self.app.set_message("取引履歴で取引を選んでください。")
            return None
        return self.app.store.trade(int(iid))

    def edit(self) -> None:
        trade = self._selected_trade()
        if trade is not None:
            self.app.open_trade_dialog(trade=trade)

    def delete(self) -> None:
        trade = self._selected_trade()
        if trade is not None:
            self.app.delete_trade(trade)


# ---- タブ: 実現損益 ----

class RealizedTab(ttk.Frame):
    """実現損益。決済ごとの一覧(左)と、銘柄ごとの合計(右)。"""

    def __init__(self, master, app: "AssetApp"):
        super().__init__(master)
        self.app = app
        self.realized = []

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=4)
        ttk.Label(bar, text="年").pack(side="left")
        self.year_box = ttk.Combobox(bar, state="readonly", width=8, values=[ALL])
        self.year_box.set(ALL)
        self.year_box.pack(side="left", padx=(4, 12))
        self.year_box.bind("<<ComboboxSelected>>", lambda e: self._render())
        self.total_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.total_var).pack(side="left")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(side="top", fill="both", expand=True, padx=4)
        self.list = SortableTree(panes, REALIZED_COLUMNS, stretch="name")
        self.by_stock = SortableTree(panes, REALIZED_BY_STOCK_COLUMNS, stretch="name")
        panes.add(self.list, weight=3)
        panes.add(self.by_stock, weight=1)

    def show(self, realized) -> None:
        self.realized = realized
        years = sorted({r.date[:4] for r in realized}, reverse=True)
        self.year_box.configure(values=[ALL, *years])
        if self.year_box.get() not in (ALL, *years):
            self.year_box.set(ALL)
        self._render()

    def _render(self):
        year = self.year_box.get()
        instruments = self.app.store.instruments
        realized = [r for r in self.realized if year == ALL or r.date[:4] == year]
        rows, totals = [], {}
        for n, r in enumerate(realized):
            instrument = instruments[r.key[0]]
            display = (
                r.date, instrument.code, instrument.name, r.key[1], r.key[2], fmt_quantity(r.quantity),
                fmt_yen(r.proceeds), fmt_yen(r.cost), fmt_yen(r.profit, signed=True),
            )
            keys = (r.date, instrument.code, instrument.name, r.key[1], r.key[2], r.quantity, r.proceeds, r.cost,
                    r.profit)
            rows.append((f"r{n}", display, keys, profit_tag(r.profit)))
            count, profit = totals.get(instrument.id, (0, 0.0))
            totals[instrument.id] = (count + 1, profit + r.profit)
        rows.sort(key=lambda r: r[2][0], reverse=True)
        self.list.set_rows(rows)
        stock_rows = []
        for n, (instrument_id, (count, profit)) in enumerate(totals.items()):
            instrument = instruments[instrument_id]
            stock_rows.append((
                f"s{n}", (instrument.code, instrument.name, count, fmt_yen(profit, signed=True)),
                (instrument.code, instrument.name, count, profit), profit_tag(profit),
            ))
        stock_rows.sort(key=lambda r: -r[2][3])
        self.by_stock.set_rows(stock_rows)
        total = sum(r.profit for r in realized)
        label = "全期間" if year == ALL else f"{year} 年"
        self.total_var.set(f"{label}の実現損益(税引前): {fmt_yen(total, signed=True)} 円  決済 {len(realized)} 回")


# ---- 取引の入力 ----

class TradeDialog(tk.Toplevel):
    """取引の入力・編集のフォーム。trade を渡すと編集、preset(項目 -> 値)を渡すと初期値にする。"""

    def __init__(self, app: "AssetApp", trade: Trade | None = None, preset: dict | None = None):
        super().__init__(app)
        self.app = app
        self.editing = trade
        self.instrument: Instrument | None = None
        self.title("取引の編集" if trade else "取引の入力")
        self.transient(app.winfo_toplevel())
        self.resizable(False, False)

        ui = app.store.ui
        preset = dict(preset or {})
        if trade is not None:
            preset = {
                "date": trade.date, "time": trade.time, "instrument_id": trade.instrument_id,
                "broker": trade.broker, "account": trade.account, "action": trade.action,
                "quantity": fmt_quantity(trade.quantity) if trade.action != "split" else "",
                "price": fmt_quantity(trade.price), "fee": fmt_quantity(trade.fee), "memo": trade.memo,
            }
        self.date_var = tk.StringVar(value=preset.get("date", date.today().isoformat()))
        self.time_var = tk.StringVar(value=preset.get("time", ""))
        broker = preset.get("broker", ui.get("last_broker", ""))
        self.broker_var = tk.StringVar(value=broker if broker in app.store.brokers else "")
        if not self.broker_var.get() and app.store.brokers:
            self.broker_var.set(app.store.brokers[0])
        account = preset.get("account", ui.get("last_account", ACCOUNT_TYPES[0]))
        self.account_var = tk.StringVar(value=account if account in ACCOUNT_TYPES else ACCOUNT_TYPES[0])
        self.action_var = tk.StringVar(value=preset.get("action", "buy"))
        self.quantity_var = tk.StringVar(value=preset.get("quantity", ""))
        self.price_var = tk.StringVar(value=preset.get("price", ""))
        self.fee_var = tk.StringVar(value=preset.get("fee", "0"))
        self.memo_var = tk.StringVar(value=preset.get("memo", ""))
        self.stock_var = tk.StringVar(value="(未選択)")
        self.holding_var, self.close_var, self.amount_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.error_var = tk.StringVar()

        self._build()
        if preset.get("instrument_id") in app.store.instruments:
            self.set_instrument(app.store.instruments[preset["instrument_id"]])
        for var in (self.broker_var, self.account_var, self.action_var, self.quantity_var, self.price_var,
                    self.fee_var):
            var.trace_add("write", lambda *_: self._update_info())
        self._on_action_change()
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        if self.instrument is None:
            self.search.focus_set()
        else:
            self.quantity_entry.focus_set()

    # ---- 画面の組み立て ----

    def _build(self):
        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        row = 0

        def label(text):
            ttk.Label(form, text=text).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))

        label("約定日")
        line = ttk.Frame(form)
        line.grid(row=row, column=1, sticky="w")
        ttk.Entry(line, textvariable=self.date_var, width=12).pack(side="left")
        ttk.Label(line, text="  時刻(任意)").pack(side="left")
        ttk.Entry(line, textvariable=self.time_var, width=6).pack(side="left", padx=(4, 0))
        row += 1

        label("銘柄")
        ttk.Label(form, textvariable=self.stock_var, font=("", 10, "bold")).grid(row=row, column=1, sticky="w")
        row += 1
        self.search = StockSearchBox(
            form, self.app.index, on_pick=lambda stock: self.set_instrument(self.app.instrument_for_code(stock.code)),
            on_message=self.error_var.set, on_unmatched=self._on_unmatched,
            label="検索", button="選択", dropdown=True, entry_width=28,
        )
        self.search.grid(row=row, column=1, sticky="w")
        row += 1

        label("証券会社")
        line = ttk.Frame(form)
        line.grid(row=row, column=1, sticky="w")
        self.broker_box = ttk.Combobox(line, textvariable=self.broker_var, state="readonly", width=16,
                                       values=self.app.store.brokers)
        self.broker_box.pack(side="left")
        ttk.Button(line, text="編集…", command=self._edit_brokers).pack(side="left", padx=(4, 0))
        row += 1

        label("口座区分")
        ttk.Combobox(form, textvariable=self.account_var, state="readonly", width=16, values=ACCOUNT_TYPES).grid(
            row=row, column=1, sticky="w",
        )
        row += 1

        label("売買")
        line = ttk.Frame(form)
        line.grid(row=row, column=1, sticky="w")
        for value in ("buy", "sell", "split"):
            ttk.Radiobutton(line, text=ACTIONS[value], value=value, variable=self.action_var,
                            command=self._on_action_change).pack(side="left", padx=(0, 8))
        row += 1

        label("数量")
        line = ttk.Frame(form)
        line.grid(row=row, column=1, sticky="w")
        self.quantity_entry = ttk.Entry(line, textvariable=self.quantity_var, width=12, justify="right")
        self.quantity_entry.pack(side="left")
        self.unit_label = ttk.Label(line, text="株")
        self.unit_label.pack(side="left", padx=(4, 12))
        ttk.Label(line, textvariable=self.holding_var, foreground="gray").pack(side="left")
        row += 1

        self.price_label = ttk.Label(form, text="単価")
        self.price_label.grid(row=row, column=0, sticky="w", pady=3)
        line = ttk.Frame(form)
        line.grid(row=row, column=1, sticky="w")
        ttk.Entry(line, textvariable=self.price_var, width=12, justify="right").pack(side="left")
        self.price_unit = ttk.Label(line, text="円")
        self.price_unit.pack(side="left", padx=(4, 12))
        ttk.Label(line, textvariable=self.close_var, foreground="gray").pack(side="left")
        row += 1

        label("手数料")
        line = ttk.Frame(form)
        line.grid(row=row, column=1, sticky="w")
        self.fee_entry = ttk.Entry(line, textvariable=self.fee_var, width=12, justify="right")
        self.fee_entry.pack(side="left")
        ttk.Label(line, text="円(税込み)").pack(side="left", padx=(4, 0))
        row += 1

        label("メモ")
        ttk.Entry(form, textvariable=self.memo_var, width=40).grid(row=row, column=1, sticky="we")
        row += 1

        label("受渡金額")
        ttk.Label(form, textvariable=self.amount_var, font=("", 10, "bold")).grid(row=row, column=1, sticky="w")
        row += 1

        ttk.Label(form, textvariable=self.error_var, foreground=RED, wraplength=420, justify="left").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(6, 0),
        )
        row += 1

        buttons = ttk.Frame(form)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(10, 0))
        if self.editing is None:
            ttk.Button(buttons, text="保存して次へ", command=lambda: self._save(keep_open=True)).pack(side="left")
        ttk.Button(buttons, text="保存", command=self._save).pack(side="left", padx=(4, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=(4, 0))

    # ---- 銘柄 ----

    def set_instrument(self, instrument: Instrument) -> None:
        self.instrument = instrument
        self.stock_var.set(f"{instrument.code}  {instrument.name}")
        self.unit_label.configure(text=instrument.rule.unit_label)
        self.error_var.set("")
        self._update_info()
        self.quantity_entry.focus_set()

    def _on_unmatched(self, query: str) -> None:
        """銘柄一覧にないコード(上場廃止の銘柄など)を直接指定したとき。名称を尋ねて使う。"""
        code = unicodedata.normalize("NFKC", query).strip().upper().removesuffix(".T")
        if not re.fullmatch(r"[0-9][0-9A-Z]{3}", code):
            self.error_var.set("銘柄が見つかりません。銘柄コード(4 桁)を入力すると、一覧にない銘柄も登録できます。")
            return
        known = self.app.store.instruments.get(jp_stock_id(code))
        name = simpledialog.askstring(
            "銘柄名", f"{code} は銘柄一覧にありません。銘柄名を入力してください。",
            initialvalue=known.name if known else "", parent=self,
        )
        if name and name.strip():
            self.set_instrument(Instrument(jp_stock_id(code), AssetClass.JP_STOCK, code, name.strip()))

    # ---- 入力に合わせた表示 ----

    def _on_action_change(self):
        is_split = self.action_var.get() == "split"
        self.price_label.configure(text="分割比率" if is_split else "単価")
        self.price_unit.configure(text="倍(1株→N株のN)" if is_split else "円")
        self.quantity_entry.configure(state="disabled" if is_split else "normal")
        self.fee_entry.configure(state="disabled" if is_split else "normal")
        self._update_info()

    def _update_info(self):
        """保有数量・前日終値・受渡金額の表示を、入力に合わせて直す。"""
        instrument = self.instrument
        if instrument is None:
            self.holding_var.set("")
            self.close_var.set("")
            self.amount_var.set("")
            return
        holding = self.app.ledger.holdings.get((instrument.id, self.broker_var.get(), self.account_var.get()))
        unit = instrument.rule.unit_label
        self.holding_var.set(f"(保有: {fmt_quantity(holding.quantity) if holding else 0} {unit})")
        quote = self.app.prices.quote(instrument)
        self.close_var.set(f"(前日終値: {fmt_price(quote.close)}  {quote.date})" if quote else "(株価データなし)")
        quantity, price = parse_number(self.quantity_var.get()), parse_number(self.price_var.get())
        fee = parse_number(self.fee_var.get()) or 0
        action = self.action_var.get()
        if action == "split" or quantity is None or price is None:
            self.amount_var.set("")
            return
        amount = quantity * price * instrument.multiplier
        amount = amount + fee if action == "buy" else amount - fee
        self.amount_var.set(f"{fmt_yen(amount)} 円")

    # ---- 保存 ----

    def _edit_brokers(self):
        BrokerDialog(self.app, parent=self).wait_window()
        self.broker_box.configure(values=self.app.store.brokers)
        if self.broker_var.get() not in self.app.store.brokers:
            self.broker_var.set(self.app.store.brokers[0] if self.app.store.brokers else "")

    def _collect(self) -> Trade | list[str]:
        """入力から取引を作る。入力に誤りがあれば、その理由の一覧。"""
        errors = []
        trade_date, trade_time = parse_date(self.date_var.get()), parse_time(self.time_var.get())
        if trade_date is None:
            errors.append("約定日は 2026-11-18 の形で入力してください。")
        if trade_time is None:
            errors.append("時刻は 09:30 の形で入力してください(空でもかまいません)。")
        if self.instrument is None:
            errors.append("銘柄を選んでください。")
        action = self.action_var.get()
        is_split = action == "split"
        quantity = 0.0 if is_split else parse_number(self.quantity_var.get())
        price = parse_number(self.price_var.get())
        fee = 0.0 if is_split else parse_number(self.fee_var.get() or "0")
        if quantity is None:
            errors.append("数量を数で入力してください。")
        if price is None:
            errors.append(("分割比率" if is_split else "単価") + "を数で入力してください。")
        if fee is None:
            errors.append("手数料を数で入力してください。")
        if errors:
            return errors
        editing = self.editing
        return Trade(
            id=editing.id if editing else self.app.store.next_id(),
            date=trade_date, time=trade_time, instrument_id=self.instrument.id,
            broker=self.broker_var.get(), account=self.account_var.get(), action=action,
            quantity=quantity, price=price, fee=fee, memo=self.memo_var.get().strip(),
            source=editing.source if editing else "manual",
            import_key=editing.import_key if editing else "",
            fx_rate=editing.fx_rate if editing else None,
        )

    def _save(self, keep_open: bool = False):
        result = self._collect()
        if isinstance(result, list):
            self.error_var.set("\n".join(result))
            return
        errors = self.app.save_trade(result, self.instrument)
        if errors:
            self.error_var.set("\n".join(errors))
            return
        self.app.store.ui.update(last_broker=result.broker, last_account=result.account)
        if not keep_open:
            self.destroy()
            return
        # 続けて入力しやすいよう、約定日・証券会社・口座区分・売買は残して、他を空にする
        self.instrument = None
        self.stock_var.set("(未選択)")
        for var in (self.quantity_var, self.price_var, self.memo_var):
            var.set("")
        self.fee_var.set("0")
        self.error_var.set(f"保存しました: {result.date} {ACTIONS[result.action]}")
        self._update_info()
        self.search.focus_set()


class BrokerDialog(tk.Toplevel):
    """証券会社の一覧の編集(追加・名前変更・並べ替え・削除)。"""

    def __init__(self, app: "AssetApp", parent=None):
        super().__init__(parent or app)
        self.app = app
        self.title("証券会社の編集")
        self.transient(parent or app.winfo_toplevel())
        self.resizable(False, False)
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(frame, height=8, width=24, exportselection=False)
        self.listbox.pack(side="left", fill="both")
        buttons = ttk.Frame(frame)
        buttons.pack(side="left", fill="y", padx=(8, 0))
        for text, command in (("追加", self.add), ("名前変更", self.rename), ("↑", lambda: self.move(-1)),
                              ("↓", lambda: self.move(1)), ("削除", self.delete), ("閉じる", self.destroy)):
            ttk.Button(buttons, text=text, command=command).pack(side="top", fill="x", pady=(0, 2))
        self.bind("<Escape>", lambda e: self.destroy())
        self._refresh()
        self.grab_set()

    def _refresh(self, select: int | None = None):
        self.listbox.delete(0, "end")
        for name in self.app.store.brokers:
            self.listbox.insert("end", name)
        if select is not None and self.app.store.brokers:
            self.listbox.selection_set(min(select, len(self.app.store.brokers) - 1))

    def _selected(self) -> int | None:
        selection = self.listbox.curselection()
        return selection[0] if selection else None

    def _ask_name(self, title: str, initial: str = "") -> str | None:
        name = (simpledialog.askstring(title, "証券会社の名前", initialvalue=initial, parent=self) or "").strip()
        if not name or name == initial:
            return None
        if name in self.app.store.brokers:
            messagebox.showerror(APP_NAME, f"「{name}」は、すでにあります。", parent=self)
            return None
        return name

    def _changed(self, select: int | None):
        self._refresh(select)
        self.app.save_store()
        self.app.recompute()

    def add(self):
        name = self._ask_name("証券会社の追加")
        if name:
            self.app.store.brokers.append(name)
            self._changed(len(self.app.store.brokers) - 1)

    def rename(self):
        i = self._selected()
        if i is None:
            return
        old = self.app.store.brokers[i]
        new = self._ask_name("証券会社の名前変更", old)
        if new:
            self.app.store.rename_broker(old, new)
            self._changed(i)

    def move(self, step: int):
        i = self._selected()
        brokers = self.app.store.brokers
        if i is None or not 0 <= i + step < len(brokers):
            return
        brokers[i], brokers[i + step] = brokers[i + step], brokers[i]
        self._changed(i + step)

    def delete(self):
        i = self._selected()
        if i is None:
            return
        name = self.app.store.brokers[i]
        if self.app.store.broker_in_use(name):
            messagebox.showerror(APP_NAME, f"「{name}」の取引があるため、削除できません。", parent=self)
            return
        del self.app.store.brokers[i]
        self._changed(i)


# ---- 全体 ----

class AssetApp(ttk.Frame):
    """資産管理の画面全体。取引の記録(AssetStore)から集計・評価して、各タブに配る。"""

    def __init__(self, master, store: AssetStore):
        super().__init__(master)
        self.store = store
        self.listed = load_listed()
        self.index = stock_index(self.listed)
        self.prices = PriceBook()
        self.ledger = build_ledger([], {})
        self.valuations: list[Valuation] = []
        self.problem_ids: set[int] = set()
        ui = store.ui

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=6, pady=6)
        self.broker_var = tk.StringVar(value=ui.get("broker") or ALL)
        self.account_var = tk.StringVar(value=ui.get("account") or ALL)
        ttk.Label(bar, text="証券会社").pack(side="left")
        self.broker_box = ttk.Combobox(bar, textvariable=self.broker_var, state="readonly", width=14)
        self.broker_box.pack(side="left", padx=(4, 12))
        ttk.Label(bar, text="口座区分").pack(side="left")
        ttk.Combobox(bar, textvariable=self.account_var, state="readonly", width=12,
                     values=[ALL, *ACCOUNT_TYPES]).pack(side="left", padx=(4, 12))
        for var in (self.broker_var, self.account_var):
            var.trace_add("write", lambda *_: self.refresh())
        self.japan_color_var = tk.BooleanVar(value=bool(ui.get("japan_color", True)))
        ttk.Checkbutton(bar, text="利益を赤で表示(日本式)", variable=self.japan_color_var,
                        command=self.refresh).pack(side="left")
        ttk.Button(bar, text="取引を入力", command=self.open_trade_dialog).pack(side="right")
        ttk.Button(bar, text="証券会社の編集", command=lambda: BrokerDialog(self)).pack(side="right", padx=(0, 4))
        ttk.Button(bar, text="株価を読み直す", command=self.reload_prices).pack(side="right", padx=(0, 4))

        # 合計とお知らせ(下)
        footer = ttk.Frame(self)
        footer.pack(side="bottom", fill="x", padx=8, pady=6)
        self.total_var, self.message_var = tk.StringVar(), tk.StringVar()
        ttk.Label(footer, textvariable=self.total_var, font=("", 11, "bold")).pack(side="top", anchor="w")
        ttk.Label(footer, textvariable=self.message_var, wraplength=1200, justify="left").pack(side="top", anchor="w")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(side="top", fill="both", expand=True, padx=6)
        self.holdings_tab = HoldingsTab(self.notebook, self)
        self.trades_tab = TradesTab(self.notebook, self)
        self.realized_tab = RealizedTab(self.notebook, self)
        self.notebook.add(self.holdings_tab, text="保有一覧")
        self.notebook.add(self.trades_tab, text="取引履歴")
        self.notebook.add(self.realized_tab, text="実現損益")
        tab = ui.get("tab")
        if isinstance(tab, int) and 0 <= tab < len(self.notebook.tabs()):
            self.notebook.select(tab)

        self.recompute()
        self._take_snapshot()

    # ---- 集計と表示 ----

    def recompute(self) -> None:
        """取引の全件から集計・評価し直して、表示を作り直す。"""
        self.ledger = build_ledger(self.store.trades, self.store.instruments)
        self.problem_ids = {p.trade_id for p in self.ledger.problems}
        self.valuations = value_all(self.ledger, self.store.instruments, self.prices)
        brokers = [ALL, *self.store.brokers]
        self.broker_box.configure(values=brokers)
        if self.broker_var.get() not in brokers:
            self.broker_var.set(ALL)  # trace で refresh が呼ばれる
        self.refresh()

    def _matches(self, broker: str, account: str) -> bool:
        """絞り込みの条件に合うか。"""
        b, a = self.broker_var.get(), self.account_var.get()
        return (b == ALL or broker == b) and (a == ALL or account == a)

    def refresh(self) -> None:
        """絞り込みに合わせて、各タブと合計を表示し直す。"""
        plus, minus = (RED, GREEN) if self.japan_color_var.get() else (GREEN, RED)
        for tab_list in (self.holdings_tab.list, self.trades_tab.list, self.realized_tab.list,
                         self.realized_tab.by_stock):
            tab_list.set_colors(plus, minus)
        valuations = [v for v in self.valuations if self._matches(v.holding.broker, v.holding.account)]
        realized = [r for r in self.ledger.realized if self._matches(r.key[1], r.key[2])]
        trades = [t for t in self.store.trades if self._matches(t.broker, t.account)]
        self.holdings_tab.show(valuations)
        self.trades_tab.show(trades)
        self.realized_tab.show(realized)

        summary = summarize(valuations, realized, date.today().isoformat())
        self.total_var.set(
            f"評価額 {fmt_yen(summary.value)} 円    "
            f"含み損益 {fmt_yen(summary.unrealized, signed=True)} 円({fmt_pct(summary.unrealized_pct)})    "
            f"実現損益 今年 {fmt_yen(summary.realized_ytd, signed=True)} 円 / "
            f"累計 {fmt_yen(summary.realized_total, signed=True)} 円",
        )
        messages = []
        if summary.price_date:
            messages.append(f"価格: {summary.price_date} の終値")
        if summary.missing:
            codes = "、".join(v.instrument.code for v in summary.missing[:5])
            messages.append(f"※ {len(summary.missing)} 銘柄は株価データがないため、合計に入れていません({codes}"
                            f"{' ほか' if len(summary.missing) > 5 else ''})。python core_gather.py で取得してください")
        if self.ledger.problems:
            messages.append(f"※ 集計できない取引が {len(self.ledger.problems)} 件あります(取引履歴で黄色の行)。"
                            f" {self.ledger.problems[0].message}")
        if not self.store.trades:
            messages.append("「取引を入力」ボタンで、保有している銘柄の買いを入力してください。")
        self.set_message("    ".join(messages))

    def set_message(self, text: str) -> None:
        self.message_var.set(text)

    def reload_prices(self) -> None:
        """株価データを読み直す(起動したまま core_gather.py で株価を更新したとき)。"""
        self.prices.clear()
        self.recompute()

    def _take_snapshot(self):
        """今日の資産の合計(絞り込みなし)を記録する。1 日 1 回。"""
        today = date.today().isoformat()
        summary = summarize(self.valuations, self.ledger.realized, today)
        if take_snapshot_if_needed(self.store, summary, today):
            self.save_store()

    # ---- 保存 ----

    def save_store(self) -> bool:
        try:
            self.store.save()
        except OSError as e:
            messagebox.showerror(APP_NAME, f"{self.store.path.name} に保存できません。\n{e}", parent=self)
            return False
        return True

    def save_ui(self) -> bool:
        """画面の状態を保存する(ウィンドウを閉じるとき)。"""
        self.store.ui.update(
            tab=self.notebook.index(self.notebook.select()),
            broker=self.broker_var.get(), account=self.account_var.get(),
            merge=self.holdings_tab.merge_var.get(), japan_color=self.japan_color_var.get(),
        )
        return self.save_store()

    # ---- 取引 ----

    def instrument_for_code(self, code: str) -> Instrument:
        """日本株の商品を、銘柄一覧の名称・市場・33 業種から作る(銘柄一覧になければ、登録済みのもの)。"""
        if code in self.listed.index:
            row = self.listed.loc[code]

            def value(column):
                v = row.get(column, "")
                return v if isinstance(v, str) and v != "-" else ""

            return Instrument(
                jp_stock_id(code), AssetClass.JP_STOCK, code, value("銘柄名"),
                market=value("市場・商品区分").split("（")[0], sector=value("33業種区分"),
            )
        known = self.store.instruments.get(jp_stock_id(code))
        return known or Instrument(jp_stock_id(code), AssetClass.JP_STOCK, code, "")

    def open_trade_dialog(self, trade: Trade | None = None, preset: dict | None = None) -> None:
        if not self.store.brokers:
            messagebox.showinfo(APP_NAME, "先に証券会社を登録してください。", parent=self)
            BrokerDialog(self).wait_window()
            if not self.store.brokers:
                return
        TradeDialog(self, trade=trade, preset=preset)

    def save_trade(self, trade: Trade, instrument: Instrument) -> list[str]:
        """取引を確かめて保存する。保存できなければ、その理由の一覧を返す。"""
        instruments = {**self.store.instruments, instrument.id: instrument}
        errors = validate_trade(trade, self.store.trades, instruments)
        if errors:
            return errors
        self.store.ensure_instrument(instrument)
        self.store.put_trade(trade)
        if not self.save_store():
            return ["保存できませんでした。"]
        self.recompute()
        return []

    def delete_trade(self, trade: Trade) -> None:
        after = [t for t in self.store.trades if t.id != trade.id]
        problems = changed_problems(self.store.trades, after, self.store.instruments)
        if problems:
            messagebox.showerror(APP_NAME, "この取引を削除すると、集計が合わなくなります。\n\n" + "\n".join(problems),
                                 parent=self)
            return
        instrument = self.store.instruments.get(trade.instrument_id)
        name = f"{instrument.code} {instrument.name}" if instrument else trade.instrument_id
        if not messagebox.askyesno(
            APP_NAME, f"{trade.date} {name} の{ACTIONS[trade.action]}を削除しますか?", parent=self,
        ):
            return
        self.store.delete_trade(trade.id)
        self.save_store()
        self.recompute()

    # ---- チャート ----

    def open_chart(self, instrument_id: str, key: HoldingKey | None = None) -> None:
        """銘柄のチャートを別のウィンドウで開き、自分の売買を重ねる(key を渡すと、その保有の売買だけ)。"""
        instrument = self.store.instruments[instrument_id]
        if instrument.asset_class != AssetClass.JP_STOCK:
            self.set_message("この資産クラスのチャートは未対応です。")
            return
        if not csv_path_for(instrument.code).exists():
            self.set_message(f"{instrument.code} {instrument.name} の株価データがありません。"
                             f"先に python core_gather.py {instrument.code} を実行してください。")
            return
        window = tk.Toplevel(self)
        window.title(f"{instrument.code} {instrument.name}")
        window.geometry("1200x800")
        chart = ChartFrame(window, instrument.code, searchable=False)
        chart.pack(fill="both", expand=True)
        trades = [t for t in self.store.trades if t.instrument_id == instrument_id and (key is None or t.key == key)]
        chart.set_trades(trade_marks(trades))


def trade_marks(trades: list[Trade]) -> list[TradeMark]:
    """チャートに重ねる売買の印を作る(表示のためだけのもの)。

    買いごとに 1 つの印にし、売りは先に買った分から順に割り当てる(先入れ先出し)。
    全部売った買いには、売りの日付と平均の売値を付ける。
    チャートの株価は分割に合わせて過去の値が直されているので、分割より前の売買の単価も分割比率で割る。
    """
    trades = sorted(trades, key=lambda t: t.order)
    splits = {}  # 分割の日 -> 比率(同じ銘柄の分割は、保有ごとに入力されていても 1 回と数える)
    for t in trades:
        if t.action == "split" and t.price > 0:
            splits[t.date] = t.price

    def factor(trade: Trade) -> float:
        return math.prod(r for d, r in splits.items() if d > trade.date)

    marks: list[TradeMark] = []
    for key in dict.fromkeys(t.key for t in trades):
        lots = []  # [買いの日、単価、残りの数量、売った数量、売った金額、最後に売った日]
        for t in trades:
            if t.key != key or t.action not in ("buy", "sell"):
                continue
            f = factor(t)
            price, quantity = t.price / f, t.quantity * f
            if t.action == "buy":
                lots.append([t.date, price, quantity, 0.0, 0.0, None])
                continue
            for lot in lots:
                if quantity <= 0:
                    break
                used = min(lot[2], quantity)
                if used <= 0:
                    continue
                lot[2] -= used
                lot[3] += used
                lot[4] += used * price
                lot[5] = t.date
                quantity -= used
        for entry, price, remaining, sold, amount, exit_date in lots:
            closed = remaining <= 1e-9 and sold > 0
            marks.append(TradeMark(
                pd.Timestamp(entry), price,
                pd.Timestamp(exit_date) if closed else None,
                amount / sold if closed else None,
            ))
    return marks


def main() -> int:
    root = tk.Tk()
    root.title("ToreCan asset")
    root.geometry("1400x820")
    store = AssetStore()
    try:
        store.load()
    except OSError as e:
        messagebox.showerror(APP_NAME, f"{ASSET_PATH.name} を読み込めません。\n{e}", parent=root)
        return 1
    except ValueError as e:
        # 壊れたファイルを上書きして失わないよう、名前を変えて残しておく
        backup = ASSET_PATH.with_name(ASSET_PATH.name + ".bak")
        os.replace(ASSET_PATH, backup)
        messagebox.showwarning(
            APP_NAME,
            f"{ASSET_PATH.name} が壊れているため、読み込めません。\n{e}\n\n"
            f"壊れたファイルを {backup.name} に残し、空の状態で起動します。",
            parent=root,
        )
        store = AssetStore()
    try:
        app = AssetApp(root, store)
    except FileNotFoundError as e:
        messagebox.showerror(APP_NAME, str(e), parent=root)
        return 1
    app.pack(fill="both", expand=True)

    def on_close():
        if app.save_ui() or messagebox.askyesno(APP_NAME, "保存せずに終了しますか?", parent=root):
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
