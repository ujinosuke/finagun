"""資産管理プログラム。取引を手入力して、保有・評価額・含み損益・実現損益を確かめる。

設計は fina_asset_design.md・fina_asset_design_phase2.md、仕様は fina_asset_spec.md を参照。
いまは Phase 2(日本株現物。CSV 取り込み・集計グラフ・配当・ヒートマップ)。

取引(約定日・銘柄・証券会社・口座区分・売買・数量・単価・手数料)を記録すると、
保有は (銘柄 × 証券会社 × 口座区分) ごとに自動で集計される。平均取得単価は移動平均法。
評価は前日終値ベースで、stock_database フォルダの保存済み日足データの終値を使う
(株価は取得しないので、先に core_gather.py で株価を取得しておくこと)。
税は反映しない(実現損益は税引前)。現金(預り金)は管理しない。

記録はルートディレクトリの asset.json に保存し、次回の起動時に自動で読み込む。
起動した日の資産の合計を、1 日 1 回 asset.json に記録する(集計タブの資産推移のグラフに使う)。
配当情報は yfinance から取得し、dividend_cache.json にキャッシュする(保有銘柄とウォッチリストの銘柄だけ)。

使い方:
    python fina_asset.py

操作:
    取引の入力    「取引を入力」ボタン。銘柄は、銘柄コードまたは銘柄名の一部を入力して候補から選ぶ。
                  前から持っている株は「入庫」で数量と取得単価を入れる
    CSV 取り込み  「CSV 取り込み」ボタン。楽天証券の「保有商品一覧」CSV なら、国内株式の保有を
                  基準日の入庫として取り込む。取り込む前に一覧で確かめ、行ごとに外せる
    絞り込み      上の欄で証券会社・口座区分を選ぶ(すべてのタブと合計に効く)
    保有一覧      ダブルクリック・「チャート」ボタンでチャートを開く(自分の売買を ▲▼ で表示)。
                  右クリック・ボタンで、その保有の買い増し・売却・移管の入力、ウォッチリストへの追加。
                  「銘柄ごとにまとめる」で、証券会社・口座をまたいで合算する
    取引履歴      ダブルクリックで編集、Delete キー・「削除」ボタンで削除
    実現損益      年を選ぶと、その年の決済と銘柄ごとの合計を表示する
    集計          資産推移(評価額と取得原価)と、配分(銘柄・業種・証券会社など)のグラフ
    配当          保有銘柄の 1 株配当・受取見込み・権利落ち日(参考表示)。「配当情報を更新」で取得する
    ヒートマップ  四角の大きさ = 評価額、色 = 前日比など。ダブルクリックでチャートを開く
    並べ替え      一覧の見出しをクリック(もう一度押すと逆順)
"""

import math
import os
import queue
import re
import shutil
import sys
import threading
import time
import tkinter as tk
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pandas as pd
from matplotlib import dates as matplotlib_dates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from core_asset import (
    ACCOUNT_TYPES, ACTIONS, ASSET_PATH, TRANSFERS, AssetClass, AssetStore, HoldingKey, Instrument, Trade,
    build_ledger, changed_problems, jp_stock_id, jp_stock_instrument, validate_trade,
)
from core_asset_import import STATUSES, ImportContext, check_problems, classify, commit, read_csv, status_counts
from core_asset_value import (
    ALLOCATION_KINDS, PriceBook, Valuation, allocation, group_key, period_change, rebuild_history, summarize,
    record_snapshot, value_all,
)
from core_dividend import FETCH_INTERVAL, DividendCache, fetch_dividend
from core_gather import csv_path_for
from core_stocks import load_listed, normalize, stock_index
from core_watchlist import watched_codes
from gui_chart import ChartFrame, TradeMark  # gui_chart を読み込むと、matplotlib の日本語フォントも設定される
from gui_treemap import TreemapCanvas, TreemapItem
from gui_widgets import SortableTree, StockSearchBox, add_to_watchlist

APP_NAME = "fina_asset"
ALL = "すべて"  # 絞り込みで「すべて」を表す項目

# 損益の色(日本式: 利益は赤、損失は緑。設定で反転できる)
RED = "#d32f2f"
GREEN = "#2e7d32"

# グラフの色(dataviz の既定の配色。評価額は系列 1 の青、取得原価は補助の灰色)
SERIES_BLUE = "#2a78d6"
SERIES_BLUE_LIGHT = "#86b6ef"   # 取引から計算し直した過去の評価額
COST_GRAY = "#8a8984"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID_COLOR = "#e6e5e1"
ALLOCATION_TOP = 10   # 配分のグラフに出す項目数(残りは「その他」)

# 集計タブの期間(表示名 -> 日数。None は全期間)
PERIODS = {"1か月": 31, "3か月": 92, "1年": 366, "全期間": None}

# ヒートマップの色の指標(表示名 -> (種類、色が一番濃くなる値 %))
HEATMAP_METRICS = {
    "前日比": ("change", 3.0),
    "含み損益率": ("unrealized", 30.0),
    "1週": ("week", 5.0),
    "1月": ("month", 10.0),
    "年初来": ("ytd", 30.0),
}
HEATMAP_GROUPS = {"33業種": "sector", "資産クラス": "asset_class", "証券会社": "broker", "なし": ""}

