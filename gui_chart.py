"""ローソク足チャートを表示する(チャート表示の基本プログラム)。

stock_database フォルダの日足データ(core_gather.py で取得)を読み込み、
ローソク足を表示する。陽線は緑、陰線は赤で描く。
移動平均線(SMA 5/25/75/200、core_indicators.SMAEssential4)を常に重ねて表示する。
下段には出来高、オシレーター(core_oscillators.py)、コンディション(core_condition.py)を表示でき、
それぞれ表示・非表示を選べる。

使い方:
    python gui_chart.py 7203    # 7203.T のチャートを表示
    python gui_chart.py         # 起動後に画面で銘柄コードを入力

操作:
    銘柄の変更      銘柄欄に銘柄コードまたは銘柄名の一部を入力すると、候補が出る。
                    上下キーで選んで Enter、候補をダブルクリック、または「表示」ボタンで表示する。
                    全角・半角、大文字・小文字、ひらがな・カタカナは区別しない。
                    候補にない銘柄(上場廃止など)も、コードを入力して Enter で表示できる
    足の変更        日足 / 週足 / 月足 ボタン
    下段の表示      「出来高」「オシレーター」「コンディション」のチェックで表示・非表示。
                    オシレーターとコンディションは、右の欄で種類を選ぶ
    スクロール      チャートの何もない所を左右にドラッグ、または下のスクロールバー
    拡大・縮小      マウスホイール
    図形の追加      図形のボタンを選んでチャートをクリック
                    水平線・垂直線      1 回クリック
                    トレンドライン・矩形 始点と終点の 2 回クリック
                    平行チャンネル      基準線の始点・終点、平行線の位置の 3 回クリック
                    パス                点を順にクリックし、ダブルクリック・Enter・右クリックで確定
                                        (終点に矢印が付く)
    作図の取り消し  Esc(パス以外は右クリックでも可)
    図形の選択      カーソルモードで図形をクリック(点の印が付く)
    図形の移動      カーソルモードで図形をドラッグ
    図形の色        色のボタンを押すと、選択中の図形とこれから描く図形の色が変わる
    図形の削除      図形を右クリック、または選択して Delete。「全消去」ですべて削除

表示の設定と図形は、銘柄ごとに charts/<コード>.json に保存する(core_chartstore.py。仕様は chart-state-spec.md)。
どのプログラムから開いても、同じ銘柄なら同じ図形・表示になる。
    保存する時機  図形を描く・動かす・色を変える・消すとき、足・下段・十字線を利用者が変えたとき
    読み込む時機  銘柄を表示するとき。ウィンドウに戻ってきたとき、ほかのウィンドウが書き換えていれば読み直す
    既定          ファイルのない銘柄は、ToreCan の設定のチャートの既定で開く。
                  「この表示を既定にする」ボタンで、今の表示を既定にできる
    図形を描く色は、全銘柄で 1 つ(ToreCan の settings.json に保存)
他のプログラムが変えた表示(スクリーニングの show_condition など)は保存しない。
テクニカル訓練は先の足を隠すので、ChartFrame(persist=False) で読み込みも保存もしない。

他のプログラムからは、ChartFrame を tkinter の部品として組み込める。
get_state() / set_state() は、表示の設定と図形をまとめて取り出し・復元する(前の版の watchlist.json の形)。
売買ポイントは set_trades() で重ね、select_trade() でその売買へ表示を動かせる(fina_bktest.py が使う)。
set_data() で途中までの日足だけを表示できる(fina_techtrainer.py が、先の足を隠すのに使う)。
"""

import math
import sys
import tkinter as tk
from dataclasses import dataclass
from tkinter import colorchooser, messagebox, ttk

import matplotlib
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.colors import is_color_like, to_rgba
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle
from matplotlib.path import Path as MplPath
from matplotlib.ticker import FuncFormatter, MaxNLocator

from gui_theme import apply_theme
from core_gather import csv_path_for, load_saved
from core_chartstore import chart_mtime, delete_chart, load_chart, save_chart
from core_settings import app_title, load_settings, market_colors, update_settings
from core_stocks import load_listed, load_stock_names, stock_index
from gui_widgets import StockSearchBox
from core_indicators import Indicator, SMAEssential4
from core_oscillators import OSCILLATORS
from core_condition import CONDITIONS

# チャート内の日本語(凡例など)を表示できるフォント。先頭から順に、見つかったものを使う
matplotlib.rcParams["font.family"] = ["Yu Gothic", "Meiryo", "MS Gothic", "sans-serif"]

# 陽線・陰線の色。全体設定(settings.json)の色の決まりに従う(海外式: 上昇が緑、日本式: 上昇が赤)
UP_COLOR, DOWN_COLOR = market_colors()
# 図形の色の候補。先頭が最初の色
DRAW_COLORS = ["#2962ff", "#f23645", "#089981", "#ff9800", "#9c27b0", "#131722"]

# 表示名 -> (pandas の期間、日付の書式)
TIMEFRAMES = {
    "日足": (None, "%Y/%m/%d"),
    "週足": ("W", "%Y/%m/%d"),
    "月足": ("M", "%Y/%m"),
}
# 表示名 -> 図形の種類
TOOLS = {
    "カーソル": "cursor",
    "水平線": "hline",
    "垂直線": "vline",
    "トレンドライン": "trend",
    "矩形": "rect",
    "平行チャンネル": "channel",
    "パス": "path",
}
# 図形を決めるのに必要なクリック数(パスは確定操作まで何点でも)
POINTS_NEEDED = {"hline": 1, "vline": 1, "trend": 2, "rect": 2, "channel": 3, "path": None}
MIN_PATH_POINTS = 2

INITIAL_BARS = 120   # 最初に表示する足の本数
MIN_BARS = 10        # 拡大したときの最小本数
LEFT_MARGIN = 1      # 左端の余白(足の本数)
RIGHT_MARGIN = 4     # 右端の余白(足の本数)
BODY_HALF_WIDTH = 0.35
PICK_PIXELS = 6      # 図形をクリックで選ぶときの許容距離
LINE_WIDTH = 1.4
INDICATOR_ZORDER = 1.5  # ローソク足(1)より上、図形(2)より下
LINK_ZORDER = 1.8       # コンディションの根拠の線。インジケーターより上、図形より下
CROSSHAIR_COLOR = "#787b86"
TRADE_ZORDER = 1.9         # 売買ポイント。コンディションの根拠の線より上、図形より下
BUY_COLOR = "#2962ff"      # 買いの印(▲)
SELL_COLOR = "#ff6d00"     # 売りの印(▼)
TRADE_VIEW_MARGIN = 30     # 売買を選んだとき、その前後に見せる足の本数
# 各段の高さの比率
PANE_HEIGHTS = {"price": 4, "volume": 1, "oscillator": 1.3, "condition": 0.5}


def base_indicators() -> list[Indicator]:
    """常に表示するインジケーター。ユーザーは非表示にできない。"""
    return [SMAEssential4()]