# CSV 取り込みの状態の色(行の背景)
IMPORT_STATUS_COLORS = {
    "maybe_duplicate": "#fff3cd", "exists": "#fff3cd", "duplicate": "#efeeeb", "skip": "#efeeeb",
    "error": "#fde2e1",
}

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
SOURCE_LABELS = {"manual": "手入力", "csv:rakuten_holdings": "楽天CSV", "csv:rakuten_trades": "楽天CSV"}
# 取引入力の売買の選択肢(並べる順)
DIALOG_ACTIONS = ("buy", "sell", "split", "transfer_in", "transfer_out")


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
        for text, command in (("ウォッチリストに追加", self.add_to_watchlist), ("移管", self.transfer),
                              ("売却", self.sell), ("買い増し", self.buy), ("チャート", self.open_chart)):
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
        self.menu.add_command(label="移管を入力…", command=self.transfer)
        self.menu.add_separator()
        self.menu.add_command(label="ウォッチリストに追加…", command=self.add_to_watchlist)
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

    def transfer(self) -> None:
        """選んだ保有の移管(別の証券会社・口座へ移す)を入力する。"""
        target = self._target()
        if target is None:
            return
        if target[1] is None:
            self.app.set_message("移管は、証券会社・口座ごとの行で選んでください(「銘柄ごとにまとめる」を外す)。")
            return
        TransferDialog(self.app, target[1])

    def add_to_watchlist(self) -> None:
        """選んだ銘柄(選んでいなければ一覧の全銘柄)を、ウォッチリストのグループに足す。"""
        selected = self.list.selected()
        iids = [selected] if selected else list(self.targets)
        instruments = self.app.store.instruments
        codes = list(dict.fromkeys(
            instruments[self.targets[i][0]].code for i in iids
            if instruments[self.targets[i][0]].asset_class == AssetClass.JP_STOCK
        ))
        message = add_to_watchlist(self, codes, APP_NAME)
        if message:
            self.app.set_message(message)

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
            if is_split:
                price = f"1→{fmt_quantity(t.price)}"
            elif t.action == "transfer_out":
                price = ""  # 出庫は平均単価で取り崩すので、単価を使わない
            else:
                price = fmt_price(t.price)
            display = (
                f"{t.date} {t.time}".strip(), code, name, t.broker, t.account, ACTIONS.get(t.action, t.action),
                "" if is_split else fmt_quantity(t.quantity), price,
                "" if is_split or t.action in TRANSFERS else fmt_yen(t.fee),
                fmt_yen(amount) if amount is not None else "",
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
        for value in DIALOG_ACTIONS:
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
        self.price_entry = ttk.Entry(line, textvariable=self.price_var, width=12, justify="right")
        self.price_entry.pack(side="left")
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
        action = self.action_var.get()
        is_split = action == "split"
        labels = {"split": ("分割比率", "倍(1株→N株のN)"), "transfer_in": ("取得単価", "円(証券会社の平均取得単価)"),
                  "transfer_out": ("単価", "円(使いません。平均取得単価で取り崩します)")}
        label, unit = labels.get(action, ("単価", "円"))
        self.price_label.configure(text=label)
        self.price_unit.configure(text=unit)
        self.quantity_entry.configure(state="disabled" if is_split else "normal")
        self.price_entry.configure(state="disabled" if action == "transfer_out" else "normal")
        self.fee_entry.configure(state="disabled" if is_split or action in TRANSFERS else "normal")
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
        if action not in ("buy", "sell") or quantity is None or price is None:
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
        price = 0.0 if action == "transfer_out" else parse_number(self.price_var.get())
        fee = 0.0 if is_split or action in TRANSFERS else parse_number(self.fee_var.get() or "0")
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


# ---- タブ: 集計(資産推移・配分) ----

def fmt_man(value: float, _pos=None) -> str:
    """グラフの軸の金額(万円)。"""
    return f"{value / 10000:,.0f}万"


def style_axes(ax) -> None:
    """軸を控えめにする(横の補助線だけ、上と右の枠なし)。"""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID_COLOR)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.8)
    ax.set_axisbelow(True)


class SummaryTab(ttk.Frame):
    """資産推移(評価額と取得原価)と、評価額の配分のグラフ。"""

    def __init__(self, master, app: "AssetApp"):
        super().__init__(master)
        self.app = app
        self.dirty = True
        self.valuations: list[Valuation] = []
        self.history_cache: dict[str, pd.DataFrame] = {}  # 絞り込み -> 取引から計算した過去の推移
        ui = app.store.ui

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=4)
        ttk.Label(bar, text="期間").pack(side="left")
        period = ui.get("summary_period")
        self.period_var = tk.StringVar(value=period if period in PERIODS else "1年")
        for name in PERIODS:
            ttk.Radiobutton(bar, text=name, value=name, variable=self.period_var, command=self._draw).pack(
                side="left", padx=(4, 0))
        self.rebuild_var = tk.BooleanVar(value=bool(ui.get("rebuild_history", True)))
        ttk.Checkbutton(bar, text="取引から過去の推移を補う", variable=self.rebuild_var, command=self._draw).pack(
            side="left", padx=(16, 0))
        kind = ui.get("allocation")
        self.kind_var = tk.StringVar(value=ALLOCATION_KINDS.get(kind, ALLOCATION_KINDS["sector"]))
        box = ttk.Combobox(bar, textvariable=self.kind_var, values=list(ALLOCATION_KINDS.values()),
                           state="readonly", width=10)
        box.pack(side="right")
        box.bind("<<ComboboxSelected>>", lambda e: self._draw())
        ttk.Label(bar, text="配分").pack(side="right", padx=(0, 4))

        self.figure = Figure(figsize=(10, 5), dpi=100, layout="constrained")
        self.ax_trend, self.ax_alloc = self.figure.subplots(1, 2, width_ratios=[3, 2])
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

    def show(self, valuations: list[Valuation]) -> None:
        self.valuations = valuations
        self._draw()

    def ui_state(self) -> dict:
        kind = next((k for k, v in ALLOCATION_KINDS.items() if v == self.kind_var.get()), "sector")
        return {"summary_period": self.period_var.get(), "allocation": kind,
                "rebuild_history": self.rebuild_var.get()}

    # ---- 資産推移 ----

    def _recorded(self) -> pd.DataFrame:
        """記録(スナップショット)の評価額と取得原価。絞り込み中は、内訳のある日だけ。"""
        filtered = bool(self.app.filter_label())
        rows = {}
        for s in self.app.store.snapshots:
            if not filtered:
                rows[pd.Timestamp(s.date)] = (s.value, s.cost)
            elif s.by_group:
                parts = [v for k, v in s.by_group.items() if self.app.matches_key(("", *k.split("|", 1)))]
                rows[pd.Timestamp(s.date)] = (sum(p[0] for p in parts), sum(p[1] for p in parts))
        return pd.DataFrame.from_dict(rows, orient="index", columns=["value", "cost"]).sort_index()

    def _rebuilt(self) -> pd.DataFrame:
        """取引から計算した過去の推移(絞り込みごとに覚えておく)。"""
        label = self.app.filter_label()
        if label not in self.history_cache:
            self.history_cache[label] = rebuild_history(
                self.app.store.trades, self.app.store.instruments, self.app.prices, self.app.matches_key,
            )
        return self.history_cache[label]

    def _draw_trend(self):
        ax = self.ax_trend
        ax.clear()
        style_axes(ax)
        recorded = self._recorded()
        rebuilt = self._rebuilt() if self.rebuild_var.get() else pd.DataFrame(columns=["value", "cost"])
        if len(recorded) and len(rebuilt):
            rebuilt = rebuilt[rebuilt.index < recorded.index[0]]  # 記録のある日は記録を使う
        days = PERIODS[self.period_var.get()]
        if days is not None:
            start = pd.Timestamp(date.today() - timedelta(days=days))
            recorded, rebuilt = recorded[recorded.index >= start], rebuilt[rebuilt.index >= start]
        title = "資産推移" + (f"({self.app.filter_label()})" if self.app.filter_label() else "")
        ax.set_title(title, loc="left", fontsize=11, color=TEXT_PRIMARY)
        if not len(recorded) and not len(rebuilt):
            ax.text(0.5, 0.5, "この期間の記録がありません", transform=ax.transAxes, ha="center", va="center",
                    color=TEXT_SECONDARY)
            ax.set_xticks([])
            ax.set_yticks([])
            return
        cost = pd.concat([rebuilt["cost"], recorded["cost"]])
        ax.plot(cost.index, cost.to_numpy(dtype=float), color=COST_GRAY, linewidth=1.5, linestyle="--",
                label="取得原価")
        if len(rebuilt):
            ax.plot(rebuilt.index, rebuilt["value"].to_numpy(dtype=float), color=SERIES_BLUE_LIGHT, linewidth=2,
                    label="評価額(取引から計算)")
        if len(recorded):
            # 記録が少ないうちは点が見えるように、印を付ける
            marker = "o" if len(recorded) <= 3 else None
            ax.plot(recorded.index, recorded["value"].to_numpy(dtype=float), color=SERIES_BLUE, linewidth=2,
                    marker=marker, markersize=6, label="評価額(記録)")
        ax.yaxis.set_major_formatter(FuncFormatter(fmt_man))
        locator = matplotlib_dates.AutoDateLocator()
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(matplotlib_dates.ConciseDateFormatter(locator))
        ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY)

    # ---- 配分 ----

    def _draw_allocation(self):
        ax = self.ax_alloc
        ax.clear()
        style_axes(ax)
        ax.grid(False)
        kind = self.ui_state()["allocation"]
        ax.set_title(f"配分({ALLOCATION_KINDS[kind]}・評価額)", loc="left", fontsize=11, color=TEXT_PRIMARY)
        items = allocation(self.valuations, kind)
        total = sum(v for _, v in items)
        if not items or total <= 0:
            ax.text(0.5, 0.5, "保有がありません", transform=ax.transAxes, ha="center", va="center",
                    color=TEXT_SECONDARY)
            ax.set_xticks([])
            ax.set_yticks([])
            return
        if len(items) > ALLOCATION_TOP:
            rest = sum(v for _, v in items[ALLOCATION_TOP - 1:])
            items = [*items[:ALLOCATION_TOP - 1], ("その他", rest)]
        labels = [(label if len(label) <= 14 else label[:13] + "…") for label, _ in items]
        values = [v for _, v in items]
        positions = range(len(items))
        bars = ax.barh(positions, values, color=SERIES_BLUE, height=0.6)
        ax.set_yticks(positions, labels)
        # 大きい順に上から並べる。項目が少なくても棒が太くなりすぎないよう、最低 6 項目分の高さにする
        ax.set_ylim(max(len(items), 6) - 0.5, -0.5)
        for bar, value in zip(bars, values):
            ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2, f"  {value / total * 100:.1f}%",
                    va="center", ha="left", fontsize=9, color=TEXT_SECONDARY)
        ax.set_xlim(0, max(values) * 1.25)
        ax.xaxis.set_major_formatter(FuncFormatter(fmt_man))
        ax.spines["bottom"].set_visible(False)
        ax.tick_params(axis="x", labelbottom=False, length=0)
        ax.tick_params(axis="y", length=0)

    def _draw(self):
        if self.app.nametowidget(self.app.notebook.select()) is not self:
            self.dirty = True  # 見えないときは、次に開いたときに描く
            return
        self._draw_trend()
        self._draw_allocation()
        self.canvas.draw_idle()


# ---- タブ: 配当(参考表示) ----

DIVIDEND_COLUMNS = {
    "code": ("コード", 60, "center"),
    "name": ("銘柄名", 170, "w"),
    "quantity": ("保有数", 70, "e"),
    "per_share": ("1株配当(年)", 90, "e"),
    "expected": ("年間受取見込", 100, "e"),
    "yield": ("利回り", 64, "e"),
    "yield_cost": ("取得利回り", 74, "e"),
    "ex_date": ("次回権利落ち日", 110, "center"),
    "last_cum": ("権利付最終日(目安)", 120, "center"),
    "pay_date": ("支払日", 90, "center"),
}
MONTH_COLUMNS = {
    "month": ("権利落ちの月", 90, "center"),
    "amount": ("受取見込", 100, "e"),
    "stocks": ("銘柄", 400, "w"),
}