def resample(daily: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    """日足を週足("W")や月足("M")にまとめる。日付は期間の最初の取引日。"""
    if rule is None:
        return daily
    grouped = daily.assign(Date=daily.index).groupby(daily.index.to_period(rule))
    return grouped.agg(
        {"Date": "first", "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).set_index("Date")


def channel_lines(xy: list[tuple[float, float]]):
    """平行チャンネルの 3 点から、基準線と平行線の両端を返す。

    1・2 点目が基準線。平行線は基準線と同じ傾きで、3 点目を通る。
    """
    (x0, y0), (x1, y1), (x2, y2) = xy
    base_at_x2 = y0 + (y1 - y0) * (x2 - x0) / (x1 - x0) if x1 != x0 else y0
    offset = y2 - base_at_x2
    return ((x0, y0), (x1, y1)), ((x0, y0 + offset), (x1, y1 + offset))


def segment_distance(p, a, b) -> float:
    """点 p から線分 ab までの距離。"""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = min(max(((px - ax) * dx + (py - ay) * dy) / length2, 0), 1) if length2 else 0
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _is_hex_color(value) -> bool:
    """"#2962ff" の形の色か(tkinter と matplotlib の両方で使える形)。"""
    return isinstance(value, str) and len(value) == 7 and value[0] == "#" and is_color_like(value)


def _parse_drawing(saved) -> dict | None:
    """保存されていた図形 1 つを、ChartFrame が持つ形に直す。壊れていれば None。"""
    try:
        kind, color = saved["type"], saved["color"]
        points = [(pd.Timestamp(date), float(price)) for date, price in saved["points"]]
    except (KeyError, TypeError, ValueError):
        return None
    if kind not in POINTS_NEEDED or not _is_hex_color(color):
        return None
    if any(pd.isna(date) or not math.isfinite(price) for date, price in points):
        return None
    needed = POINTS_NEEDED[kind]
    if len(points) != needed if needed else len(points) < MIN_PATH_POINTS:
        return None
    return {"type": kind, "points": points, "color": color}


# ---- 表示の設定(銘柄ごとに保存する。ファイルのない銘柄は既定) ----

DISPLAY_KEYS = {  # 表示の設定の項目 -> 値の型
    "timeframe": str, "volume": bool, "oscillator_on": bool, "oscillator": str,
    "condition_on": bool, "condition": str, "crosshair": bool,
}
# ToreCan の設定にチャートの既定がないときの表示(前の版の初期値と同じ)
BUILTIN_DISPLAY = {
    "timeframe": "日足", "volume": True, "oscillator_on": True, "oscillator": next(iter(OSCILLATORS)),
    "condition_on": True, "condition": next(iter(CONDITIONS)), "crosshair": False,
}


def valid_display(saved, base: dict) -> dict:
    """保存されていた表示の設定を確かめる。ない・使えない項目(なくなったオシレーターなど)は base の値にする。"""
    display = dict(base)
    choices = {"timeframe": TIMEFRAMES, "oscillator": OSCILLATORS, "condition": CONDITIONS}
    for key, kind in DISPLAY_KEYS.items():
        value = saved.get(key) if isinstance(saved, dict) else None
        if type(value) is kind and (key not in choices or value in choices[key]):
            display[key] = value
    return display


def chart_defaults() -> dict:
    """チャートの既定の表示(ToreCan の設定 settings.json の chart_defaults。なければ BUILTIN_DISPLAY)。"""
    return valid_display(load_settings().chart_defaults, BUILTIN_DISPLAY)


def serialize_drawings(drawings: list[dict]) -> list[dict]:
    """図形を JSON に保存できる形にする。"""
    return [
        {"type": d["type"], "color": d["color"],
         "points": [[f"{date:%Y-%m-%d}", float(price)] for date, price in d["points"]]}
        for d in drawings
    ]


@dataclass(frozen=True)
class TradeMark:
    """チャートに示す売買 1 つ。exit_date が None なら保有中。"""
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    take_profit: float | None = None  # 選んだときに横線で示す(None・無限大・0 以下なら示さない)
    stop_loss: float | None = None


class ChartFrame(ttk.Frame):
    """ローソク足チャートと操作用ツールバーをまとめた部品。"""

    def __init__(self, master, code: str | None = None, searchable: bool = True, persist: bool = True):
        """searchable が False なら、銘柄欄を出さない(表示する銘柄やデータを、他のプログラムが決めるとき)。

        persist が False なら、銘柄ごとのファイルを読み込みも保存もしない(テクニカル訓練が使う)。
        """
        super().__init__(master)
        self.searchable = searchable
        self.names = load_stock_names()
        self.code = None
        self.daily = None   # 日足データ
        self.bars = None    # 表示中の足(日足・週足・月足のいずれか)
        # 図形は {"type": 種類, "points": [(日付, 価格), ...], "color": 色} の形で、銘柄ごとに保持する
        self.drawings_by_code = {}
        self.drawings = []
        saved_color = load_settings().draw_color  # 図形を描く色は全銘柄で 1 つ
        self.color = saved_color if _is_hex_color(saved_color) else DRAW_COLORS[0]
        self.persist = persist
        self.saved_display = chart_defaults()  # 銘柄のファイルに書く表示(利用者が選んだもの)
        self.loaded_mtime: float | None = None  # 読み込んだときの、銘柄のファイルの更新時刻
        self.pending = None   # 作図中の図形(確定した点だけを持つ)
        self.preview = None   # 作図中、マウス位置にある次の点
        self.selected = None  # 選択中の図形
        self.drag = None      # 図形の移動中の情報
        self.pan_start = None
        self.indicators = base_indicators()
        self.indicator_values = None   # 表示中の足で計算したインジケーターの値(全インジケーターの列)
        self.oscillator_values = None  # 表示中のオシレーターの値
        self.condition_values = None   # 表示中のコンディションの判定結果
        self.ax = self.ax_vol = self.ax_osc = self.ax_cond = None
        self.candle_artists = []
        self.indicator_artists = []
        self.drawing_artists = []
        self.trade_artists = []
        self.trade_marks: list[TradeMark] = []  # 表示中の銘柄の売買ポイント(バックテストなどから渡される)
        self.selected_trade: int | None = None  # 利確・損切りの線を出す売買(trade_marks の番号)

        self._build_toolbar()
        self._apply_display(self.saved_display)
        self._build_chart()
        # ほかのウィンドウで同じ銘柄の図形が変わっていたら、戻ってきたときに読み直す
        self.winfo_toplevel().bind("<FocusIn>", lambda e: self._reload_if_changed(), add="+")
        if code:
            self.set_code(code)

    # ---- 画面の組み立て ----

    def _build_toolbar(self):
        # 1 段目: 銘柄と足
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=(4, 0))

        # 銘柄コードまたは銘柄名の一部で探す。銘柄一覧にないコード(上場廃止など)も、そのまま入力して表示できる
        if self.searchable:
            try:
                index = stock_index(load_listed())
            except FileNotFoundError:  # 銘柄一覧がなくても、コードの入力で表示できるようにする
                index = []
            self.search = StockSearchBox(
                bar, index,
                on_pick=lambda stock: self.set_code(stock.code),
                on_unmatched=self.set_code,
                label="銘柄", button="表示", dropdown=True, entry_width=18,
            )
            self.search.pack(side="left")
        self.name_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.name_var, width=28).pack(side="left", padx=8)

        self.timeframe_var = tk.StringVar(value="日足")
        for label in TIMEFRAMES:
            ttk.Radiobutton(
                bar, text=label, value=label, variable=self.timeframe_var,
                style="Toolbutton", command=lambda: self._on_display_changed("timeframe"),
            ).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        # 下段の表示・非表示
        self.volume_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="出来高", variable=self.volume_var,
                        command=lambda: self._on_display_changed("volume")).pack(side="left")
        self.oscillator_on_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bar, text="オシレーター", variable=self.oscillator_on_var,
            command=lambda: self._on_display_changed("oscillator_on"),
        ).pack(side="left", padx=(8, 2))
        self.oscillator_var = tk.StringVar(value=next(iter(OSCILLATORS)))
        self.oscillator_box = ttk.Combobox(
            bar, textvariable=self.oscillator_var, values=list(OSCILLATORS), state="readonly", width=8,
        )
        self.oscillator_box.pack(side="left")
        self.oscillator_box.bind("<<ComboboxSelected>>", lambda e: self._on_display_changed("oscillator"))
        self.condition_on_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bar, text="コンディション", variable=self.condition_on_var,
            command=lambda: self._on_display_changed("condition_on"),
        ).pack(side="left", padx=(8, 2))
        self.condition_var = tk.StringVar(value=next(iter(CONDITIONS)))
        self.condition_box = ttk.Combobox(
            bar, textvariable=self.condition_var, values=list(CONDITIONS), state="readonly", width=16,
        )
        self.condition_box.pack(side="left")
        self.condition_box.bind("<<ComboboxSelected>>", lambda e: self._on_display_changed("condition"))

        # 2 段目: 図形
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=(4, 0))

        self.tool_var = tk.StringVar(value="cursor")
        for label, tool in TOOLS.items():
            ttk.Radiobutton(
                bar, text=label, value=tool, variable=self.tool_var,
                style="Toolbutton", command=self._on_tool_change,
            ).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Label(bar, text="色").pack(side="left", padx=(0, 4))
        self.color_indicator = tk.Label(bar, width=3, bg=self.color, relief="sunken", bd=2)
        self.color_indicator.pack(side="left", padx=(0, 6))
        for color in DRAW_COLORS:
            swatch = tk.Label(bar, width=2, bg=color, relief="raised", bd=1, cursor="hand2")
            swatch.pack(side="left", padx=1)
            swatch.bind("<Button-1>", lambda e, c=color: self.set_color(c))
        ttk.Button(bar, text="…", width=3, command=self._choose_color).pack(side="left", padx=(4, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="全消去", width=7, command=self.clear_drawings).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.crosshair_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="十字線", variable=self.crosshair_var,
                        command=lambda: self._on_display_changed("crosshair")).pack(side="left")
        ttk.Button(bar, text="この表示を既定にする", command=self.save_as_default).pack(side="right")

        # マウス位置の足の日付と OHLCV を表示する
        self.info_var = tk.StringVar()
        ttk.Label(self, textvariable=self.info_var).pack(side="top", fill="x", padx=6, pady=2)

    def _build_chart(self):
        self.fig = Figure(figsize=(11, 6.5), dpi=100)
        self._build_axes()

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self._on_scrollbar)
        self.scrollbar.pack(side="bottom", fill="x")
        widget = self.canvas.get_tk_widget()
        widget.pack(side="top", fill="both", expand=True)
        widget.bind("<Escape>", lambda e: self._on_escape())
        widget.bind("<Return>", lambda e: self._finish_pending())
        widget.bind("<Delete>", lambda e: self.delete_selected())

        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)

    def _build_axes(self):
        """価格・出来高・オシレーターの段を、表示する設定に合わせて作り直す。"""
        self.fig.clear()
        # 図を作り直すと線などはすべて消えるので、記録も空にする
        self.candle_artists, self.indicator_artists, self.drawing_artists = [], [], []
        self.oscillator_artists, self.condition_artists, self.trade_artists = [], [], []
        panes = ["price"]
        if self.volume_var.get():
            panes.append("volume")
        if self.oscillator_on_var.get():
            panes.append("oscillator")
        if self.condition_on_var.get():
            panes.append("condition")

        grid = self.fig.add_gridspec(
            len(panes), 1, height_ratios=[PANE_HEIGHTS[p] for p in panes], hspace=0.03,
        )
        self.ax = self.fig.add_subplot(grid[0])
        axes = {"price": self.ax}
        for i, pane in enumerate(panes[1:], 1):
            axes[pane] = self.fig.add_subplot(grid[i], sharex=self.ax)
        self.ax_vol = axes.get("volume")
        self.ax_osc = axes.get("oscillator")
        self.ax_cond = axes.get("condition")
        self.fig.subplots_adjust(left=0.04, right=0.93, top=0.98, bottom=0.06)

        for ax in axes.values():
            ax.yaxis.tick_right()
            ax.grid(True, color="#e6e6e6", linewidth=0.6)
            ax.set_axisbelow(True)
            ax.autoscale(False)
            ax.tick_params(labelbottom=False)
        # 日付は一番下の段にだけ出す
        bottom = axes[panes[-1]]
        bottom.tick_params(labelbottom=True)
        # 横軸は足の番号。休日の隙間を作らないため、日付ではなく番号で並べる
        bottom.xaxis.set_major_locator(MaxNLocator(nbins=8, integer=True))
        bottom.xaxis.set_major_formatter(FuncFormatter(self._format_date))
        if self.ax_vol:
            self.ax_vol.yaxis.set_major_locator(MaxNLocator(nbins=3))
            self.ax_vol.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1e6:g}M"))

        # 十字線: 縦線は全段に、横線は価格チャートにだけ引く。普段は隠しておく
        style = {"color": CROSSHAIR_COLOR, "linewidth": 0.8, "linestyle": "--", "visible": False, "zorder": 5}
        self.crosshair_v = [ax.axvline(0, **style) for ax in axes.values()]
        self.crosshair_h = self.ax.axhline(0, **style)

    def _on_condition_toggle(self):
        self.condition_box.configure(state="readonly" if self.condition_on_var.get() else "disabled")
        self._rebuild_panes()

    def show_condition(self, name: str) -> None:
        """指定したコンディション(CONDITIONS の表示名)を最下段に表示する。他のプログラムから使う。"""
        if self.condition_on_var.get() and self.condition_var.get() == name:
            return
        self.condition_var.set(name)
        self.condition_on_var.set(True)
        self._on_condition_toggle()

    def _rebuild_panes(self):
        """段の構成を変えて描き直す。表示中の範囲はそのまま保つ。"""
        xlim = self.ax.get_xlim() if self.bars is not None else None
        self.pending = self.preview = self.drag = None
        self.pan_start = None
        self._build_axes()
        if self.bars is not None:
            self._draw_all()
            self._set_view(*xlim)
        else:
            self.canvas.draw_idle()

    # ---- 銘柄と足の切り替え ----

    def set_code(self, code: str) -> None:
        """表示する銘柄を変える。"""
        code = code.strip().upper().removesuffix(".T")
        if not code:
            return
        daily = load_saved(csv_path_for(code))
        if daily is None:
            messagebox.showerror(
                "gui_chart",
                f"{code} のデータがありません。\n先に python core_gather.py {code} を実行してください。",
                parent=self,
            )
            return
        self.code = code
        self.daily = daily.dropna()
        self.name_var.set(f"{code} {self.names.get(code, '')}".strip())
        if self.persist:
            self._load_chart(code)  # 銘柄の表示と図形(ファイルがなければ既定の表示・図形なし)
        self.drawings = self.drawings_by_code.setdefault(code, [])
        self.selected = None
        self.trade_marks, self.selected_trade = [], None  # 売買ポイントは銘柄ごとに渡し直してもらう
        self.pending = self.preview = self.drag = self.pan_start = None
        self._build_axes()  # 表示の設定(下段)が銘柄ごとに違うので、段を作り直す
        self._show_bars()

    def set_data(self, code: str, daily: pd.DataFrame) -> None:
        """渡した日足(途中までのデータなど)で表示する。他のプログラム(fina_techtrainer.py など)から使う。

        同じ銘柄で続けて呼ぶと、表示中の本数(拡大・縮小の具合)を保ったまま、右端の足が見える位置に合わせる。
        set_code と違い、売買ポイントは消さない。
        """
        width = None
        if self.bars is not None and self.code == code:
            lo, hi = self.ax.get_xlim()
            width = hi - lo
        self.code = code
        self.daily = daily
        self.name_var.set(f"{code} {self.names.get(code, '')}".strip())
        self.drawings = self.drawings_by_code.setdefault(code, [])
        self.pending = self.preview = self.drag = None
        self.bars = resample(self.daily, TIMEFRAMES[self.timeframe_var.get()][0])
        self._draw_all()
        right = len(self.bars) + RIGHT_MARGIN
        self._set_view(right - (width or INITIAL_BARS), right)

    def _show_bars(self):
        """選択中の足(日足・週足・月足)で描き直し、直近の足が見える位置に合わせる。"""
        if self.daily is None:
            return
        self.pending = self.preview = self.drag = None
        self.bars = resample(self.daily, TIMEFRAMES[self.timeframe_var.get()][0])
        self._draw_all()
        right = len(self.bars) + RIGHT_MARGIN
        self._set_view(right - INITIAL_BARS, right)

    def _draw_all(self):
        self._draw_candles()
        self._draw_indicators()
        self._draw_oscillator()
        self._draw_condition()
        self._draw_trades()
        self._render_drawings()

    def _draw_candles(self):
        for artist in self.candle_artists:
            artist.remove()
        b = self.bars
        w = BODY_HALF_WIDTH
        colors = [UP_COLOR if c >= o else DOWN_COLOR for o, c in zip(b["Open"], b["Close"])]
        wicks = [((i, lo), (i, hi)) for i, (lo, hi) in enumerate(zip(b["Low"], b["High"]))]
        bodies = [
            ((i - w, o), (i - w, c), (i + w, c), (i + w, o))
            for i, (o, c) in enumerate(zip(b["Open"], b["Close"]))
        ]
        self.candle_artists = [
            self.ax.add_collection(LineCollection(wicks, colors=colors, linewidths=0.8)),
            # 始値と終値が同じ足も線として見えるよう、枠線を付ける
            self.ax.add_collection(PolyCollection(bodies, facecolors=colors, edgecolors=colors, linewidths=0.6)),
        ]
        if self.ax_vol:
            volumes = [((i - w, 0), (i - w, v), (i + w, v), (i + w, 0)) for i, v in enumerate(b["Volume"])]
            self.candle_artists.append(self.ax_vol.add_collection(
                PolyCollection(volumes, facecolors=colors, edgecolors=colors, linewidths=0.6)
            ))

    def _draw_indicators(self):
        """インジケーターを計算し、価格チャートに線で重ねる。"""
        for artist in self.indicator_artists:
            artist.remove()
        self.indicator_artists = []
        x = range(len(self.bars))
        values = []
        for indicator in self.indicators:
            result = indicator.compute(self.bars)
            values.append(result)
            for column, style in indicator.lines.items():
                (line,) = self.ax.plot(
                    x, result[column], color=style.color, linewidth=style.width,
                    label=style.label, zorder=INDICATOR_ZORDER,
                )
                self.indicator_artists.append(line)
        self.indicator_values = pd.concat(values, axis=1) if values else None
        if self.indicator_artists:
            legend = self.ax.legend(
                handles=self.indicator_artists, loc="upper left", frameon=False, fontsize=9, ncols=len(self.indicator_artists),
            )
            self.indicator_artists.append(legend)

    def _draw_oscillator(self):
        """選択中のオシレーターを下段に描く。"""
        for artist in self.oscillator_artists:
            artist.remove()
        self.oscillator_artists = []
        self.oscillator_values = None
        if self.ax_osc is None:
            return
        ax, add = self.ax_osc, self.oscillator_artists.append
        oscillator = OSCILLATORS[self.oscillator_var.get()]()
        result = oscillator.compute(self.bars)
        self.oscillator_values = result

        levels = sorted(oscillator.levels)
        if len(levels) >= 2:
            add(ax.axhspan(levels[0], levels[-1], color=to_rgba(next(iter(oscillator.lines.values())).color, 0.08)))
        for level in levels:
            add(ax.axhline(level, color="#9e9e9e", linewidth=0.8, linestyle="--"))
        handles = []
        for column, style in oscillator.lines.items():
            (line,) = ax.plot(range(len(self.bars)), result[column], color=style.color, linewidth=style.width, label=style.label)
            handles.append(line)
            add(line)
        add(ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=9, ncols=len(handles)))

        if oscillator.ylim:
            ax.set_ylim(*oscillator.ylim)
        if levels:
            ax.set_yticks(levels)

    def _draw_condition(self):
        """選択中のコンディションを最下段に描く。状態ごとに 1 行を使い、成り立つ足に色を付ける。"""
        for artist in self.condition_artists:
            artist.remove()
        self.condition_artists = []
        self.condition_values = None
        if self.ax_cond is None:
            return
        ax = self.ax_cond
        condition = CONDITIONS[self.condition_var.get()]()
        result = condition.compute(self.bars)
        self.condition_values = result

        states = list(condition.states.items())
        for row, (state, style) in enumerate(reversed(states)):
            marks = [
                ((i - 0.5, row + 0.15), (i - 0.5, row + 0.85), (i + 0.5, row + 0.85), (i + 0.5, row + 0.15))
                for i in result.index.get_indexer(result.index[result[state]])
            ]
            self.condition_artists.append(ax.add_collection(
                PolyCollection(marks, facecolors=style.color, edgecolors=style.color, linewidths=0.8)
            ))
        ax.set_ylim(0, len(states))
        ax.set_yticks([row + 0.5 for row in range(len(states))])
        ax.set_yticklabels([style.label for _, style in reversed(states)], fontsize=8)
        ax.tick_params(axis="y", length=0)
        ax.grid(False, axis="y")
        self.condition_artists.append(ax.text(
            0.005, 0.5, condition.name, transform=ax.transAxes, fontsize=8, va="center", color="#555555",
        ))

        # 判定の根拠の線。価格チャートと、表示中のオシレーター(名前が一致するもの)に描く
        oscillator_name = OSCILLATORS[self.oscillator_var.get()].name if self.ax_osc else None
        for link in condition.links(self.bars):
            if link.pane == "price":
                target = self.ax
            elif link.pane == oscillator_name:
                target = self.ax_osc
            else:
                continue
            (x0, y0), (x1, y1) = [(self._x_of(date), value) for date, value in (link.start, link.end)]
            (line,) = target.plot(
                [x0, x1], [y0, y1], color=condition.states[link.state].color, linewidth=1.6,
                marker="o", markersize=3.5, zorder=LINK_ZORDER,
            )
            self.condition_artists.append(line)

    # ---- 売買ポイント ----

    def set_trades(self, marks: list[TradeMark]) -> None:
        """売買ポイントを価格チャートに重ねる。他のプログラム(fina_bktest.py など)から使う。

        表示中の銘柄の売買を渡す。銘柄を変えると(set_code)、売買ポイントは消える。
        """
        self.trade_marks = list(marks)
        self.selected_trade = None
        if self.bars is not None:
            self._draw_trades()
            self.canvas.draw_idle()

    def select_trade(self, number: int | None) -> None:
        """trade_marks の number 番目の売買を選び、利確・損切りの線を出して、その売買が見える位置へ動かす。"""
        self.selected_trade = number
        if self.bars is None:
            return
        self._draw_trades()
        if number is None:
            self.canvas.draw_idle()
            return
        mark = self.trade_marks[number]
        self.show_range(mark.entry_date, mark.exit_date or self.bars.index[-1])

    def show_range(self, start: pd.Timestamp, end: pd.Timestamp) -> None:
        """start から end までが真ん中に見えるように、表示範囲を動かす。短い範囲は INITIAL_BARS 本で見せる。"""
        if self.bars is None:
            return
        x0, x1 = self._x_of(start), self._x_of(end)
        width = max(x1 - x0 + TRADE_VIEW_MARGIN * 2, INITIAL_BARS)
        center = (x0 + x1) / 2
        self._set_view(center - width / 2, center + width / 2)

    def _draw_trades(self):
        """売買ポイントを描く。買いは ▲、売りは ▼(終値の位置)、買いと売りを点線で結ぶ(勝ちは緑、負けは赤)。"""
        for artist in self.trade_artists:
            artist.remove()
        self.trade_artists = []
        if not self.trade_marks:
            return
        ax, add = self.ax, self.trade_artists.append
        buys, sells = [], []
        for number, mark in enumerate(self.trade_marks):
            x0 = self._x_of(mark.entry_date)
            buys.append((x0, mark.entry_price))
            x1 = len(self.bars) - 1
            if mark.exit_date is not None:
                x1 = self._x_of(mark.exit_date)
                sells.append((x1, mark.exit_price))
                color = UP_COLOR if mark.exit_price >= mark.entry_price else DOWN_COLOR
                (line,) = ax.plot([x0, x1], [mark.entry_price, mark.exit_price], color=color,
                                  linewidth=1.2, linestyle=":", zorder=TRADE_ZORDER)
                add(line)
            if number == self.selected_trade:
                # 選んだ売買の利確・損切りの値を、保有した期間に横線で示す
                for value, color, label in ((mark.take_profit, UP_COLOR, "利確"), (mark.stop_loss, DOWN_COLOR, "損切り")):
                    if value is None or not math.isfinite(value) or value <= 0:
                        continue
                    (line,) = ax.plot([x0, x1], [value, value], color=color, linewidth=1.2,
                                      linestyle="--", zorder=TRADE_ZORDER)
                    add(line)
                    add(ax.text(x1 + 0.5, value, f" {label} {value:,.1f}", color=color, fontsize=8,
                                va="center", zorder=TRADE_ZORDER))
                (ring,) = ax.plot([x0], [mark.entry_price], marker="o", markersize=14, markerfacecolor="none",
                                  markeredgecolor=BUY_COLOR, markeredgewidth=1.5, zorder=TRADE_ZORDER)
                add(ring)
        for points, marker, color in ((buys, "^", BUY_COLOR), (sells, "v", SELL_COLOR)):
            if points:
                xs, ys = zip(*points)
                (line,) = ax.plot(xs, ys, linestyle="none", marker=marker, markersize=9, color=color,
                                  markeredgecolor="white", markeredgewidth=0.8, zorder=TRADE_ZORDER)
                add(line)

    # ---- 表示範囲(スクロールと拡大・縮小) ----

    def _set_view(self, lo: float, hi: float) -> None:
        """横軸の表示範囲を決め、見えている足に合わせて縦軸を調整する。"""
        xmin, xmax = -LEFT_MARGIN, len(self.bars) + RIGHT_MARGIN
        width = min(max(hi - lo, MIN_BARS), xmax - xmin)
        lo = min(max(lo, xmin), xmax - width)
        hi = lo + width
        self.ax.set_xlim(lo, hi)

        rows = slice(max(math.ceil(lo), 0), max(math.floor(hi) + 1, 0))
        visible = self.bars.iloc[rows]
        if not visible.empty:
            low, high = visible["Low"].min(), visible["High"].max()
            # 見えているインジケーターの線も、縦軸の範囲に収める
            if self.indicator_values is not None:
                lines = self.indicator_values.iloc[rows].to_numpy().ravel()
                lines = lines[~pd.isna(lines)]
                if lines.size:
                    low, high = min(low, lines.min()), max(high, lines.max())
            pad = (high - low) * 0.05 or high * 0.01 or 1
            self.ax.set_ylim(low - pad, high + pad)
            if self.ax_vol:
                self.ax_vol.set_ylim(0, max(visible["Volume"].max(), 1) * 1.1)
            if self.ax_osc and self.oscillator_values is not None:
                oscillator = OSCILLATORS[self.oscillator_var.get()]
                if oscillator.ylim is None:
                    # 縦軸の範囲が決まっていないオシレーターは、見えている値に合わせる
                    values = self.oscillator_values.iloc[rows].to_numpy().ravel()
                    values = values[~pd.isna(values)]
                    if values.size:
                        pad = (values.max() - values.min()) * 0.05 or 1
                        self.ax_osc.set_ylim(values.min() - pad, values.max() + pad)

        self.scrollbar.set((lo - xmin) / (xmax - xmin), (hi - xmin) / (xmax - xmin))
        self.canvas.draw_idle()

    def _on_scrollbar(self, action, value, unit=None):
        if self.bars is None:
            return
        lo, hi = self.ax.get_xlim()
        width = hi - lo
        if action == "moveto":
            lo = float(value) * (len(self.bars) + LEFT_MARGIN + RIGHT_MARGIN) - LEFT_MARGIN
        else:
            lo += int(value) * width * (0.9 if unit == "pages" else 0.1)
        self._set_view(lo, lo + width)

    def _on_scroll(self, event):
        """マウスホイールで、マウス位置を中心に拡大・縮小する。"""
        if self.bars is None:
            return
        lo, hi = self.ax.get_xlim()
        scale = 0.8 if event.button == "up" else 1.25
        anchor = event.xdata if event.xdata is not None else hi
        self._set_view(anchor - (anchor - lo) * scale, anchor + (hi - anchor) * scale)

    def _format_date(self, x, _pos):
        i = round(x)
        if self.bars is None or not 0 <= i < len(self.bars):
            return ""
        return self.bars.index[i].strftime(TIMEFRAMES[self.timeframe_var.get()][1])

    # ---- マウスとキーの操作 ----

    def _on_press(self, event):
        self.canvas.get_tk_widget().focus_set()
        if self.bars is None:
            return
        if event.button == 3:
            self._on_right_click(event)
        elif event.button == 1:
            if self.tool_var.get() == "cursor":
                self._on_cursor_press(event)
            elif event.inaxes is self.ax:
                self._on_draw_click(event)

    def _on_right_click(self, event):
        if self.pending:
            # パスは右クリックで確定、それ以外は作図の取り消し
            if self.pending["type"] == "path":
                self._finish_pending()
            else:
                self._cancel_pending()
            return
        if event.inaxes is self.ax:
            drawing = self._drawing_at(event.x, event.y)
            if drawing:
                self._remove_drawing(drawing)

    def _on_cursor_press(self, event):
        """カーソルモード: 図形の上なら選択して移動を始め、何もない所ならスクロールを始める。"""
        drawing = self._drawing_at(event.x, event.y) if event.inaxes is self.ax else None
        self.selected = drawing
        if drawing:
            self.drag = {
                "drawing": drawing,
                "start": self._data_at(event),
                "points": [(self._x_of(d), p) for d, p in drawing["points"]],
            }
        else:
            self.pan_start = (event.x, self.ax.get_xlim())
        self._render_drawings()

    def _on_draw_click(self, event):
        tool = self.tool_var.get()
        if self.pending is None:
            self.pending = {"type": tool, "points": [], "color": self.color}
        if tool == "path" and event.dblclick:
            # ダブルクリックの 1 回目で点は追加済みなので、ここでは確定だけを行う
            self._finish_pending()
            return
        self.pending["points"].append(self._snap(event))
        self.preview = None
        if len(self.pending["points"]) == POINTS_NEEDED[tool]:
            self._finish_pending()
        else:
            self._render_drawings()

    def _on_release(self, _event):
        moved = bool(self.drag and self.drag.get("moved"))
        self.pan_start = None
        self.drag = None
        if moved:  # 図形を動かし終わったら保存する
            self._save_chart()

    def _on_motion(self, event):
        if self.bars is None:
            return
        if self.drag:
            self._move_drawing(event)
        elif self.pan_start:
            press_x, (lo, hi) = self.pan_start
            shift = (press_x - event.x) * (hi - lo) / self.ax.bbox.width
            self._set_view(lo + shift, hi + shift)
        elif self.pending and event.inaxes is self.ax:
            # 作図中の図形を、マウス位置まで仮に描く
            self.preview = self._snap(event)
            self._render_drawings()
        self._update_crosshair(event)
        self._show_info(event)

    def _update_crosshair(self, event):
        """十字線がオンで、マウスが価格チャートの上にあるときだけ十字線を出す。"""
        if not (self.crosshair_var.get() and event.inaxes is self.ax):
            self._hide_crosshair()
            return
        # 縦線は一番近い足の中心に合わせる
        x = min(max(round(event.xdata), 0), len(self.bars) - 1)
        for line in self.crosshair_v:
            line.set_xdata([x, x])
            line.set_visible(True)
        self.crosshair_h.set_ydata([event.ydata, event.ydata])
        self.crosshair_h.set_visible(True)
        self.canvas.draw_idle()

    def _hide_crosshair(self):
        if self.crosshair_h.get_visible():
            for line in self.crosshair_v + [self.crosshair_h]:
                line.set_visible(False)
            self.canvas.draw_idle()

    def _on_escape(self):
        if self.pending:
            self._cancel_pending()
        elif self.selected:
            self.selected = None
            self._render_drawings()

    def _on_tool_change(self):
        self._cancel_pending()

    def _show_info(self, event):
        if event.xdata is None:
            return
        i = round(event.xdata)
        if not 0 <= i < len(self.bars):
            return
        bar = self.bars.iloc[i]
        text = (
            f"{self.bars.index[i]:%Y-%m-%d}   始 {bar['Open']:,.1f}   高 {bar['High']:,.1f}   "
            f"安 {bar['Low']:,.1f}   終 {bar['Close']:,.1f}   出来高 {int(bar['Volume']):,}"
        )
        if self.indicator_values is not None:
            for column, value in self.indicator_values.iloc[i].items():
                if not pd.isna(value):
                    text += f"   {column} {value:,.1f}"
        if self.oscillator_values is not None:
            for column, value in self.oscillator_values.iloc[i].items():
                if not pd.isna(value):
                    text += f"   {column} {value:,.1f}"
        if self.condition_values is not None:
            row = self.condition_values.iloc[i]
            condition = CONDITIONS[self.condition_var.get()]
            for state, style in condition.states.items():
                if row[state]:
                    text += f"   【{condition.name}: {style.label}】 {row.get('note', '')}"
        if event.inaxes is self.ax:
            text += f"      カーソル {event.ydata:,.1f}"
        self.info_var.set(text)

    # ---- 座標の変換 ----

    def _data_at(self, event) -> tuple[float, float]:
        """マウス位置をチャートの座標(足の番号、価格)に直す。チャートの外でも使える。"""
        return tuple(self.ax.transData.inverted().transform((event.x, event.y)))

    def _snap(self, event) -> tuple[pd.Timestamp, float]:
        """マウス位置を(一番近い足の日付、価格)に直す。"""
        x, y = self._data_at(event)
        i = min(max(round(x), 0), len(self.bars) - 1)
        return self.bars.index[i], y

    def _x_of(self, date: pd.Timestamp) -> int:
        """日付を、表示中の足の番号に直す(週足・月足では、その日を含む足)。"""
        return max(int(self.bars.index.searchsorted(date, side="right")) - 1, 0)

    # ---- 図形の描画 ----

    def _render_drawings(self):
        for artist in self.drawing_artists:
            artist.remove()
        self.drawing_artists = []
        for drawing in self.drawings:
            self._render(drawing["type"], drawing["points"], drawing["color"])
        if self.pending:
            points = self.pending["points"] + ([self.preview] if self.preview else [])
            self._render(self.pending["type"], points, self.pending["color"])
        if self.selected is not None:
            self._render_handles(self.selected)
        self.canvas.draw_idle()

    def _render(self, kind: str, points: list, color: str) -> None:
        if not points:
            return
        xy = [(self._x_of(date), price) for date, price in points]
        ax, add = self.ax, self.drawing_artists.append
        line = {"color": color, "linewidth": LINE_WIDTH}
        if kind == "hline":
            add(ax.axhline(xy[0][1], **line))
        elif kind == "vline":
            add(ax.axvline(xy[0][0], **line))
        elif len(xy) < 2:
            return  # 残りの図形は 2 点以上で描く
        elif kind == "rect":
            (x0, y0), (x1, y1) = xy[0], xy[-1]
            add(ax.add_patch(Rectangle(
                (min(x0, x1), min(y0, y1)), abs(x1 - x0), abs(y1 - y0),
                facecolor=to_rgba(color, 0.12), edgecolor=color, linewidth=LINE_WIDTH,
            )))
        elif kind == "channel" and len(xy) == 3:
            base, parallel = channel_lines(xy)
            add(ax.add_patch(Polygon(
                [base[0], base[1], parallel[1], parallel[0]], facecolor=to_rgba(color, 0.12), edgecolor="none",
            )))
            for (x0, y0), (x1, y1) in (base, parallel):
                add(ax.plot([x0, x1], [y0, y1], **line)[0])
            # 中央線
            mid = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(base, parallel)]
            add(ax.plot([mid[0][0], mid[1][0]], [mid[0][1], mid[1][1]], color=color, linewidth=0.8, linestyle="--")[0])
        elif kind == "path":
            if len(xy) > 2:
                add(ax.plot([x for x, _ in xy[:-1]], [y for _, y in xy[:-1]], **line)[0])
            add(ax.add_patch(FancyArrowPatch(
                xy[-2], xy[-1], arrowstyle="-|>", mutation_scale=16,
                color=color, linewidth=LINE_WIDTH, shrinkA=0, shrinkB=0,
            )))
        else:
            # トレンドライン(作図中の平行チャンネルも、基準線だけを描く)
            add(ax.plot([xy[0][0], xy[1][0]], [xy[0][1], xy[1][1]], **line)[0])

    def _render_handles(self, drawing: dict) -> None:
        """選択中の図形の点に、白丸の印を付ける。"""
        xy = [(self._x_of(date), price) for date, price in drawing["points"]]
        (handles,) = self.ax.plot(
            [x for x, _ in xy], [y for _, y in xy], "o", markersize=7,
            markerfacecolor="white", markeredgecolor=drawing["color"], markeredgewidth=1.5, zorder=5,
        )
        self.drawing_artists.append(handles)

    # ---- 図形の編集 ----

    def _finish_pending(self):
        """作図中の図形を確定する。点が足りなければ取り消す。"""
        if not self.pending:
            return
        needed = POINTS_NEEDED[self.pending["type"]] or MIN_PATH_POINTS
        if len(self.pending["points"]) >= needed:
            self.drawings.append(self.pending)
            self.selected = self.pending
            self.tool_var.set("cursor")
            self.pending = self.preview = None
            self._save_chart()
        self.pending = self.preview = None
        self._render_drawings()

    def _cancel_pending(self):
        self.pending = self.preview = None
        self._render_drawings()

    def _move_drawing(self, event):
        """ドラッグ量だけ図形を動かす。横方向は足の単位で動かし、チャートの外には出さない。"""
        drag = self.drag
        x, y = self._data_at(event)
        dx = round(x - drag["start"][0])
        dy = y - drag["start"][1]
        xs = [i for i, _ in drag["points"]]
        dx = min(max(dx, -min(xs)), len(self.bars) - 1 - max(xs))
        drag["drawing"]["points"] = [(self.bars.index[i + dx], p + dy) for i, p in drag["points"]]
        drag["moved"] = True
        self._render_drawings()

    def set_color(self, color: str) -> None:
        """図形の色を変える。選択中の図形があれば、その色も変える。"""
        self.color = color
        self.color_indicator.configure(bg=color)
        try:
            update_settings(draw_color=color)  # 描く色は全銘柄で 1 つ
        except OSError:
            pass
        if self.selected:
            self.selected["color"] = color
            self._render_drawings()
            self._save_chart()

    def _choose_color(self):
        _, color = colorchooser.askcolor(self.color, parent=self, title="図形の色")
        if color:
            self.set_color(color)

    def _remove_drawing(self, drawing: dict) -> None:
        # 同じ形の図形が複数あっても、この図形そのものを消す(== ではなく is で比べる)
        self.drawings[:] = [d for d in self.drawings if d is not drawing]
        if self.selected is drawing:
            self.selected = None
        self._render_drawings()
        self._save_chart()

    def delete_selected(self) -> None:
        """選択中の図形を消す。"""
        if self.selected is not None:
            self._remove_drawing(self.selected)

    def clear_drawings(self) -> None:
        """表示中の銘柄の図形をすべて消す。"""
        self.drawings.clear()
        self.selected = None
        self._cancel_pending()
        self._save_chart()

    # ---- 銘柄ごとの保存(charts/<コード>.json) ----

    def _display_now(self) -> dict:
        """今の表示の設定。"""
        return {
            "timeframe": self.timeframe_var.get(), "volume": self.volume_var.get(),
            "oscillator_on": self.oscillator_on_var.get(), "oscillator": self.oscillator_var.get(),
            "condition_on": self.condition_on_var.get(), "condition": self.condition_var.get(),
            "crosshair": self.crosshair_var.get(),
        }

    def _apply_display(self, display: dict) -> None:
        """表示の設定を画面の欄に入れる(描き直しは呼び出し側で行う)。"""
        self.timeframe_var.set(display["timeframe"])
        self.volume_var.set(display["volume"])
        self.oscillator_on_var.set(display["oscillator_on"])
        self.oscillator_var.set(display["oscillator"])
        self.condition_on_var.set(display["condition_on"])
        self.condition_var.set(display["condition"])
        self.crosshair_var.set(display["crosshair"])
        self.oscillator_box.configure(state="readonly" if display["oscillator_on"] else "disabled")
        self.condition_box.configure(state="readonly" if display["condition_on"] else "disabled")

    def _on_display_changed(self, key: str) -> None:
        """利用者が表示の設定を変えた。描き直して、銘柄のファイルに保存する。

        変えた項目だけを「利用者が選んだ表示」に入れる(スクリーニングが表示させたコンディションなど、
        他のプログラムが変えた表示を保存しないため)。
        """
        self.saved_display[key] = self._display_now()[key]
        if key == "timeframe":
            self._show_bars()
        elif key == "crosshair":
            self._hide_crosshair()
        else:
            self.oscillator_box.configure(state="readonly" if self.oscillator_on_var.get() else "disabled")
            self.condition_box.configure(state="readonly" if self.condition_on_var.get() else "disabled")
            self._rebuild_panes()
        self._save_chart()

    def _load_chart(self, code: str) -> None:
        """銘柄のファイルから、表示の設定と図形を読む。ファイルがなければ既定の表示・図形なし。"""
        data = load_chart(code)
        saved = data.get("drawings") if data else None
        self.drawings_by_code[code] = [d for d in map(_parse_drawing, saved if isinstance(saved, list) else []) if d]
        self.saved_display = valid_display(data.get("display") if data else None, chart_defaults())
        self._apply_display(self.saved_display)
        self.loaded_mtime = chart_mtime(code)

    def _save_chart(self) -> None:
        """表示中の銘柄の表示と図形を、銘柄のファイルに保存する。

        表示は既定と違う項目だけを書く(既定を変えたら、変えていない項目は新しい既定に従う)。
        図形がなく、表示も既定のままなら、ファイルを消す。
        """
        if not self.persist or not self.code:
            return
        defaults = chart_defaults()
        display = {k: v for k, v in self.saved_display.items() if v != defaults.get(k)}
        drawings = self.drawings_by_code.get(self.code, [])
        try:
            if not drawings and not display:
                delete_chart(self.code)
            else:
                save_chart(self.code, display or None, serialize_drawings(drawings))
        except OSError as e:
            self.info_var.set(f"チャートを保存できません: {e}")
        self.loaded_mtime = chart_mtime(self.code)

    def _reload_if_changed(self) -> None:
        """ほかのウィンドウが同じ銘柄のファイルを書き換えていたら、読み直して描き直す。"""
        if not self.persist or not self.code or self.pending or self.drag:
            return
        if chart_mtime(self.code) == self.loaded_mtime:
            return
        timeframe = self.timeframe_var.get()
        self._load_chart(self.code)
        self.drawings = self.drawings_by_code.setdefault(self.code, [])
        self.selected = None
        if self.daily is None:
            return
        if self.timeframe_var.get() != timeframe:
            self._build_axes()
            self._show_bars()
        else:
            self._rebuild_panes()

    def save_as_default(self) -> None:
        """今の表示の設定を、チャートの既定(ToreCan の設定)にする。"""
        try:
            update_settings(chart_defaults=self._display_now())
        except OSError as e:
            self.info_var.set(f"既定を保存できません: {e}")
            return
        self.saved_display = self._display_now()
        self._save_chart()  # 既定と同じになった表示は、銘柄のファイルから外れる
        self.info_var.set("今の表示をチャートの既定にしました(ファイルのない銘柄は、この表示で開きます)。")

    # ---- 状態の保存と復元(前の版の形。watchlist.json からの引き継ぎに使う) ----

    def get_state(self) -> dict:
        """表示の設定と全銘柄の図形を、JSON に保存できる形で返す。他のプログラムから使う。"""
        return {
            "timeframe": self.timeframe_var.get(),
            "volume": self.volume_var.get(),
            "oscillator_on": self.oscillator_on_var.get(),
            "oscillator": self.oscillator_var.get(),
            "condition_on": self.condition_on_var.get(),
            "condition": self.condition_var.get(),
            "crosshair": self.crosshair_var.get(),
            "color": self.color,
            "drawings": {
                code: serialize_drawings(drawings) for code, drawings in self.drawings_by_code.items() if drawings
            },
        }

    def set_state(self, state: dict) -> None:
        """get_state() で得た状態に戻す。

        手で書き換えたファイルや古い版のファイルでも止まらないよう、
        使えない値(なくなったオシレーターの名前など)は無視して、今の設定を残す。
        """
        choices = {
            "timeframe": (self.timeframe_var, TIMEFRAMES),
            "oscillator": (self.oscillator_var, OSCILLATORS),
            "condition": (self.condition_var, CONDITIONS),
        }
        for key, (var, valid) in choices.items():
            if isinstance(state.get(key), str) and state[key] in valid:
                var.set(state[key])
        switches = {
            "volume": self.volume_var,
            "oscillator_on": self.oscillator_on_var,
            "condition_on": self.condition_on_var,
            "crosshair": self.crosshair_var,
        }
        for key, var in switches.items():
            if isinstance(state.get(key), bool):
                var.set(state[key])
        self.oscillator_box.configure(state="readonly" if self.oscillator_on_var.get() else "disabled")
        self.condition_box.configure(state="readonly" if self.condition_on_var.get() else "disabled")
        if _is_hex_color(state.get("color")):
            self.color = state["color"]
            self.color_indicator.configure(bg=self.color)

        saved = state.get("drawings")
        self.drawings_by_code = {
            code: [d for d in map(_parse_drawing, drawings) if d]
            for code, drawings in (saved.items() if isinstance(saved, dict) else ())
            if isinstance(drawings, list)
        }
        if self.code:
            self.drawings = self.drawings_by_code.setdefault(self.code, [])
        self.selected = self.pending = self.preview = self.drag = self.pan_start = None
        self._build_axes()
        if self.daily is None:
            self.canvas.draw_idle()
        else:
            self._show_bars()

    # ---- 図形の当たり判定 ----

    def _drawing_at(self, px: float, py: float) -> dict | None:
        """画面上の位置 (px, py) にある図形を返す(後から描いたものを優先)。"""
        for drawing in reversed(self.drawings):
            if self._hits(drawing, (px, py)):
                return drawing
        return None

    def _hits(self, drawing: dict, p: tuple[float, float]) -> bool:
        to_pixels = self.ax.transData.transform
        xy = [(self._x_of(date), price) for date, price in drawing["points"]]
        pts = [tuple(v) for v in to_pixels(xy)]
        kind = drawing["type"]
        if kind == "hline":
            return abs(p[1] - pts[0][1]) <= PICK_PIXELS
        if kind == "vline":
            return abs(p[0] - pts[0][0]) <= PICK_PIXELS
        if kind == "rect":
            (x0, y0), (x1, y1) = pts
            return (min(x0, x1) - PICK_PIXELS <= p[0] <= max(x0, x1) + PICK_PIXELS
                    and min(y0, y1) - PICK_PIXELS <= p[1] <= max(y0, y1) + PICK_PIXELS)
        if kind == "channel":
            base, parallel = (tuple(map(tuple, to_pixels(line))) for line in channel_lines(xy))
            if MplPath([base[0], base[1], parallel[1], parallel[0]]).contains_point(p):
                return True
            segments = [base, parallel]
        else:
            # トレンドラインとパス
            segments = list(zip(pts, pts[1:]))
        return any(segment_distance(p, a, b) <= PICK_PIXELS for a, b in segments)


def main() -> int:
    root = tk.Tk()
    apply_theme(root)  # ToreCan の色・フォント(gui_theme.py)
    root.title(app_title("chart"))
    root.geometry("1200x820")
    ChartFrame(root, sys.argv[1] if len(sys.argv) > 1 else None).pack(fill="both", expand=True)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