class DividendTab(ttk.Frame):
    """保有銘柄の配当の参考表示。配当情報は yfinance から別のスレッドで取得し、dividend_cache.json に覚えておく。"""

    def __init__(self, master, app: "AssetApp"):
        super().__init__(master)
        self.app = app
        self.dirty = True
        self.valuations: list[Valuation] = []
        self.cache = DividendCache()
        self.cache.load()
        self.queue: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.started = False  # タブを開いたときの自動の取得を、もう始めたか

        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=4, pady=4)
        self.total_var, self.status_var = tk.StringVar(), tk.StringVar()
        ttk.Label(top, textvariable=self.total_var, font=("", 10, "bold")).pack(side="top", anchor="w")
        line = ttk.Frame(top)
        line.pack(side="top", fill="x", pady=(4, 0))
        self.update_button = ttk.Button(line, text="配当情報を更新", command=lambda: self.start_fetch(refresh_days=1))
        self.update_button.pack(side="left")
        ttk.Label(line, textvariable=self.status_var, foreground=TEXT_SECONDARY).pack(side="left", padx=(8, 0))

        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(side="top", fill="both", expand=True, padx=4)
        self.list = SortableTree(panes, DIVIDEND_COLUMNS, stretch="name", height=12)
        self.months = SortableTree(panes, MONTH_COLUMNS, stretch="stocks", height=8)
        panes.add(self.list, weight=3)
        panes.add(self.months, weight=2)
        ttk.Label(self, text="※ 配当は yfinance の情報による参考表示です(税引前)。今後の配当は、発表済みの次回分のほかは"
                             "去年の同じ時期の配当から推定しています(推定)。権利付最終日は土日だけを除いた目安です。",
                  foreground=TEXT_SECONDARY, wraplength=1200, justify="left").pack(side="bottom", fill="x",
                                                                                  padx=6, pady=4)

    def show(self, valuations: list[Valuation]) -> None:
        self.valuations = valuations
        self._render()
        if not self.started:
            self.started = True
            self.start_fetch()

    # ---- 取得 ----

    def _codes(self) -> list[str]:
        """配当情報を取得する銘柄(保有している日本株 + ウォッチリストの銘柄)。"""
        held = [v.instrument.code for v in self.app.valuations if v.instrument.asset_class == AssetClass.JP_STOCK]
        return list(dict.fromkeys([*held, *watched_codes()]))

    def start_fetch(self, refresh_days: int | None = None) -> None:
        """古い配当情報を、別のスレッドで取得し直す。"""
        if self.running:
            return
        codes = self.cache.stale_codes(self._codes(), *(() if refresh_days is None else (refresh_days,)))
        if not codes:
            self._set_status()
            return
        self.running = True
        self.cancel.clear()
        self.update_button.configure(state="disabled")
        self.fetch_total, self.fetch_done = len(codes), 0
        self.status_var.set(f"配当情報を取得中… 0 / {len(codes)}")
        threading.Thread(target=self._fetch_worker, args=(codes,), daemon=True).start()
        self.after(200, self._poll)

    def _fetch_worker(self, codes: list[str]) -> None:
        """(別のスレッド)1 銘柄ずつ間をあけて取得し、結果を queue に入れる。最後に None を入れる。"""
        for n, code in enumerate(codes):
            if self.cancel.is_set():
                break
            if n:
                time.sleep(FETCH_INTERVAL)
            self.queue.put(fetch_dividend(code))
        self.queue.put(None)

    def _poll(self):
        finished = False
        while True:
            try:
                info = self.queue.get_nowait()
            except queue.Empty:
                break
            if info is None:
                finished = True
                break
            self.cache.items[info.code] = info
            self.fetch_done += 1
        if not finished:
            self.status_var.set(f"配当情報を取得中… {self.fetch_done} / {self.fetch_total}")
            self.after(200, self._poll)
            return
        self.running = False
        self.update_button.configure(state="normal")
        try:
            self.cache.save()
        except OSError as e:
            self.status_var.set(f"{self.cache.path.name} に保存できません: {e}")
            return
        self._render()

    def stop(self) -> None:
        """取得を止める(ウィンドウを閉じるとき)。"""
        self.cancel.set()

    def _set_status(self):
        last = self.cache.last_fetched()
        errors = [c for c in self._codes() if (i := self.cache.get(c)) is not None and i.error]
        text = f"最終更新 {last.replace('T', ' ')[:16]}" if last else "まだ取得していません"
        if errors:
            text += f"   ※ {len(errors)} 銘柄は取得できませんでした({'、'.join(errors[:5])})"
        self.status_var.set(text)

    # ---- 表示 ----

    def _render(self):
        if not self.running:
            self._set_status()
        today = date.today().isoformat()
        merged: dict[str, list] = {}  # 商品 ID -> [商品、数量、取得原価、評価額、終値]
        for v in self.valuations:
            if v.instrument.asset_class != AssetClass.JP_STOCK:
                continue
            item = merged.setdefault(v.instrument.id, [v.instrument, 0.0, 0.0, 0.0, None])
            item[1] += v.holding.quantity
            item[2] += v.cost_jpy
            item[3] += v.value_jpy or 0.0
            item[4] = v.quote.close if v.quote else None
        rows, months, total, total_cost, total_value = [], {}, 0.0, 0.0, 0.0
        for n, (instrument, quantity, cost, value, close) in enumerate(merged.values()):
            info = self.cache.get(instrument.code)
            schedule = info.schedule(today) if info else []
            per_share = sum(s.amount for s in schedule) if schedule else (info.trailing_annual(today) if info else 0.0)
            expected = per_share * quantity
            total += expected
            total_cost += cost
            total_value += value
            yield_pct = per_share / close * 100 if close and per_share else None
            yield_cost = per_share / (cost / quantity) * 100 if quantity and cost and per_share else None
            upcoming = schedule[0] if schedule else None
            ex_text = (upcoming.ex_date + ("" if upcoming.confirmed else "(推定)")) if upcoming else "―"
            pay = info.next_pay_date if info and upcoming and upcoming.confirmed and info.next_pay_date else "―"
            if info is None:
                ex_text = "取得中" if self.running else "未取得"
            elif info.error:
                ex_text = "取得できません"
            display = (
                instrument.code, instrument.name, fmt_quantity(quantity), fmt_price(per_share) if per_share else "―",
                fmt_yen(expected) if expected else "―", fmt_pct(yield_pct).lstrip("+") if yield_pct else "―",
                fmt_pct(yield_cost).lstrip("+") if yield_cost else "―", ex_text,
                upcoming.last_cum_date if upcoming else "―", pay,
            )
            keys = (instrument.code, instrument.name, quantity, per_share, expected, yield_pct, yield_cost,
                    upcoming.ex_date if upcoming else None, upcoming.last_cum_date if upcoming else None, pay)
            rows.append((f"d{n}", display, keys, ()))
            for s in schedule:
                month = s.ex_date[:7]
                entry = months.setdefault(month, [0.0, []])
                entry[0] += s.amount * quantity
                entry[1].append(f"{instrument.name or instrument.code}{'' if s.confirmed else '(推定)'}")
        rows.sort(key=lambda r: -(r[2][4] or 0))
        self.list.set_rows(rows)

        # 今月から 12 か月分を並べる(配当のない月も出す)
        start = date.today().replace(day=1)
        month_rows = []
        for i in range(12):
            year, month = start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1
            key = f"{year}-{month:02d}"
            amount, names = months.get(key, [0.0, []])
            month_rows.append((f"m{i}", (f"{year}年{month}月", fmt_yen(amount) if amount else "―", "、".join(names)),
                               (key, amount, len(names)), ()))
        self.months.set_rows(month_rows)
        on_cost = f"取得額に対して {total / total_cost * 100:.2f}%" if total_cost else ""
        on_value = f"評価額に対して {total / total_value * 100:.2f}%" if total_value else ""
        self.total_var.set(f"今後 12 か月の配当の受取見込み {fmt_yen(total)} 円(税引前)    {on_cost}    {on_value}")


# ---- タブ: ヒートマップ ----

class HeatmapTab(ttk.Frame):
    """ポートフォリオのツリーマップ。四角の大きさ = 評価額、色 = 前日比など。"""

    def __init__(self, master, app: "AssetApp"):
        super().__init__(master)
        self.app = app
        self.dirty = True
        self.valuations: list[Valuation] = []
        ui = app.store.ui

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=4)
        ttk.Label(bar, text="色").pack(side="left")
        metric = ui.get("heatmap_metric")
        self.metric_var = tk.StringVar(value=metric if metric in HEATMAP_METRICS else "前日比")
        ttk.Combobox(bar, textvariable=self.metric_var, values=list(HEATMAP_METRICS), state="readonly",
                     width=10).pack(side="left", padx=(4, 12))
        ttk.Label(bar, text="グループ").pack(side="left")
        group = ui.get("heatmap_group")
        self.group_var = tk.StringVar(value=group if group in HEATMAP_GROUPS else "33業種")
        ttk.Combobox(bar, textvariable=self.group_var, values=list(HEATMAP_GROUPS), state="readonly",
                     width=10).pack(side="left", padx=(4, 12))
        for var in (self.metric_var, self.group_var):
            var.trace_add("write", lambda *_: self._render())
        self.note_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.note_var, foreground=TEXT_SECONDARY).pack(side="left", padx=(8, 0))
        self.canvas = TreemapCanvas(self, on_open=self._open)
        self.canvas.pack(side="top", fill="both", expand=True, padx=4, pady=(0, 4))

    def show(self, valuations: list[Valuation]) -> None:
        self.valuations = valuations
        self._render()

    def ui_state(self) -> dict:
        return {"heatmap_metric": self.metric_var.get(), "heatmap_group": self.group_var.get()}

    def _open(self, key: str):
        self.app.open_chart(key.split("|", 1)[0])

    def _render(self):
        metric, scale = HEATMAP_METRICS[self.metric_var.get()]
        grouping = HEATMAP_GROUPS[self.group_var.get()]
        today = date.today().isoformat()
        merged: dict[str, list] = {}  # キー -> [評価、評価額、取得原価]
        for v in self.valuations:
            if v.value_jpy is None:
                continue
            key = v.instrument.id + (f"|{v.holding.broker}" if grouping == "broker" else "")
            item = merged.setdefault(key, [v, 0.0, 0.0])
            item[1] += v.value_jpy
            item[2] += v.cost_jpy
        items = []
        for key, (v, value, cost) in merged.items():
            instrument = v.instrument
            if metric == "change":
                pct = v.change_pct
            elif metric == "unrealized":
                pct = (value - cost) / cost * 100 if cost else None
            else:
                days = {"week": 7, "month": 30, "ytd": None}[metric]
                pct = period_change(self.app.prices.history(instrument), days, today)
            if grouping == "sector":
                group = instrument.sector or "(業種なし)"
            elif grouping == "asset_class":
                group = instrument.rule.label
            elif grouping == "broker":
                group = v.holding.broker
            else:
                group = ""
            detail = (f"{instrument.code} {instrument.name}\n評価額 {fmt_yen(value)} 円\n"
                      f"{self.metric_var.get()} {fmt_pct(pct)}")
            items.append(TreemapItem(key, instrument.code, value, pct, group, instrument.name, detail))
        missing = sum(1 for v in self.valuations if v.value_jpy is None)
        self.note_var.set(f"色が一番濃くなるのは ±{scale:g}%。ダブルクリックでチャートを開く"
                          + (f"   ※ 株価データのない {missing} 銘柄は出していません" if missing else ""))
        self.canvas.set_items(items, scale, up_is_red=self.app.up_is_red)


# ---- 移管 ----

class TransferDialog(tk.Toplevel):
    """移管(保有を別の証券会社・口座へ移す)の入力。出庫と入庫の 2 件を、同じ日・同じ平均取得単価で作る。"""

    def __init__(self, app: "AssetApp", key: HoldingKey):
        super().__init__(app)
        self.app = app
        self.key = key
        self.instrument = app.store.instruments[key[0]]
        holding = app.ledger.holdings[key]
        self.title("移管の入力")
        self.transient(app.winfo_toplevel())
        self.resizable(False, False)

        self.date_var = tk.StringVar(value=date.today().isoformat())
        others = [b for b in app.store.brokers if b != key[1]]
        self.broker_var = tk.StringVar(value=others[0] if others else key[1])
        self.account_var = tk.StringVar(value=key[2])
        self.quantity_var = tk.StringVar(value=fmt_quantity(holding.quantity))
        self.error_var = tk.StringVar()

        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)
        unit = self.instrument.rule.unit_label
        ttk.Label(form, text=f"{self.instrument.code}  {self.instrument.name}", font=("", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(form, text=f"移管元: {key[1]} / {key[2]}   保有 {fmt_quantity(holding.quantity)} {unit}   "
                             f"平均取得単価 {fmt_price(holding.average_price)} 円").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 8))
        fields = (
            ("移管の日", ttk.Entry(form, textvariable=self.date_var, width=12)),
            ("移管先の証券会社", ttk.Combobox(form, textvariable=self.broker_var, values=app.store.brokers,
                                       state="readonly", width=16)),
            ("移管先の口座区分", ttk.Combobox(form, textvariable=self.account_var, values=ACCOUNT_TYPES,
                                       state="readonly", width=16)),
            ("数量", ttk.Entry(form, textvariable=self.quantity_var, width=12, justify="right")),
        )
        for n, (label, widget) in enumerate(fields, start=2):
            ttk.Label(form, text=label).grid(row=n, column=0, sticky="w", pady=3, padx=(0, 8))
            widget.grid(row=n, column=1, sticky="w")
        ttk.Label(form, text="移管の日の平均取得単価を引き継ぎます(実現損益は出ません)。", foreground=TEXT_SECONDARY).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(form, textvariable=self.error_var, foreground=RED, wraplength=380, justify="left").grid(
            row=7, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(form)
        buttons.grid(row=8, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="保存", command=self._save).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=(4, 0))
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def _save(self):
        day, quantity = parse_date(self.date_var.get()), parse_number(self.quantity_var.get())
        destination = (self.key[0], self.broker_var.get(), self.account_var.get())
        if day is None:
            self.error_var.set("移管の日は 2026-11-18 の形で入力してください。")
            return
        if quantity is None or quantity <= 0:
            self.error_var.set("数量を 0 より大きい数で入力してください。")
            return
        if destination == self.key:
            self.error_var.set("移管先が移管元と同じです。")
            return
        # 移管の日までの取引で、その日の平均取得単価を出す
        store = self.app.store
        before = build_ledger([t for t in store.trades if t.date <= day], store.instruments)
        holding = before.holdings.get(self.key)
        if holding is None:
            self.error_var.set(f"{day} の時点で、移管元にこの銘柄の保有がありません。")
            return
        average = holding.average_price
        memo = f"移管 {self.key[1]}/{self.key[2]} → {destination[1]}/{destination[2]}"
        first = store.next_id()
        out_trade = Trade(first, day, self.key[0], self.key[1], self.key[2], "transfer_out", quantity, average,
                          memo=memo)
        in_trade = Trade(first + 1, day, self.key[0], destination[1], destination[2], "transfer_in", quantity,
                         average, memo=memo)
        errors = self.app.save_trades([out_trade, in_trade], [self.instrument])
        if errors:
            self.error_var.set("\n".join(errors))
            return
        self.app.set_message(f"移管を保存しました: {memo} {fmt_quantity(quantity)} {self.instrument.rule.unit_label}")
        self.destroy()


# ---- CSV 取り込み ----

IMPORT_COLUMNS = {
    "selected": ("取込", 44, "center"),
    "line": ("行", 40, "e"),
    "date": ("日付", 90, "center"),
    "code": ("コード", 60, "center"),
    "name": ("銘柄名", 180, "w"),
    "account": ("口座", 90, "center"),
    "action": ("売買", 50, "center"),
    "quantity": ("数量", 80, "e"),
    "price": ("単価", 90, "e"),
    "status": ("状態", 100, "center"),
    "message": ("内容", 300, "w"),
}


class ImportDialog(tk.Toplevel):
    """証券会社の CSV の取り込み。読み込んだ行を確かめてから取り込む。"""

    def __init__(self, app: "AssetApp"):
        super().__init__(app)
        self.app = app
        self.adapter = None
        self.rows = []
        self.overrides: dict[int, bool] = {}  # 行番号 -> 利用者が切り替えた「取り込むか」
        self.title("CSV の取り込み")
        self.transient(app.winfo_toplevel())
        self.geometry("1180x620")

        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(side="top", fill="x")
        self.path_var = tk.StringVar(value=app.store.ui.get("import_path", ""))
        self.date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Label(top, text="ファイル").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.path_var, width=90).grid(row=0, column=1, sticky="we", padx=4)
        ttk.Button(top, text="参照…", command=self._browse).grid(row=0, column=2)
        ttk.Button(top, text="読み込む", command=self._load).grid(row=0, column=3, padx=(4, 0))
        ttk.Label(top, text="基準日").grid(row=1, column=0, sticky="w", pady=(4, 0))
        line = ttk.Frame(top)
        line.grid(row=1, column=1, columnspan=3, sticky="w", padx=4, pady=(4, 0))
        ttk.Entry(line, textvariable=self.date_var, width=12).pack(side="left")
        ttk.Label(line, text="保有商品一覧の CSV は、この日の「入庫」として取り込みます(CSV を保存した日にしてください)",
                  foreground=TEXT_SECONDARY).pack(side="left", padx=(6, 0))
        self.format_var = tk.StringVar(value="形式: (未読み込み)")
        ttk.Label(top, textvariable=self.format_var).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        top.columnconfigure(1, weight=1)

        bottom = ttk.Frame(self, padding=(10, 4, 10, 10))
        bottom.pack(side="bottom", fill="x")
        self.count_var, self.hint_var = tk.StringVar(), tk.StringVar()
        ttk.Label(bottom, textvariable=self.count_var).pack(side="top", anchor="w")
        ttk.Label(bottom, textvariable=self.hint_var, foreground=TEXT_SECONDARY, wraplength=1100,
                  justify="left").pack(side="top", anchor="w")
        buttons = ttk.Frame(bottom)
        buttons.pack(side="top", anchor="e", pady=(6, 0))
        self.commit_button = ttk.Button(buttons, text="取り込む", command=self._commit, state="disabled")
        self.commit_button.pack(side="left")
        ttk.Button(buttons, text="閉じる", command=self.destroy).pack(side="left", padx=(4, 0))

        self.list = SortableTree(self, IMPORT_COLUMNS, stretch="message")
        self.list.pack(side="top", fill="both", expand=True, padx=10)
        for status, color in IMPORT_STATUS_COLORS.items():
            self.list.tree.tag_configure(status, background=color)
        self.list.tree.bind("<Button-1>", self._on_click)
        self.list.tree.bind("<space>", lambda e: self._toggle(self.list.selected()))
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        if self.path_var.get() and Path(self.path_var.get()).exists():
            self._load()

    def _browse(self):
        initial = Path(self.path_var.get()).parent if self.path_var.get() else Path.home()
        path = filedialog.askopenfilename(
            parent=self, title="取り込む CSV", initialdir=initial,
            filetypes=[("CSV ファイル", "*.csv"), ("すべてのファイル", "*.*")],
        )
        if path:
            self.path_var.set(path)
            self.overrides.clear()
            self._load()

    def _load(self):
        """CSV を読み、重複と数量不足を調べて一覧に出す(利用者が切り替えた行は、その選択を残す)。"""
        path = Path(self.path_var.get().strip().strip('"'))
        base_date = parse_date(self.date_var.get())
        if base_date is None:
            self.hint_var.set("基準日は 2026-11-18 の形で入力してください。")
            return
        context = ImportContext(base_date, self.app.instrument_for_code)
        try:
            self.adapter, self.rows = read_csv(path, context)
        except (OSError, ValueError) as e:
            self.adapter, self.rows = None, []
            self.format_var.set("形式: (読み込めません)")
            self.hint_var.set(str(e))
            self._render()
            return
        self.app.store.ui["import_path"] = str(path)
        self.format_var.set(f"形式: {self.adapter.label}(自動判定)   証券会社: {self.adapter.broker}")
        classify(self.rows, self.app.store, self.app.ledger)
        for row in self.rows:
            if row.toggleable and row.line in self.overrides:
                row.selected = self.overrides[row.line]
        check_problems(self.rows, self.app.store)
        self._render()

    def _render(self):
        rows = []
        for row in self.rows:
            t = row.trade
            instrument = row.instrument
            mark = ("☑" if row.selected else "☐") if row.toggleable else "―"
            display = (
                mark, row.line, t.date if t else "", instrument.code if instrument else "",
                instrument.name if instrument else next(iter(row.values.values()), ""),
                t.account if t else "", ACTIONS.get(t.action, "") if t else "",
                fmt_quantity(t.quantity) if t else "", fmt_price(t.price) if t and t.action != "transfer_out" else "",
                STATUSES[row.status][0], row.message,
            )
            keys = (row.selected, row.line, t.date if t else None, instrument.code if instrument else None,
                    instrument.name if instrument else None, t.account if t else None, t.action if t else None,
                    t.quantity if t else None, t.price if t else None, row.status, row.message)
            rows.append((str(row.line), display, keys, (row.status,)))
        self.list.set_rows(rows)
        counts = status_counts(self.rows)
        chosen = sum(1 for r in self.rows if r.selected and r.trade is not None)
        self.count_var.set("   ".join(f"{STATUSES[s][0]} {n} 件" for s, n in counts.items() if n)
                           + f"      取り込む行: {chosen} 件")
        self.commit_button.configure(text=f"取り込む({chosen} 件)", state="normal" if chosen else "disabled")
        hints = []
        if any(r.status == "error" and "保有" in r.message for r in self.rows):
            hints.append("※ CSV より前から保有している銘柄は、先に「入庫」で期首の数量と取得単価を入力するか、"
                         "保有商品一覧の CSV を取り込んでください。")
        if counts["maybe_duplicate"] or counts["exists"]:
            hints.append("※ 黄色の行は、取り込むと二重に数える可能性があります。必要なら「取込」の欄をクリックして取り込んでください。")
        if self.adapter is not None:
            self.hint_var.set("\n".join(hints))

    def _on_click(self, event):
        if self.list.tree.identify_region(event.x, event.y) == "cell" and self.list.tree.identify_column(event.x) == "#1":
            self._toggle(self.list.tree.identify_row(event.y))

    def _toggle(self, iid: str | None):
        row = next((r for r in self.rows if str(r.line) == iid), None)
        if row is None or not row.toggleable:
            return
        self.overrides[row.line] = not row.selected
        self._load()  # 選び直した行で、数量不足を調べ直す

    def _commit(self):
        chosen = sum(1 for r in self.rows if r.selected and r.trade is not None)
        if not chosen or not messagebox.askyesno(APP_NAME, f"{chosen} 件の取引を取り込みますか?", parent=self):
            return
        store = self.app.store
        if store.path.exists():
            # 取り込みは元に戻せないので、取り込む前のファイルを残しておく
            shutil.copy2(store.path, store.path.with_name(store.path.name + ".bak"))
        count = commit(self.rows, store, self.adapter.broker)
        if not self.app.save_store():
            return
        self.app.recompute()
        self.app.set_message(f"{self.adapter.label} から {count} 件の取引を取り込みました"
                             f"(取り込む前の記録は {store.path.name}.bak に残しています)。")
        self.destroy()


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
        ttk.Button(bar, text="CSV 取り込み", command=lambda: ImportDialog(self)).pack(side="right", padx=(0, 4))
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
        self.summary_tab = SummaryTab(self.notebook, self)
        self.dividend_tab = DividendTab(self.notebook, self)
        self.heatmap_tab = HeatmapTab(self.notebook, self)
        self.notebook.add(self.holdings_tab, text="保有一覧")
        self.notebook.add(self.trades_tab, text="取引履歴")
        self.notebook.add(self.realized_tab, text="実現損益")
        self.notebook.add(self.summary_tab, text="集計")
        self.notebook.add(self.dividend_tab, text="配当")
        self.notebook.add(self.heatmap_tab, text="ヒートマップ")
        tab = ui.get("tab")
        if isinstance(tab, int) and 0 <= tab < len(self.notebook.tabs()):
            self.notebook.select(tab)
        # 重いタブ(グラフ・ヒートマップ・配当)は、見えているときだけ描き直す
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._show_lazy_tab())

        self.recompute()

    # ---- 集計と表示 ----

    def recompute(self) -> None:
        """取引の全件から集計・評価し直して、表示を作り直す。"""
        self.ledger = build_ledger(self.store.trades, self.store.instruments)
        self.problem_ids = {p.trade_id for p in self.ledger.problems}
        self.valuations = value_all(self.ledger, self.store.instruments, self.prices)
        self.summary_tab.history_cache.clear()  # 取引が変わったので、過去の推移を計算し直す
        self._take_snapshot()
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
        self.filtered_valuations = valuations
        for tab in (self.summary_tab, self.dividend_tab, self.heatmap_tab):
            tab.dirty = True
        self._show_lazy_tab()

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

    def matches_key(self, key: HoldingKey) -> bool:
        """保有のキーが絞り込みの条件に合うか。"""
        return self._matches(key[1], key[2])

    def filter_label(self) -> str:
        """絞り込みの条件の表示(絞り込みなしなら空文字)。"""
        parts = [v for v in (self.broker_var.get(), self.account_var.get()) if v != ALL]
        return " / ".join(parts)

    @property
    def up_is_red(self) -> bool:
        return self.japan_color_var.get()

    def _show_lazy_tab(self):
        """見えているタブが重いタブで、表示が古ければ描き直す。"""
        if not hasattr(self, "filtered_valuations"):
            return
        current = self.nametowidget(self.notebook.select())
        if current in (self.summary_tab, self.dividend_tab, self.heatmap_tab) and current.dirty:
            current.dirty = False
            current.show(self.filtered_valuations)

    def reload_prices(self) -> None:
        """株価データを読み直す(起動したまま core_gather.py で株価を更新したとき)。"""
        self.prices.clear()
        self.recompute()

    def _take_snapshot(self):
        """今日の資産の合計(絞り込みなし)を記録する(1 日 1 件。今日の記録は最新の値で置き換える)。"""
        today = date.today().isoformat()
        summary = summarize(self.valuations, self.ledger.realized, today)
        if record_snapshot(self.store, summary, today):
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
            **self.summary_tab.ui_state(), **self.heatmap_tab.ui_state(),
        )
        self.dividend_tab.stop()
        return self.save_store()

    # ---- 取引 ----

    def instrument_for_code(self, code: str, name: str = "") -> Instrument:
        """日本株の商品を、銘柄一覧の名称・市場・33 業種から作る(銘柄一覧になければ、登録済みのもの)。"""
        return jp_stock_instrument(code, self.listed, self.store.instruments, name)

    def save_trades(self, trades: list[Trade], instruments: list[Instrument]) -> list[str]:
        """複数の取引(移管の出庫と入庫など)をまとめて確かめて保存する。保存できなければ、その理由の一覧。"""
        known = {**self.store.instruments, **{i.id: i for i in instruments}}
        ids = {t.id for t in trades}
        others = [t for t in self.store.trades if t.id not in ids]
        errors = changed_problems(others, [*others, *trades], known)
        if errors:
            return errors
        for instrument in instruments:
            self.store.ensure_instrument(instrument)
        for trade in trades:
            self.store.put_trade(trade)
        if not self.save_store():
            return ["保存できませんでした。"]
        self.recompute()
        return []

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

    買い(と入庫)ごとに 1 つの印にし、売りは先に買った分から順に割り当てる(先入れ先出し)。
    全部売った買いには、売りの日付と平均の売値を付ける。
    出庫(移管)した分は印から除く(移管先の入庫が、新しい印になる)。
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
        lots = []  # [買いの日、単価、残りの数量、売った数量、売った金額、最後に売った日、出庫した数量]
        for t in trades:
            if t.key != key or t.action not in ("buy", "sell", *TRANSFERS):
                continue
            f = factor(t)
            price, quantity = t.price / f, t.quantity * f
            if t.action in ("buy", "transfer_in"):
                lots.append([t.date, price, quantity, 0.0, 0.0, None, 0.0])
                continue
            for lot in lots:
                if quantity <= 0:
                    break
                used = min(lot[2], quantity)
                if used <= 0:
                    continue
                lot[2] -= used
                if t.action == "sell":
                    lot[3] += used
                    lot[4] += used * price
                    lot[5] = t.date
                else:
                    lot[6] += used
                quantity -= used
        for entry, price, remaining, sold, amount, exit_date, moved in lots:
            if remaining <= 1e-9 and sold <= 0 and moved > 0:
                continue  # 全部を移管した買い
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
