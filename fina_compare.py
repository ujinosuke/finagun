"""銘柄の指標・値動き・相関・業績を比べる。

設計は fina_compare_design.md、仕様は compare-extension-spec.md を参照。
画面は 3 つのビュー(タブ)に分かれる。銘柄の追加・削除・更新は、どのビューからでも全ビューに効く。

比較表
    行に銘柄、列に 銘柄 / コード / 市場 / 株価 / 時価総額 と、次のグループの列を出す
    (表示するグループは「表示する列」のチェックで選ぶ。「値動き」は最初は隠している)。
        バリュエーション  EV/Rev / EV/EBITDA / PER(実績) / PER(予想) / PBR / PSR / 配当利回り(yfinance から取得)
        騰落率            1か月 / 3か月 / 6か月 / 1年
        トレンド          52週高値比 / 52週安値比 / 移動平均(25・75・200 日)からの乖離率
        値動き            RSI(14) / ボラティリティ(20・60 日、年率) / ATR(14)% / β(1年) / 売買代金(20 日平均)
    騰落率から右の列(テクニカル)は、stock_database フォルダの保存済み日足から計算する(外部からは取得しない)。
    価格は終値だけを使う。例外は 52 週高値・安値(日中の高値・安値)と ATR(高値・安値・終値)。
    表の最後の 4 行は、指標の列ごとの High(最大)・Average(平均)・Median(中央値)・Low(最小)。
    バリュエーションの列は、マイナスの値(赤字の PER など)を計算から除き、一番高い値を赤い文字で出す。
    テクニカルの列は、マイナスの値も含めて計算する(騰落率や乖離率のマイナスは意味のある値のため)。
相関
    銘柄 × 銘柄の、日次の終値の前日比リターンの相関係数(+1 に近いほど青、-1 に近いほど赤)。
    2 銘柄ごとに、両方に値がある日だけでそろえて計算する。マスの下の数字は、計算に使った日数。
    使える日数が足りない・値動きがないときは「算出不能」。期間は 3か月 / 6か月 / 1年 / 全期間。
    ベンチマーク(TOPIX 連動の 1306 などの ETF)を先頭の行に出し、右端に各銘柄のベータを出す。
    ベンチマークを「なし」にすると、ベンチマークの行とベータ(比較表の β の列も)を出さない。
業績推移
    yfinance の年次の財務諸表から、売上高・営業利益・純利益(financials_cache.json に 30 日キャッシュ)。
    決算月が違う銘柄をそろえるため、西暦年ではなく「何期前か」で並べる。
    グラフ: 各銘柄の実額(億円)の棒グラフ(期ごとに銘柄を並べる)。折れ線: 一番古い期を 100 にした指数の折れ線。
    数値表: 実額 / 前期比 / 営業利益率。

株価データの中に、数日だけ桁の違う値(分割の調整の誤りなど)があると、その日を除いて計算する
(除いた日は、下のお知らせ欄に出す)。

銘柄はグループに分けて登録する。グループはウォッチリスト(fina_watchlist.py)と共有で、watchlist.json に保存する
(どちらで変えても、もう片方に出る。同時に開いているときは、ウィンドウに戻ってきたときに読み直す)。
一番上の「保有資産(自動)」は固定のグループで、資産管理(asset.json)で今保有している日本株が、
評価額の大きい順に自動で入る(全証券会社・口座をまとめる。銘柄の追加・削除は資産管理で行う)。
保有資産のときは、比較表に「保有」の列(保有数・評価額・構成比・含み損益率)も出せる。
仕様は compare-groups-spec.md。

選んでいるグループと表示の設定は、ルートディレクトリの compare.json に保存し、次回の起動時に自動で読み込む
(前の版の compare.json にあったグループは、最初に読んだときに watchlist.json に移す)。
ベンチマークと相関の期間は、全プログラム共通の settings.json に保存する(ToreCan のホームの設定と同じ値)。

使い方:
    python fina_compare.py                    # 前回のグループで起動
    python fina_compare.py 7203 7267          # 指定した銘柄を、選んでいるグループに追加して起動
                                              # (保有資産を選んでいるときは、先頭のふつうのグループに追加)
    python fina_compare.py --group 保有資産   # グループを選んで起動

操作:
    グループ    上の欄で選ぶ。「新規」「名前変更」「削除」で編集する(保有資産は編集できない)
    銘柄の登録  「銘柄の追加」欄に銘柄コードまたは銘柄名の一部を入力すると、候補が出る。
                上下キーで選んで Enter、または候補をダブルクリックで登録する
    並べ替え    見出しをクリック(もう一度クリックで逆順)。集計の 4 行は常に最後
    銘柄の削除  比較表で行を選んで「削除」ボタン、または Delete キー(Ctrl・Shift で複数選べる)
    コピー      比較表で Ctrl+C。選んだ行(選んでいなければ表全体)を、見えている列だけ見出し付きでコピーする。
                タブ区切りなので、表計算ソフトにそのまま貼り付けられる
    Excel 出力  「Excel 出力」ボタン。画面で隠している列も含め、取れている情報をすべて出す
                (比較表・相関(4 期間)・ベータ・業績・情報 の 5 つのシート)
    更新        「更新」ボタンで、指標を取り直し、保存済みの日足を読み直す(業績は 30 日ごとに自動で取り直す。
                業績推移の「業績を取り直す」ボタンですぐ取り直せる)
    チャート    比較表の行をダブルクリック(または選んで Enter)で、その銘柄のチャート(gui_chart.py)を
                別のウィンドウで開く。相関のマスをダブルクリックすると、2 銘柄のチャートを開く。
                チャートに描いた図形は保存しない
"""

import json
import math
import os
import queue
import statistics
import sys
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np
import yfinance as yf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from gui_theme import apply_theme
from core_settings import app_title, load_settings, profit_colors, settings_exist, update_settings
from core_compare import (
    BENCHMARK_CHOICES, BENCHMARKS, CORRELATION_PERIODS, ITEMS, NO_BENCHMARK, Correlation, Financials,
    FinancialsCache, TechMetrics, aligned_years, benchmark_label, beta, cagr, correlation_matrix, drop_glitches,
    fetch_financials, growth, indexed, technical_metrics,
)
from core_asset import ASSET_PATH, AssetClass, AssetStore, build_ledger
from core_asset_value import PriceBook, value_all
from core_gather import BASE_DIR, csv_path_for, load_saved
from core_stocks import load_listed, stock_index
from core_watchlist import (
    WATCHLIST_PATH, RESERVED_NAMES, add_codes, create_group, delete_group, load_groups, merge_groups, name_error,
    remove_codes, rename_group, watchlist_mtime,
)
from gui_chart import ChartFrame  # gui_chart を読み込むと、matplotlib の日本語フォントも設定される
from gui_corrmatrix import CorrMatrix, MatrixLabel, correlation_color
from gui_widgets import SortableTree, StockSearchBox

APP_NAME = "fina_compare"
COMPARE_PATH = BASE_DIR / "compare.json"
FORMAT_VERSION = 3  # 3: グループは watchlist.json に持つ(2 は groups、1 は codes だけを compare.json に持っていた)
WORKERS = 4          # 指標・業績を同時に取得する銘柄数
POLL_INTERVAL = 100  # 取得結果を画面に反映する間隔(ミリ秒)
MISSING = "—"        # 値がないときの表示
MAX_YEARS = 5        # 業績で並べる期の数の上限

# 表の列(列の名前 -> (見出し、幅、文字の寄せ方))
COLUMNS = {
    "name": ("銘柄", 220, "w"),
    "code": ("コード", 64, "center"),
    "market": ("市場", 90, "center"),
    "price": ("株価", 90, "e"),
    "market_cap": ("時価総額(億円)", 110, "e"),
    "ev_rev": ("EV/Rev", 84, "e"),
    "ev_ebitda": ("EV/EBITDA", 92, "e"),
    "per": ("PER(実績)", 84, "e"),
    "forward_per": ("PER(予想)", 84, "e"),
    "pbr": ("PBR", 70, "e"),
    "psr": ("PSR", 84, "e"),
    "dividend_yield": ("配当利回り", 92, "e"),
    "ret_1m": ("1か月", 76, "e"),
    "ret_3m": ("3か月", 76, "e"),
    "ret_6m": ("6か月", 76, "e"),
    "ret_1y": ("1年", 76, "e"),
    "high52": ("52週高値比", 90, "e"),
    "low52": ("52週安値比", 90, "e"),
    "dev25": ("乖離25日", 80, "e"),
    "dev75": ("乖離75日", 80, "e"),
    "dev200": ("乖離200日", 84, "e"),
    "rsi14": ("RSI(14)", 70, "e"),
    "vol20": ("ボラ20日", 80, "e"),
    "vol60": ("ボラ60日", 80, "e"),
    "atr14": ("ATR(14)%", 80, "e"),
    "beta": ("β(1年)", 70, "e"),
    "turnover20": ("売買代金(百万円)", 120, "e"),
}
BASE_COLUMNS = ["name", "code", "market", "price", "market_cap"]  # 常に表示する列
# 集計しない値の列(列の名前 -> yfinance の info の項目名)
FIELDS = {
    "price": "currentPrice",
    "market_cap": "marketCap",
}
# 指標の列(列の名前 -> yfinance の info の項目名)。集計の 4 行を出し、一番高い値を赤くする
METRICS = {
    "ev_rev": "enterpriseToRevenue",
    "ev_ebitda": "enterpriseToEbitda",
    "per": "trailingPE",
    "forward_per": "forwardPE",
    "pbr": "priceToBook",
    "psr": "priceToSalesTrailing12Months",
    "dividend_yield": "dividendYield",  # yfinance 1.x では % の値(2.92 なら 2.92%)
}
# テクニカルの列(保存済みの日足から計算する)。集計の 4 行はマイナスも含めて出す。赤い文字は付けない
TECH_COLUMNS = list(COLUMNS)[len(BASE_COLUMNS) + len(METRICS):]
# 保有の列(保有資産のグループのときだけ出す)。列の名前 -> (見出し、幅、文字の寄せ方)
HOLDING_COLUMNS = {
    "hold_qty": ("保有数", 80, "e"),
    "hold_value": ("評価額(円)", 110, "e"),
    "hold_weight": ("構成比", 70, "e"),
    "hold_unrealized": ("含み損益率", 90, "e"),
}
# 列のグループ(グループの名前 -> (表示名、列))
COLUMN_GROUPS = {
    "valuation": ("バリュエーション", list(METRICS)),
    "returns": ("騰落率", ["ret_1m", "ret_3m", "ret_6m", "ret_1y"]),
    "trend": ("トレンド", ["high52", "low52", "dev25", "dev75", "dev200"]),
    "motion": ("値動き", ["rsi14", "vol20", "vol60", "atr14", "beta", "turnover20"]),
    "holding": ("保有", list(HOLDING_COLUMNS)),  # 保有資産のグループのときだけ
}
DEFAULT_GROUPS = ["valuation", "returns", "trend", "holding"]  # 「値動き」は最初は隠す
COLUMNS |= HOLDING_COLUMNS  # 表の列には常に持ち、表示するかは表示する列(displaycolumns)で決める
HOLDINGS_GROUP = RESERVED_NAMES[0]  # 固定のグループの名前「保有資産」(保存しない)
HOLDINGS_LABEL = "保有資産(自動)"   # グループの欄での表示
LEGACY_GROUP_NAME = "比較リスト"    # 前の版(グループのない版)の銘柄を入れるグループ
SIGNED_PERCENT = {"ret_1m", "ret_3m", "ret_6m", "ret_1y", "dev25", "dev75", "dev200"}  # +1.23% の形
PERCENT = {"high52", "low52", "vol20", "vol60", "atr14"}                               # 1.23% の形
# 集計の行(行の名前 -> 計算のしかた)
SUMMARIES = {
    "High": max,
    "Average": statistics.mean,
    "Median": statistics.median,
    "Low": min,
}
SUMMARY_PREFIX = "summary:"  # 集計の行の iid の頭(銘柄コードと区別する)

# 業績の数値表の種類
FIN_TABLES = {"amount": "実額", "growth": "前期比", "margin": "営業利益率"}
FIN_MODES = {"bar": "グラフ", "line": "折れ線", "table": "数値表"}  # 業績推移の表示(横に並べる順)
BAR_LABEL_LIMIT = 30  # 棒グラフで、棒の数がこれ以下なら棒に数値を書く
# 業績のグラフの色(dataviz の分類の配色。銘柄の登録順に固定で割り当てる)。
# 9 銘柄目からは、同じ色合いの濃い色、17 銘柄目からは薄い色を使う(25 銘柄目からは最初に戻る)
LINE_COLORS = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948",  # 1〜8 銘柄目
    "#1b4f8f", "#a8461f", "#0f7a55", "#a87200", "#b04f7a", "#4d6b00", "#3d2f9e", "#9e2b2b",  # 9〜16 銘柄目(濃い)
    "#8fb8ea", "#f5ad8c", "#7fd6b5", "#f5cd6e", "#f2b3cd", "#7fc17f", "#aaa0ea", "#f09a99",  # 17〜24 銘柄目(薄い)
]
LINE_STYLES = ["-", "--", ":"]  # 折れ線の線の種類(1〜8・9〜16・17〜24 銘柄目)。似た色でも見分けられるように


def series_style(n: int) -> tuple[str, str]:
    """n 番目(0 から)の銘柄の (色, 折れ線の線の種類)。"""
    return LINE_COLORS[n % len(LINE_COLORS)], LINE_STYLES[n % len(LINE_COLORS) // 8]
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID_COLOR = "#e6e5e1"

# Excel 出力での数値の表示形式と列の幅(列の名前 -> (表示形式, 幅))
EXCEL_FORMATS = {
    "name": (None, 28),
    "code": (None, 8),
    "market": (None, 12),
    "price": ("#,##0.0", 11),
    "market_cap": ("#,##0", 15),
    "ev_rev": ("#,##0.00", 10),
    "ev_ebitda": ("#,##0.00", 12),
    "per": ("#,##0.00", 11),
    "forward_per": ("#,##0.00", 11),
    "pbr": ("#,##0.00", 9),
    "psr": ("#,##0.00", 10),
    "dividend_yield": ("0.00%", 12),
    **{c: ("+0.00%;-0.00%;0.00%", 10) for c in SIGNED_PERCENT},
    "high52": ("0.0%", 12), "low52": ("0.0%", 12),
    "rsi14": ("0.0", 9), "vol20": ("0.0%", 10), "vol60": ("0.0%", 10), "atr14": ("0.00%", 10),
    "beta": ("0.00", 8), "turnover20": ("#,##0", 16),
    "hold_qty": ("#,##0", 9), "hold_value": ("#,##0", 13), "hold_weight": ("0.0%", 9),
    "hold_unrealized": ("+0.0%;-0.0%;0.0%", 11),
}
# Excel の比較表に足す補助の列(列の名前 -> (見出し、表示形式、幅))。画面にはない値
EXCEL_EXTRAS = {
    "base_date": ("株価の基準日", None, 12),
    "high52": ("52週高値(円)", "#,##0.0", 13),
    "low52": ("52週安値(円)", "#,##0.0", 13),
    "atr14": ("ATR(14)(円)", "#,##0.0", 12),
    "sma25": ("25日移動平均(円)", "#,##0.0", 16),
    "sma75": ("75日移動平均(円)", "#,##0.0", 16),
    "sma200": ("200日移動平均(円)", "#,##0.0", 17),
}
EXCEL_RED = "D00000"         # 一番高い値の文字の色
EXCEL_HEADER_FILL = "DDE3EA"  # 見出しの背景
EXCEL_SUMMARY_FILL = "EEF1F5"  # 集計の行の背景(画面と同じ)


def excel_value(column: str, value: float | None):
    """Excel のセルに入れる値。時価総額は億円、% の列は割合(2.92% なら 0.0292)にする。"""
    if value is None:
        return None
    if column == "market_cap":
        return value / 1e8
    if column in ("dividend_yield", "hold_weight", "hold_unrealized") or column in SIGNED_PERCENT or column in PERCENT:
        return value / 100
    return value


@dataclass(frozen=True)
class Quote:
    """1 銘柄の株価と指標。values は 列の名前 -> 値 で、値がない項目は None。"""
    values: dict[str, float | None] | None = None
    long_name: str = ""   # Yahoo の銘柄名(銘柄一覧にない銘柄の表示に使う)
    error: str = ""       # 取得に失敗したときの理由


def to_number(value) -> float | None:
    """info の値を数値に直す。数値でない・無限大・NaN は None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def fetch_quote(code: str) -> Quote:
    """yfinance から 1 銘柄の株価と指標を取得する(取得用のスレッドで動く)。"""
    try:
        info = yf.Ticker(f"{code}.T").info
    except Exception as e:  # noqa: BLE001  通信の失敗など。画面に出すだけにする
        return Quote(error=f"{type(e).__name__}: {e}")
    if not info:
        return Quote(error="Yahoo にデータがありません")
    values = {column: to_number(info.get(key)) for column, key in (FIELDS | METRICS).items()}
    if values["price"] is None:
        values["price"] = to_number(info.get("regularMarketPrice"))
    if all(v is None for v in values.values()):
        # 存在しないコードでも、info は空にならず中身のない値が返る
        return Quote(error="Yahoo にデータがありません")
    return Quote(
        values=values,
        long_name=info.get("longName") or info.get("shortName") or "",
    )


def format_value(column: str, value: float | None) -> str:
    if value is None:
        return MISSING
    if column == "price":
        return f"{value:,.1f}"
    if column == "market_cap":
        return f"{value / 1e8:,.0f}"
    if column == "dividend_yield":
        return f"{value:.2f}%"
    if column in SIGNED_PERCENT:
        return f"{value:+.2f}%"
    if column == "atr14":
        return f"{value:.2f}%"
    if column in PERCENT:
        return f"{value:.1f}%"
    if column == "rsi14":
        return f"{value:.1f}"
    if column == "hold_qty":
        return f"{value:,.10g}"
    if column == "hold_value":
        return f"{value:,.0f}"
    if column == "hold_weight":
        return f"{value:.1f}%"
    if column == "hold_unrealized":
        return f"{value:+.1f}%"
    if column == "turnover20":
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def safe_name(text: str) -> str:
    """ファイル名に使えない文字を _ にする。"""
    return "".join("_" if c in '\\/:*?"<>|' else c for c in text).strip() or "group"


def format_oku(value: float) -> str:
    """億円の値の表示(100 以上は整数、それより小さいと小数 1 桁)。"""
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.1f}"


def period_labels(count: int) -> list[str]:
    """業績の期の見出し(古い順)。"""
    return [f"{k}期前" if k else "直近期" for k in reversed(range(count))]


def fiscal_month(end: str) -> str:
    """決算期末 "2026-03-31" -> "3月期"。"""
    return f"{int(end[5:7])}月期"


def oku(value: float | None) -> float | None:
    """円 -> 億円。"""
    return None if value is None else value / 1e8


# ---- 保有資産(資産管理の保有から作る) ----

@dataclass(frozen=True)
class HoldingInfo:
    """保有資産の 1 銘柄(全証券会社・口座の合計)。"""
    quantity: float
    value: float | None    # 評価額(円)。株価データがなければ None
    cost: float            # 取得原価(円)
    weight: float | None   # 構成比(%)

    @property
    def unrealized_pct(self) -> float | None:
        if self.value is None or not self.cost:
            return None
        return (self.value - self.cost) / self.cost * 100


def load_holdings() -> dict[str, HoldingInfo]:
    """asset.json から、今保有している日本株を評価額の大きい順に返す(コード -> 保有)。なければ空。"""
    store = AssetStore(ASSET_PATH)
    try:
        store.load()
    except (OSError, ValueError):
        return {}
    ledger = build_ledger(store.trades, store.instruments)
    merged: dict[str, list] = {}  # コード -> [保有数、評価額(None あり)、取得原価]
    for v in value_all(ledger, store.instruments, PriceBook()):
        if v.instrument.asset_class != AssetClass.JP_STOCK or v.holding.quantity <= 0:
            continue
        item = merged.setdefault(v.instrument.code, [0.0, 0.0, 0.0])
        item[0] += v.holding.quantity
        item[1] = None if item[1] is None or v.value_jpy is None else item[1] + v.value_jpy
        item[2] += v.cost_jpy
    total = sum(value for _, value, _ in merged.values() if value is not None)
    ordered = sorted(merged.items(), key=lambda kv: -(kv[1][1] or 0))
    return {
        code: HoldingInfo(quantity, value, cost, value / total * 100 if value is not None and total else None)
        for code, (quantity, value, cost) in ordered
    }


def asset_mtime() -> float | None:
    try:
        return ASSET_PATH.stat().st_mtime
    except OSError:
        return None


# ---- 保存(compare.json) ----

def read_compare() -> dict:
    """compare.json を読み込み、項目ごとに形を確かめる(おかしな値は既定値にする)。ファイルがなければ既定値。

    前の版の compare.json にあったグループは "legacy_groups" に入れて返す(main で watchlist.json に移す)。
    選んでいるグループがあるかは、グループを読んでから main で確かめる。
    """
    state = {
        "selected_group": "", "view": "table", "column_groups": list(DEFAULT_GROUPS),
        "benchmark": next(iter(BENCHMARKS)), "corr_period": "1年",
        "fin_item": "revenue", "fin_mode": "bar", "fin_table": "amount", "legacy_groups": [],
    }
    if not COMPARE_PATH.exists():
        return with_global_settings(state)
    with open(COMPARE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("ファイルの形式が違います")
    if isinstance(data.get("groups"), list):  # 2 の版
        state["legacy_groups"] = legacy_groups(data["groups"])
    elif isinstance(data.get("codes"), list):  # 1 の版(グループのない版)の銘柄は、「比較リスト」のグループに入れる
        codes = list(dict.fromkeys(c for c in data["codes"] if isinstance(c, str) and c))
        state["legacy_groups"] = [{"name": LEGACY_GROUP_NAME, "codes": codes}] if codes else []
    if isinstance(data.get("selected_group"), str):
        state["selected_group"] = data["selected_group"]
    elif state["legacy_groups"]:
        state["selected_group"] = state["legacy_groups"][0]["name"]
    choices = {
        "view": ("table", "correlation", "financials"), "benchmark": tuple(BENCHMARK_CHOICES.values()),
        "corr_period": tuple(CORRELATION_PERIODS), "fin_item": tuple(ITEMS), "fin_mode": tuple(FIN_MODES),
        "fin_table": tuple(FIN_TABLES),
    }
    for key, allowed in choices.items():
        if data.get(key) in allowed:
            state[key] = data[key]
    if data.get("fin_mode") == "chart":  # 前の版の「グラフ」は、今の「折れ線」
        state["fin_mode"] = "line"
    groups = data.get("column_groups")
    if isinstance(groups, list):
        if data.get("version", 1) < 2:  # 前の版には「保有」の列がなかったので、最初は表示する
            groups = [*groups, "holding"]
        state["column_groups"] = [g for g in COLUMN_GROUPS if g in groups]
    # 前の版は compare.json にベンチマーク・相関の期間を持っていた。settings.json がまだなければ引き継ぐ
    if not settings_exist() and ("benchmark" in data or "corr_period" in data):
        try:
            update_settings(benchmark=state["benchmark"], corr_period=state["corr_period"])
        except OSError:
            pass
    return with_global_settings(state)


def legacy_groups(saved) -> list[dict]:
    """前の版の compare.json のグループを {"name": 名前, "codes": [...]} の一覧に直す(形の違うもの・重複は除く)。"""
    groups = []
    for group in saved if isinstance(saved, list) else []:
        if not isinstance(group, dict):
            continue
        name, codes = group.get("name"), group.get("codes")
        if not isinstance(name, str) or not name.strip() or any(g["name"] == name.strip() for g in groups):
            continue
        codes = [c for c in codes if isinstance(c, str) and c] if isinstance(codes, list) else []
        groups.append({"name": name.strip(), "codes": list(dict.fromkeys(codes))})
    return groups


def shared_groups(groups: list[dict]) -> list[dict]:
    """ウォッチリストと共有のグループのうち、銘柄比較で使うもの(固定のグループと同じ名前のものは除く)。"""
    return [g for g in groups if g["name"] not in RESERVED_NAMES]


def with_global_settings(state: dict) -> dict:
    """ベンチマークと相関の期間は、全体設定(settings.json)の値を使う(ToreCan のホームの設定と同じ値)。"""
    settings = load_settings()
    benchmarks = BENCHMARK_CHOICES.values()  # 「なし」(NO_BENCHMARK)も選べる
    state["benchmark"] = settings.benchmark if settings.benchmark in benchmarks else next(iter(BENCHMARKS))
    state["corr_period"] = settings.corr_period if settings.corr_period in CORRELATION_PERIODS else "1年"
    return state


GLOBAL_KEYS = ("benchmark", "corr_period")  # settings.json に持つ項目
NOT_SAVED_KEYS = (*GLOBAL_KEYS, "legacy_groups")  # compare.json に書かない項目


def write_compare(state: dict) -> None:
    """compare.json に保存する。途中で止まっても元のファイルが壊れないよう、別のファイルから置き換える。"""
    temporary = COMPARE_PATH.with_name(COMPARE_PATH.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        # ベンチマークと相関の期間は settings.json に、グループは watchlist.json に持つので、compare.json には書かない
        saved = {k: v for k, v in state.items() if k not in NOT_SAVED_KEYS}
        json.dump({"version": FORMAT_VERSION, **saved}, f, ensure_ascii=False, indent=2)
    os.replace(temporary, COMPARE_PATH)


# ---- 全体 ----

class CompareApp(ttk.Frame):
    """銘柄の登録・指標と業績の取得・保存を持ち、3 つのビュー(比較表・相関・業績推移)に配る。"""

    def __init__(self, master, settings: dict, groups: list[dict]):
        super().__init__(master)
        self.settings = settings  # compare.json の内容(ttk の state() と名前が重ならないよう settings にする)
        self.groups = shared_groups(groups)  # ふつうのグループ(watchlist.json。ウォッチリストと共有)
        self.groups_mtime = watchlist_mtime()
        self.listed = load_listed()
        self.holdings: dict[str, HoldingInfo] = load_holdings()  # 保有資産(資産管理の保有から)
        self.holding_codes: list[str] = list(self.holdings)
        self.asset_mtime = asset_mtime()
        self.quotes: dict[str, Quote] = {}       # 取得済みの銘柄だけが入る
        self.pending: set[str] = set()           # 指標を取得中の銘柄
        self.techs: dict[str, TechMetrics] = {}  # テクニカル指標
        self.dailies: dict[str, object] = {}     # 銘柄コード -> 異常値を除いた日足(なければ None)
        self.glitches: dict[str, list[str]] = {}  # 銘柄コード -> 異常値として除いた日
        self.financials = FinancialsCache()
        self.financials.load()
        self.fin_pending: set[str] = set()       # 業績を取得中の銘柄
        self.export_waiting = False              # 業績の取得が終わったら Excel 出力に進む
        self.queue = queue.Queue()               # 取得用のスレッドから画面への受け渡し
        self.pool = ThreadPoolExecutor(max_workers=WORKERS)
        self.chart_windows: dict[str, tk.Toplevel] = {}  # 銘柄コード -> 開いているチャートのウィンドウ

        self._build_group_bar()
        self._build_controls()
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(side="top", fill="both", expand=True, padx=4, pady=(6, 0))
        self.table = TableView(self.notebook, self)
        self.correlation = CorrelationView(self.notebook, self)
        self.financials_view = FinancialsView(self.notebook, self)
        self.views = {"table": self.table, "correlation": self.correlation, "financials": self.financials_view}
        for name, view in self.views.items():
            self.notebook.add(view, text={"table": "比較表", "correlation": "相関", "financials": "業績推移"}[name])
        self.notebook.select(self.views[settings["view"]])
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._on_view_changed())

        self.compute_tech(self.codes)
        self.fetch(self.codes)
        self._refresh_groups()
        # 資産管理で取引を入れたら、戻ってきたときに保有資産を作り直す。
        # ウォッチリストでグループや銘柄を変えたら、戻ってきたときに読み直す
        self.winfo_toplevel().bind("<FocusIn>", lambda e: self._reload_holdings_if_changed(), add="+")
        self.winfo_toplevel().bind("<FocusIn>", lambda e: self._reload_groups_if_changed(), add="+")
        self.after(POLL_INTERVAL, self._poll)

    # ---- グループ ----

    @property
    def is_holdings(self) -> bool:
        """保有資産(固定のグループ)を選んでいるか。"""
        return self.settings["selected_group"] == HOLDINGS_GROUP

    @property
    def group(self) -> dict | None:
        """選んでいるふつうのグループ(保有資産のときは None)。"""
        return next((g for g in self.groups if g["name"] == self.settings["selected_group"]), None)

    @property
    def codes(self) -> list[str]:
        """選んでいるグループの銘柄(比較表・相関・業績・Excel はこの銘柄で作る)。"""
        if self.is_holdings:
            return self.holding_codes
        group = self.group
        return group["codes"] if group is not None else []

    def _build_group_bar(self):
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=(4, 0))
        ttk.Label(bar, text="グループ").pack(side="left")
        self.group_box = ttk.Combobox(bar, state="readonly", width=24)
        self.group_box.pack(side="left", padx=(4, 8))
        self.group_box.bind("<<ComboboxSelected>>", lambda e: self._on_group_selected())
        self.group_buttons = []
        for text, command in (("新規", self.new_group), ("名前変更", self.rename_group), ("削除", self.delete_group)):
            button = ttk.Button(bar, text=text, command=command)
            button.pack(side="left", padx=(0, 4))
            self.group_buttons.append(button)
        self.group_note = tk.StringVar()
        ttk.Label(bar, textvariable=self.group_note, style="Muted.TLabel").pack(side="left", padx=(8, 0))

    def _group_names(self) -> list[str]:
        return [HOLDINGS_GROUP, *(g["name"] for g in self.groups)]

    def _refresh_groups(self) -> None:
        """グループの欄と、保有資産のときに使えない操作を、選んでいるグループに合わせる。"""
        labels = [HOLDINGS_LABEL, *(g["name"] for g in self.groups)]
        self.group_box.configure(values=labels)
        self.group_box.current(self._group_names().index(self.settings["selected_group"]))
        holdings = self.is_holdings
        for button in self.group_buttons[1:]:  # 名前変更・削除
            button.configure(state="disabled" if holdings else "normal")
        self.search.entry.configure(state="disabled" if holdings else "normal")
        for widget in self.search.bar.winfo_children():  # 銘柄の追加欄の「追加」ボタン
            if isinstance(widget, ttk.Button):
                widget.configure(state="disabled" if holdings else "normal")
        self.delete_button.configure(state="disabled" if holdings else "normal")
        self.group_note.set(
            ("資産管理の保有 " + f"{len(self.holding_codes)} 銘柄(評価額の大きい順)。銘柄の追加・削除は資産管理で行います"
             if self.holding_codes else "資産管理で保有を登録すると、ここに出ます")
            if holdings else ""
        )
        self.table.show_holding_group(holdings)

    def _on_group_selected(self) -> None:
        self.group_box.selection_clear()
        self.select_group(self._group_names()[self.group_box.current()])

    def select_group(self, name: str) -> None:
        """グループを選ぶ。まだ指標や株価を持っていない銘柄だけを取りに行く。"""
        if name not in self._group_names():
            return
        self.settings["selected_group"] = name
        self.table.sort_column, self.table.sort_reverse = None, False
        missing = [c for c in self.codes if c not in self.techs]
        self.compute_tech(missing)
        self.fetch([c for c in self.codes if c not in self.quotes])
        self._refresh_groups()
        self.save()
        self.refresh()

    def _ask_group_name(self, title: str, initial: str = "") -> str | None:
        """グループの名前を尋ねる。取り消し・使えない名前は None。"""
        name = (simpledialog.askstring(title, "グループの名前", initialvalue=initial, parent=self) or "").strip()
        if not name or name == initial:
            return None
        error = name_error(name, self.groups)
        if error:
            messagebox.showerror(APP_NAME, error, parent=self)
            return None
        return name

    def _apply(self, edit, *args) -> bool:
        """グループや銘柄を core_watchlist の関数 edit で変えて watchlist.json に保存する。できたら True。

        edit はファイルを読み直してから変えるので、ウォッチリストでの変更は消えない。
        """
        try:
            result = edit(*args)
            groups = result if isinstance(result, list) else load_groups()  # add_codes は足した数を返す
        except (OSError, ValueError) as e:
            messagebox.showerror(APP_NAME, f"{WATCHLIST_PATH.name} に保存できません。\n{e}", parent=self)
            return False
        self.groups = shared_groups(groups)
        self.groups_mtime = watchlist_mtime()
        return True

    def new_group(self) -> None:
        name = self._ask_group_name("グループの新規作成")
        if name and self._apply(create_group, name):
            self.select_group(name)

    def rename_group(self) -> None:
        group = self.group
        if group is None:
            return
        name = self._ask_group_name("グループの名前変更", group["name"])
        if name and self._apply(rename_group, group["name"], name):
            self.settings["selected_group"] = name if name in self._group_names() else HOLDINGS_GROUP
            self._refresh_groups()
            self.save()
            self.refresh()

    def delete_group(self) -> None:
        """選んでいるグループを消す(ウォッチリストからも消える)。最後の 1 つを消したときは、空のグループを作り直す。"""
        group = self.group
        if group is None:
            return
        message = f"グループ「{group['name']}」を削除しますか?(ウォッチリストからも消えます)"
        if group["codes"]:
            message = (f"グループ「{group['name']}」と、登録されている {len(group['codes'])} 銘柄を削除しますか?"
                       "(ウォッチリストからも消えます)")
        if not messagebox.askyesno(APP_NAME, message, parent=self):
            return
        index = self.groups.index(group)
        if self._apply(delete_group, group["name"]):
            self.select_group(self.groups[min(index, len(self.groups) - 1)]["name"] if self.groups else HOLDINGS_GROUP)

    def _reload_groups_if_changed(self) -> None:
        """ウォッチリストなどが watchlist.json を変えていたら、グループを読み直して表示し直す。"""
        mtime = watchlist_mtime()
        if mtime == self.groups_mtime:
            return
        try:
            groups = shared_groups(load_groups())
        except (OSError, ValueError):
            return  # 読めないときは、今の表示のまま(次に戻ってきたときにもう一度試す)
        self.groups_mtime = mtime
        if groups == self.groups:
            return
        index = next((i for i, g in enumerate(self.groups) if g is self.group), 0)
        self.groups = groups
        if not self.is_holdings and self.group is None:  # 選んでいたグループが消えた・名前が変わった
            self.settings["selected_group"] = groups[min(index, len(groups) - 1)]["name"] if groups else HOLDINGS_GROUP
            self.table.sort_column, self.table.sort_reverse = None, False
            self.save()
        self.compute_tech([c for c in self.codes if c not in self.techs])
        self.fetch([c for c in self.codes if c not in self.quotes])
        self._refresh_groups()
        self.refresh()

    def _reload_holdings_if_changed(self) -> None:
        """asset.json が変わっていたら、保有資産を作り直す(資産管理で取引を入れたとき)。"""
        mtime = asset_mtime()
        if mtime == self.asset_mtime:
            return
        self.asset_mtime = mtime
        self.holdings = load_holdings()
        self.holding_codes[:] = list(self.holdings)
        if self.is_holdings:
            self.compute_tech([c for c in self.codes if c not in self.techs])
            self.fetch([c for c in self.codes if c not in self.quotes])
            self._refresh_groups()
            self.refresh()

    def _build_controls(self):
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=(4, 0))
        ttk.Button(bar, text="Excel 出力", command=self.export_excel).pack(side="right", padx=(12, 0))
        self.delete_button = ttk.Button(bar, text="削除", width=5, command=self.remove_selected)
        self.delete_button.pack(side="right")
        self.refresh_button = ttk.Button(bar, text="更新", width=5, command=self.reload)
        self.refresh_button.pack(side="right", padx=(0, 4))
        self.status_var = tk.StringVar()
        self.search = StockSearchBox(
            bar, stock_index(self.listed),
            on_pick=lambda stock: self.add_stock(stock.code),
            on_message=self.status_var.set,
            label="銘柄の追加", button="追加",
        )
        self.search.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Label(self, textvariable=self.status_var, justify="left").pack(side="bottom", fill="x", padx=6, pady=4)

    # ---- 銘柄 ----

    def stock_name(self, code: str) -> str:
        name = self.listed["銘柄名"].get(code)
        if isinstance(name, str):
            return name
        quote = self.quotes.get(code)
        return quote.long_name if quote else ""

    def stock_market(self, code: str) -> str:
        market = self.listed["市場・商品区分"].get(code)
        return market.split("（")[0] if isinstance(market, str) else ""

    def add_stock(self, code: str) -> None:
        """銘柄を選んでいるグループに加えて指標を取得する。登録済みなら比較表で選ぶだけ。"""
        if self.is_holdings:
            self.status_var.set("保有資産のグループの銘柄は、資産管理の保有から自動で作られます(追加は資産管理で行います)。")
            return
        if code not in self.codes:
            if not self._apply(add_codes, self.group["name"], [code]):
                return
            self.compute_tech([code])
            self.fetch([code])
            self.refresh()
        self.table.select(code)

    def remove_selected(self) -> None:
        """比較表で選んでいる銘柄を外す(集計の行は外せない。保有資産のグループでは外せない)。"""
        if self.is_holdings:
            self.status_var.set("保有資産のグループの銘柄は、資産管理の保有から自動で作られます(削除は資産管理で行います)。")
            return
        removed = {iid for iid in self.table.tree.selection() if iid in self.codes}
        if not removed:
            self.status_var.set("削除する銘柄を、比較表で選んでください。")
            return
        if self._apply(remove_codes, self.group["name"], removed):
            self.refresh()

    @property
    def benchmark(self) -> str:
        return self.settings["benchmark"]

    def set_benchmark(self, code: str) -> None:
        """ベンチマークを変える(比較表の β も計算し直す)。"""
        self.settings["benchmark"] = code
        self.save_global(benchmark=code)
        self.compute_tech(self.codes)
        self.refresh()

    def save_global(self, **changes) -> None:
        """全体設定(settings.json)の一部を変えて保存する(ベンチマーク・相関の期間)。"""
        try:
            update_settings(**changes)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"settings.json に保存できません。\n{e}", parent=self)

    def save(self) -> None:
        if hasattr(self, "views"):  # 表示中のビューも保存する
            current = self.nametowidget(self.notebook.select())
            self.settings["view"] = next(n for n, v in self.views.items() if v is current)
        try:
            write_compare(self.settings)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"{COMPARE_PATH.name} に保存できません。\n{e}", parent=self)

    # ---- 保存済みの日足とテクニカル指標 ----

    def daily(self, code: str):
        """異常値を除いた日足(読み込んだものは覚えておく)。株価データがなければ None。"""
        if code not in self.dailies:
            try:
                daily = load_saved(csv_path_for(code))
            except (OSError, ValueError, KeyError):  # 壊れた CSV は、データなしとして扱う
                daily = None
            if daily is not None and "Close" in daily and len(daily.dropna(subset=["Close"])):
                daily, dropped = drop_glitches(daily.dropna(subset=["Close"]))
                self.glitches[code] = dropped
            else:
                daily = None
            self.dailies[code] = daily
        return self.dailies[code]

    def close(self, code: str):
        if code == NO_BENCHMARK:  # ベンチマーク「なし」
            return None
        daily = self.daily(code)
        return daily["Close"] if daily is not None else None

    def compute_tech(self, codes: list[str]) -> None:
        bench = self.close(self.benchmark)
        for code in codes:
            self.techs[code] = technical_metrics(self.daily(code), bench)

    def value(self, code: str, column: str) -> float | None:
        """比較表の値(指標は取得した値、テクニカルは計算した値、保有の列は資産管理の保有)。"""
        if column in HOLDING_COLUMNS:
            holding = self.holdings.get(code) if self.is_holdings else None
            if holding is None:
                return None
            return {"hold_qty": holding.quantity, "hold_value": holding.value, "hold_weight": holding.weight,
                    "hold_unrealized": holding.unrealized_pct}[column]
        if column in TECH_COLUMNS:
            tech = self.techs.get(code)
            return tech.values.get(column) if tech and not tech.error else None
        quote = self.quotes.get(code)
        if quote is None or quote.error:
            return None
        return quote.values.get(column)

    def reload(self) -> None:
        """指標を取り直し、保存済みの日足を読み直す。"""
        self.dailies.clear()
        self.glitches.clear()
        self.compute_tech(self.codes)
        self.fetch(self.codes)
        self.refresh()

    # ---- 取得(指標・業績) ----

    def fetch(self, codes: list[str]) -> None:
        """銘柄の指標を、取得用のスレッドで取得する(取得中の銘柄は除く)。"""
        for code in codes:
            if code in self.pending:
                continue
            self.pending.add(code)
            future = self.pool.submit(fetch_quote, code)
            # 閉じるときに取り消した取得は、結果を返さない
            future.add_done_callback(lambda f, c=code: f.cancelled() or self.queue.put(("quote", c, f.result())))
        self.refresh_status()

    def fetch_financials(self, codes: list[str]) -> None:
        """銘柄の業績を、取得用のスレッドで取得する(取得中の銘柄は除く)。"""
        for code in codes:
            if code in self.fin_pending:
                continue
            self.fin_pending.add(code)
            future = self.pool.submit(fetch_financials, code)
            future.add_done_callback(lambda f, c=code: f.cancelled() or self.queue.put(("financials", c, f.result())))
        self.refresh_status()

    def ensure_financials(self, force: bool = False) -> None:
        """業績のキャッシュが古い銘柄(force なら全銘柄)を取得する。"""
        codes = list(self.codes) if force else self.financials.stale_codes(self.codes)
        self.fetch_financials(codes)

    def _poll(self):
        """取得用のスレッドから届いた結果を反映する。"""
        changed = financials_done = False
        while True:
            try:
                kind, code, result = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "quote":
                self.pending.discard(code)
                self.quotes[code] = result  # 他のグループでも使うので、今のグループにない銘柄も覚えておく
                changed = True
            else:
                self.fin_pending.discard(code)
                self.financials.items[code] = result
                financials_done = not self.fin_pending
                changed = True
        if financials_done:
            try:
                self.financials.save()
            except OSError as e:
                self.status_var.set(f"{self.financials.path.name} に保存できません: {e}")
        if changed:
            self.refresh()
        if financials_done and self.export_waiting:
            self.export_waiting = False
            self.export_excel()
        self.after(POLL_INTERVAL, self._poll)

    def stop(self) -> None:
        """取得を止める(ウィンドウを閉じるときに呼ぶ)。"""
        self.pool.shutdown(wait=False, cancel_futures=True)

    # ---- 表示 ----

    def refresh(self) -> None:
        """比較表を作り直し、相関・業績推移は次に見えたときに作り直す。"""
        self.table.refresh()
        self.correlation.dirty = self.financials_view.dirty = True
        self._show_lazy_view()
        self.refresh_status()

    def _on_view_changed(self):
        self._show_lazy_view()
        self.save()

    def _show_lazy_view(self):
        current = self.nametowidget(self.notebook.select())
        if current in (self.correlation, self.financials_view) and current.dirty:
            current.dirty = False
            current.show()

    def refresh_status(self) -> None:
        if not self.codes:
            self.status_var.set("資産管理で保有を登録すると、保有資産のグループに出ます。" if self.is_holdings else
                                "「銘柄の追加」欄に銘柄コードまたは銘柄名を入力して、比べる銘柄を登録してください。")
            return
        lines = []
        text = f"{len(self.codes)} 銘柄"
        dates = {t.base_date for c in self.codes if (t := self.techs.get(c)) and t.base_date}
        if dates:
            text += f"   株価データ: {max(dates)} の終値"
        if self.pending:
            text += f"(指標を取得中: 残り {len(self.pending)} 銘柄)"
        if self.fin_pending:
            text += f"(業績を取得中: 残り {len(self.fin_pending)} 銘柄)"
        lines.append(text)
        failed = [c for c in self.codes if (q := self.quotes.get(c)) and q.error]
        if failed:
            reasons = "、".join(f"{c}: {self.quotes[c].error}" for c in failed[:3])
            lines.append(f"指標の取得に失敗した銘柄があります({reasons}{' ほか' if len(failed) > 3 else ''})。"
                         "「更新」で取り直せます")
        no_data = [c for c in self.codes if (t := self.techs.get(c)) and t.error]
        if no_data:
            lines.append(f"株価データがないため、テクニカルの列を出せない銘柄: {'、'.join(no_data[:5])}"
                         f"(python core_gather.py {' '.join(no_data[:5])} で取得してください)")
        old = [c for c in self.codes if (t := self.techs.get(c)) and t.base_date and dates and t.base_date < max(dates)]
        if old:
            lines.append("株価データが古い銘柄(売買停止など): " + "、".join(f"{c}({self.techs[c].base_date})" for c in old[:5]))
        glitched = [c for c in [*self.codes, self.benchmark] if self.glitches.get(c)]
        if glitched:
            lines.append("株価データの異常値(数日だけ桁の違う値)を除いて計算した銘柄: " + "、".join(
                f"{c}({self.glitches[c][0]}{' ほか' if len(self.glitches[c]) > 1 else ''})"
                for c in dict.fromkeys(glitched)))
        self.status_var.set("\n".join(lines))

    # ---- チャート ----

    def open_chart(self, code: str) -> None:
        """銘柄のチャート(gui_chart.py の ChartFrame)を別のウィンドウで開く。開いていれば前に出す。"""
        if not code or code.startswith(SUMMARY_PREFIX):  # 集計の行など
            return
        window = self.chart_windows.get(code)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_force()
            return
        if not csv_path_for(code).exists():
            # 空のウィンドウを開かないよう、先に確かめて理由を出す
            self.status_var.set(
                f"{f'{code} {self.stock_name(code)}'.strip()} の株価データがありません。"
                f"先に python core_gather.py {code} を実行してください。",
            )
            return
        window = tk.Toplevel(self)
        window.title(f"ToreCan chart - {code} {self.stock_name(code)}")
        window.geometry("1200x820")
        ChartFrame(window, code).pack(fill="both", expand=True)
        window.bind("<Destroy>", lambda e: e.widget is window and self.chart_windows.pop(code, None))
        self.chart_windows[code] = window

    # ---- Excel 出力 ----

    def export_excel(self) -> None:
        """取れている情報をすべて、Excel ファイル(.xlsx)に保存する。業績が未取得の銘柄は、先に取得する。"""
        if not self.codes:
            self.status_var.set("出力する銘柄がありません。")
            return
        missing = [c for c in self.codes if self.financials.get(c) is None]
        if missing or self.fin_pending:
            self.fetch_financials(missing)
            self.export_waiting = True
            self.status_var.set(f"出力の準備中…(業績を取得しています: 残り {len(self.fin_pending)} 銘柄)")
            return
        if self.pending and not messagebox.askyesno(
            APP_NAME, f"まだ指標を取得中の銘柄が {len(self.pending)} 銘柄あります。このまま出力しますか?", parent=self,
        ):
            return
        now = datetime.now()
        path = filedialog.asksaveasfilename(
            parent=self, title="Excel に出力",
            initialdir=BASE_DIR, initialfile=f"compare_{safe_name(self.settings['selected_group'])}_{now:%Y%m%d}.xlsx",
            defaultextension=".xlsx", filetypes=[("Excel ブック", "*.xlsx")],
        )
        if not path:
            return
        try:
            ExcelWriter(self, now).write(Path(path))
        except OSError as e:  # Excel で開いたままのファイルに上書きしようとした、など
            messagebox.showerror(APP_NAME, f"保存できません。\n{e}", parent=self)
            return
        self.status_var.set(f"{Path(path).name} に出力しました(比較表・相関・ベータ・業績・情報 の 5 シート)。")

    # ---- 相関・業績の共通の部品 ----

    def matrix_codes(self) -> list[str]:
        """相関に並べる銘柄(先頭がベンチマーク。株価データのないベンチマークは除く。二重には出さない)。"""
        bench = [self.benchmark] if self.close(self.benchmark) is not None else []
        return [*bench, *(c for c in self.codes if c != self.benchmark)]

    def label_of(self, code: str) -> MatrixLabel:
        if code == self.benchmark:
            return MatrixLabel(code, f"★{code}", BENCHMARKS.get(code, self.stock_name(code)))
        return MatrixLabel(code, code, self.stock_name(code))


# ---- ビュー: 比較表 ----

class TableView(ttk.Frame):
    """銘柄の比較表。表示する列のグループを選べる。"""

    def __init__(self, master, app: CompareApp):
        super().__init__(master)
        self.app = app
        self.sort_column, self.sort_reverse = None, False  # None は登録順

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=2, pady=(4, 2))
        ttk.Label(bar, text="表示する列:").pack(side="left")
        self.group_vars = {}
        self.group_checks = {}
        self.holding_group = False  # 保有資産のグループを表示しているか(保有の列を出せる)
        for name, (label, _columns) in COLUMN_GROUPS.items():
            var = tk.BooleanVar(value=name in app.settings["column_groups"])
            check = ttk.Checkbutton(bar, text=label, variable=var, command=self._on_groups_changed)
            check.pack(side="left", padx=(6, 0))
            self.group_vars[name] = var
            self.group_checks[name] = check

        frame = ttk.Frame(self)
        frame.pack(side="top", fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=list(COLUMNS), show="headings", selectmode="extended")
        for column, (heading, width, anchor) in COLUMNS.items():
            self.tree.heading(column, text=heading, command=lambda c=column: self._on_heading(c))
            self.tree.column(column, width=width, anchor=anchor, stretch=column == "name")
        self.tree.tag_configure("summary", background="#eef1f5")
        self.tree.tag_configure("error", foreground="#b22222")
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)

        def on_scroll(scrollbar):
            def handler(first, last):
                scrollbar.set(first, last)
                self._schedule_highlights()
            return handler

        self.tree.configure(yscrollcommand=on_scroll(y_scroll), xscrollcommand=on_scroll(x_scroll))
        y_scroll.pack(side="right", fill="y")
        x_scroll.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)
        self._apply_groups()

        # 一番高い値のセルに重ねて出す、赤い文字の欄(Treeview はセルごとに色を変えられないため)
        self.highlights: list[tk.Label] = []
        self.highlight_job = None
        self.highlight_font = ttk.Style().lookup("Treeview", "font") or "TkDefaultFont"
        self.tree.bind("<Configure>", lambda e: self._schedule_highlights(), add="+")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._schedule_highlights(), add="+")
        self.tree.bind("<B1-Motion>", lambda e: self._schedule_highlights(), add="+")  # 列の幅の変更
        self.tree.bind("<ButtonRelease-1>", lambda e: self._schedule_highlights(), add="+")

        self.tree.bind("<Delete>", lambda e: app.remove_selected())
        self.tree.bind("<Control-c>", lambda e: self.copy())
        self.tree.bind("<Control-a>", lambda e: self.tree.selection_set(app.codes))
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Return>", lambda e: app.open_chart(self.tree.focus()))

    # ---- 列のグループ ----

    def visible_columns(self) -> list[str]:
        """表示する列。保有の列は、保有資産のグループのときだけ、銘柄の基本の列のすぐ右に出す。"""
        columns = list(BASE_COLUMNS)
        if self.holding_group and self.group_vars["holding"].get():
            columns += list(HOLDING_COLUMNS)
        for name, (_label, group_columns) in COLUMN_GROUPS.items():
            if name != "holding" and self.group_vars[name].get():
                columns += group_columns
        return columns

    def show_holding_group(self, holdings: bool) -> None:
        """保有資産のグループかどうかに合わせて、「保有」のチェックと保有の列を出し入れする。"""
        self.holding_group = holdings
        check = self.group_checks["holding"]
        if holdings:
            check.pack(side="left", padx=(6, 0))
        else:
            check.pack_forget()
        self._apply_groups()

    def _apply_groups(self):
        self.tree.configure(displaycolumns=self.visible_columns())
        if hasattr(self, "highlights"):
            self._schedule_highlights()

    def _on_groups_changed(self):
        self.app.settings["column_groups"] = [n for n, v in self.group_vars.items() if v.get()]
        self.app.save()
        self._apply_groups()

    # ---- 表 ----

    def _row(self, code: str) -> tuple[str, ...]:
        quote = self.app.quotes.get(code)
        columns = (*FIELDS, *METRICS)
        if quote is None:
            numbers = ["取得中…"] + [""] * (len(columns) - 1)
        elif quote.error:
            numbers = ["取得失敗"] + [""] * (len(columns) - 1)
        else:
            numbers = [format_value(c, self.app.value(code, c)) for c in columns]
        tech = [format_value(c, self.app.value(code, c)) for c in TECH_COLUMNS]
        holding = [format_value(c, self.app.value(code, c)) for c in HOLDING_COLUMNS]
        return (self.app.stock_name(code), code, self.app.stock_market(code), *numbers, *tech, *holding)

    def sorted_codes(self) -> list[str]:
        column, codes = self.sort_column, self.app.codes
        if column is None:
            return list(codes)
        if column in ("name", "code", "market"):
            key = {"name": self.app.stock_name, "code": str, "market": self.app.stock_market}[column]
            return sorted(codes, key=key, reverse=self.sort_reverse)
        # 値がない銘柄は、昇順でも降順でも最後に置く
        with_value = [c for c in codes if self.app.value(c, column) is not None]
        without = [c for c in codes if self.app.value(c, column) is None]
        with_value.sort(key=lambda c: self.app.value(c, column), reverse=self.sort_reverse)
        return with_value + without

    def summary_values(self) -> dict[str, list[float]]:
        """集計に使う値(列の名前 -> 値の一覧)。

        バリュエーションの列は、マイナスの値(赤字の PER、EBITDA が赤字の EV/EBITDA など)を除く
        (倍率として比べられないため)。テクニカルの列は、マイナスの値も含める。
        """
        values = {}
        for column in (*METRICS, *TECH_COLUMNS):
            numbers = [v for c in self.app.codes if (v := self.app.value(c, column)) is not None]
            values[column] = [v for v in numbers if v >= 0] if column in METRICS else numbers
        return values

    def summary_rows(self) -> dict[str, tuple[str, ...]]:
        """集計の 4 行(行の名前 -> 表示する値)。"""
        values = self.summary_values()
        rows = {}
        for label, function in SUMMARIES.items():
            cells = [format_value(c, function(values[c]) if values[c] else None) for c in (*METRICS, *TECH_COLUMNS)]
            rows[label] = (label, "", "", *[""] * len(FIELDS), *cells, *[""] * len(HOLDING_COLUMNS))
        return rows

    def refresh(self):
        """並び順に合わせて、表を作り直す。"""
        selected = [iid for iid in self.tree.selection() if iid in self.app.codes]
        self.tree.delete(*self.tree.get_children())
        for code in self.sorted_codes():
            quote = self.app.quotes.get(code)
            tags = ["error"] if quote and quote.error else []
            self.tree.insert("", "end", iid=code, values=self._row(code), tags=tags)
        for label, values in self.summary_rows().items():
            self.tree.insert("", "end", iid=SUMMARY_PREFIX + label, values=values, tags=["summary"])
        if selected:
            self.tree.selection_set(selected)
        self._schedule_highlights()

    def select(self, code: str) -> None:
        if self.tree.exists(code):
            self.tree.selection_set(code)
            self.tree.see(code)

    # ---- 一番高い値の赤い文字(バリュエーションの列だけ) ----

    def colored_cells(self) -> list[tuple[str, str, str]]:
        """文字に色を付けるセル (銘柄コード, 列の名前, 色)。

        バリュエーションの列の一番高い値は赤。保有資産の含み損益率は、全体設定の色の決まりの利益・損失の色。
        """
        cells = [(code, column, "#d00000") for code, column in self.highest_cells()]
        if self.holding_group:
            plus, minus = profit_colors()
            for code in self.app.codes:
                value = self.app.value(code, "hold_unrealized")
                if value is not None and abs(value) >= 0.05:
                    cells.append((code, "hold_unrealized", plus if value > 0 else minus))
        return cells

    def highest_cells(self) -> list[tuple[str, str]]:
        """指標の列ごとに、一番高い値(集計の High と同じ値)の (銘柄コード, 列の名前) の一覧。同じ値は全部。"""
        cells = []
        values = self.summary_values()
        for column in METRICS:
            if values[column]:
                high = max(values[column])
                cells += [(code, column) for code in self.app.codes if self.app.value(code, column) == high]
        return cells

    def _schedule_highlights(self):
        """赤い文字の欄を、表の描き直しが終わってから置き直す(続けて呼ばれても 1 回にまとめる)。"""
        if getattr(self, "highlight_job", 0) is None:
            self.highlight_job = self.after_idle(self._place_highlights)

    def _place_highlights(self):
        """一番高い値のセルに、同じ値を赤い文字で出す欄を重ねる。見えていないセルの欄は隠す。"""
        self.highlight_job = None
        style = ttk.Style()
        selected = set(self.tree.selection())
        visible = set(self.visible_columns())
        cells = [(code, column, color) for code, column, color in self.colored_cells()
                 if self.tree.exists(code) and column in visible]
        while len(self.highlights) < len(cells):
            self.highlights.append(self._new_highlight())
        for label, (code, column, color) in zip(self.highlights, cells):
            bbox = self.tree.bbox(code, column)
            if not bbox:  # スクロールで見えていない
                label.place_forget()
                continue
            state = ["selected"] if code in selected else []
            label.code = code
            label.configure(
                text=self.tree.set(code, column),
                background=style.lookup("Treeview", "background", state) or "white",
                foreground=color,
            )
            x, y, width, height = bbox
            label.place(in_=self.tree, x=x + 1, y=y + 1, width=max(width - 2, 1), height=max(height - 2, 1))
        for label in self.highlights[len(cells):]:
            label.place_forget()

    def _new_highlight(self) -> tk.Label:
        """赤い文字の欄を 1 つ作る。欄の上のクリックは、下の表で受けたことにする。"""
        label = tk.Label(self.tree, anchor="e", padx=3, borderwidth=0, font=self.highlight_font)

        def forward(event, sequence):
            # 欄の左上から、表の中での位置に直して、同じ操作を表に送る
            x = event.x + label.winfo_x()
            y = event.y + label.winfo_y()
            self.tree.focus_set()
            self.tree.event_generate(sequence, x=x, y=y, state=event.state)
            return "break"

        for sequence in ("<Button-1>", "<ButtonRelease-1>", "<Button-3>"):
            label.bind(sequence, lambda e, s=sequence: forward(e, s))
        # ダブルクリックは送り直すと 2 回のクリックと区別できないので、直接チャートを開く
        label.bind("<Double-Button-1>", lambda e: self.app.open_chart(label.code) or "break")
        label.code = None
        label.bind("<MouseWheel>", lambda e: self.tree.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        return label

    # ---- 操作 ----

    def _on_heading(self, column: str):
        """見出しのクリックで並べ替える。同じ見出しをもう一度クリックすると逆順。"""
        self.sort_reverse = column == self.sort_column and not self.sort_reverse
        self.sort_column = column
        self.refresh()

    def _on_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell":  # 見出しのダブルクリックは並べ替えだけ
            self.app.open_chart(self.tree.identify_row(event.y))

    def copy(self) -> str:
        """選んだ行(選んでいなければ表全体)を、見えている列だけ、見出し付きのタブ区切りでクリップボードに入れる。"""
        rows = self.tree.selection() or self.tree.get_children()
        columns = self.visible_columns()
        indexes = [list(COLUMNS).index(c) for c in columns]
        lines = ["\t".join(COLUMNS[c][0] for c in columns)]
        for iid in rows:
            values = self.tree.item(iid, "values")
            lines.append("\t".join(str(values[i]) for i in indexes))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.app.status_var.set(f"{len(rows)} 行をコピーしました。")
        return "break"


# ---- ビュー: 相関 ----

class CorrelationView(ttk.Frame):
    """銘柄 × 銘柄の相関マトリクスと、ベンチマークに対するベータ。"""

    def __init__(self, master, app: CompareApp):
        super().__init__(master)
        self.app = app
        self.dirty = True

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=2, pady=(4, 4))
        ttk.Label(bar, text="期間").pack(side="left")
        self.period_var = tk.StringVar(value=app.settings["corr_period"])
        for name in CORRELATION_PERIODS:
            ttk.Radiobutton(bar, text=name, value=name, variable=self.period_var, command=self._on_period).pack(
                side="left", padx=(4, 0))
        ttk.Label(bar, text="ベンチマーク").pack(side="left", padx=(20, 4))
        self.bench_names = BENCHMARK_CHOICES  # 先頭に「なし」
        self.bench_var = tk.StringVar(value=benchmark_label(app.benchmark))
        box = ttk.Combobox(bar, textvariable=self.bench_var, values=list(self.bench_names), state="readonly", width=24)
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda e: self._on_benchmark())

        ttk.Label(self, foreground=TEXT_SECONDARY, justify="left", text=(
            "相関: +1 に近い(濃い青)ほど同じ方向に動きやすく、分散が効かない。-1 に近い(濃い赤)ほど逆に動きやすい。"
            "0 付近は無色。\nマスの下の数字は計算に使った日数(期間より少ないと橙色)。日次の終値の前日比リターンで計算し、"
            "2 銘柄で重なる期間に合わせる。β はベンチマークに対するベータ。マスのダブルクリックで 2 銘柄のチャートを開く。"
        )).pack(side="bottom", fill="x", padx=4, pady=4)
        self.note_var = tk.StringVar()
        ttk.Label(self, textvariable=self.note_var, foreground="#b25e00").pack(side="bottom", fill="x", padx=4)
        self.matrix = CorrMatrix(self, on_open=self._open)
        self.matrix.pack(side="top", fill="both", expand=True, padx=2)

    def show(self) -> None:
        app = self.app
        window = CORRELATION_PERIODS[self.period_var.get()]
        codes = app.matrix_codes()
        closes = {c: close for c in codes if (close := app.close(c)) is not None}
        cells = correlation_matrix(closes, window)
        bench = app.close(app.benchmark)
        extra = {c: beta(closes[c], bench, window) for c in closes if c != app.benchmark} if bench is not None else {}
        none = app.benchmark == NO_BENCHMARK  # ベンチマーク「なし」なら β の列を出さない
        self.matrix.set_data([app.label_of(c) for c in codes], cells, window, extra_title="" if none else "β",
                             extra=extra)
        notes = []
        if bench is None and not none:
            notes.append(f"ベンチマーク {app.benchmark} の株価データがありません(python core_gather.py {app.benchmark} で取得)。")
        missing = [c for c in codes if c not in closes]
        if missing:
            notes.append(f"株価データがない銘柄は算出不能になります: {'、'.join(missing)}")
        self.note_var.set("  ".join(notes))

    def _on_period(self):
        self.app.settings["corr_period"] = self.period_var.get()
        self.app.save_global(corr_period=self.period_var.get())
        self.show()

    def _on_benchmark(self):
        self.app.set_benchmark(self.bench_names[self.bench_var.get()])
        self.show()

    def _open(self, a: str, b: str):
        self.app.open_chart(a)
        if b != a:
            self.app.open_chart(b)


# ---- ビュー: 業績推移 ----

class FinancialsView(ttk.Frame):
    """売上高・営業利益・純利益の推移。グラフ(実額の棒)・折れ線(指数化)・数値表を切り替える。"""

    def __init__(self, master, app: CompareApp):
        super().__init__(master)
        self.app = app
        self.dirty = True

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=2, pady=(4, 4))
        self.mode_var = tk.StringVar(value=app.settings["fin_mode"])
        for value, text in FIN_MODES.items():
            ttk.Radiobutton(bar, text=text, value=value, variable=self.mode_var, command=self._on_change).pack(
                side="left", padx=(0, 6))
        ttk.Label(bar, text="項目").pack(side="left", padx=(14, 4))
        self.item_names = {name: key for key, name in ITEMS.items()}
        self.item_var = tk.StringVar(value=ITEMS[app.settings["fin_item"]])
        item_box = ttk.Combobox(bar, textvariable=self.item_var, values=list(self.item_names), state="readonly",
                                width=10)
        item_box.pack(side="left")
        item_box.bind("<<ComboboxSelected>>", lambda e: self._on_change())
        self.table_label = ttk.Label(bar, text="表示")
        self.table_names = {name: key for key, name in FIN_TABLES.items()}
        self.table_var = tk.StringVar(value=FIN_TABLES[app.settings["fin_table"]])
        self.table_box = ttk.Combobox(bar, textvariable=self.table_var, values=list(self.table_names),
                                      state="readonly", width=10)
        self.table_box.bind("<<ComboboxSelected>>", lambda e: self._on_change())
        ttk.Button(bar, text="業績を取り直す", command=lambda: app.ensure_financials(force=True)).pack(side="right")

        self.note_var = tk.StringVar()
        ttk.Label(self, textvariable=self.note_var, foreground=TEXT_SECONDARY, justify="left", wraplength=1200).pack(
            side="bottom", fill="x", padx=4, pady=4)
        self.body = ttk.Frame(self)
        self.body.pack(side="top", fill="both", expand=True)
        self.figure = Figure(figsize=(9, 5), dpi=100, layout="constrained")
        self.ax = self.figure.subplots()
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.body)
        self.table: SortableTree | None = None
        # マウスを重ねた棒・線の銘柄名と値を出す。(棒か線, マウスの x -> 出す文字) の一覧
        self.hover_items: list[tuple] = []
        self.hover_note = None
        self.canvas.mpl_connect("motion_notify_event", self._on_hover)

    def show(self) -> None:
        self.app.ensure_financials()
        is_table = self.mode_var.get() == "table"
        for widget in (self.table_label, self.table_box):
            widget.pack_forget()
        if is_table:
            self.table_label.pack(side="left", padx=(14, 4))
            self.table_box.pack(side="left")
        self.canvas.get_tk_widget().pack_forget()
        if self.table is not None:
            self.table.destroy()
            self.table = None
        if is_table:
            self._draw_table()
        else:
            self.canvas.get_tk_widget().pack(fill="both", expand=True)
            if self.mode_var.get() == "bar":
                self._draw_bars()
            else:
                self._draw_chart()

    def _on_change(self):
        state = self.app.settings
        state["fin_mode"] = self.mode_var.get()
        state["fin_item"] = self.item_names[self.item_var.get()]
        state["fin_table"] = self.table_names[self.table_var.get()]
        self.app.save()
        self.show()

    def _count(self) -> int:
        """並べる期の数(取れている期の数の最大。上限は MAX_YEARS)。"""
        lengths = [len(f.years) for c in self.app.codes if (f := self.app.financials.get(c))]
        return min(max(lengths, default=1), MAX_YEARS) or 1

    def _status_notes(self) -> list[str]:
        notes = []
        app = self.app
        if app.fin_pending:
            notes.append(f"業績を取得中… 残り {len(app.fin_pending)} 銘柄")
        failed = [f"{c}({f.error})" for c in app.codes if (f := app.financials.get(c)) and f.error]
        if failed:
            notes.append("業績を取得できなかった銘柄: " + "、".join(failed[:4]))
        foreign = [f"{c}({f.currency})" for c in app.codes if (f := app.financials.get(c)) and f.currency != "JPY"]
        if foreign:
            notes.append("円以外の通貨で決算している銘柄(金額はその通貨): " + "、".join(foreign))
        fetched = [f.fetched_at for c in app.codes if (f := app.financials.get(c))]
        if fetched:
            notes.append(f"業績の取得: {min(fetched).replace('T', ' ')[:16]}(30 日ごとに取り直します)")
        notes.append("会計基準(日本基準・IFRS)で利益の出方が違うため、金額より推移と成長率で比べてください。")
        return notes

    # ---- グラフ(棒) ----

    def _style_axes(self, title: str):
        """グラフの枠・目盛り・横の補助線をそろえる(棒と折れ線で共通)。"""
        ax = self.ax
        ax.clear()
        ax.set_title(title, loc="left", fontsize=11, color=TEXT_PRIMARY)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID_COLOR)
        ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
        ax.grid(axis="y", color=GRID_COLOR, linewidth=0.8)
        ax.set_axisbelow(True)
        # マウスを重ねたときの吹き出し(ax.clear() で消えるので、描くたびに作り直す)
        self.hover_items = []
        self.hover_note = ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords="offset points", fontsize=9, color=TEXT_PRIMARY,
            bbox={"boxstyle": "round,pad=0.4", "fc": "white", "ec": GRID_COLOR}, visible=False, zorder=10,
            multialignment="left",
        )
        return ax

    def _on_hover(self, event) -> None:
        """マウスを重ねた棒・線があれば、その銘柄名と値を吹き出しで出す。なければ消す。"""
        note = self.hover_note
        if note is None:
            return
        text = None
        if event.inaxes is self.ax and event.xdata is not None:
            for artist, describe in reversed(self.hover_items):  # 上に描いたものを優先
                if artist.contains(event)[0]:
                    text = describe(event.xdata)
                    break
        if text is None:
            if note.get_visible():
                note.set_visible(False)
                self.canvas.draw_idle()
            return
        # 右半分では吹き出しを左側に出す(枠からはみ出さないように)
        left = event.x > self.ax.bbox.x0 + self.ax.bbox.width / 2
        note.xy = (event.xdata, event.ydata)
        note.set_position((-12 if left else 12, 12))
        note.set_horizontalalignment("right" if left else "left")
        note.set_text(text)
        note.set_visible(True)
        self.canvas.draw_idle()

    @staticmethod
    def _legend(ax) -> None:
        """凡例。8 銘柄より多いと棒や線に重なるので、グラフの右の外に出す。"""
        many = len(ax.get_legend_handles_labels()[1]) > 8
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1) if many else None, frameon=False, fontsize=9,
                  labelcolor=TEXT_SECONDARY)

    def _draw_bars(self):
        """各銘柄の実額(億円)を、期ごとに銘柄を並べた棒グラフで出す(色は折れ線と同じ割り当て)。"""
        item = self.item_names[self.item_var.get()]
        count = self._count()
        ax = self._style_axes(f"{ITEMS[item]}(億円)")
        ax.axhline(0, color="#8a8984", linewidth=1)
        series = []  # (色, 名前, 期ごとの値(億円))
        for n, code in enumerate(self.app.codes):
            financials = self.app.financials.get(code)
            if financials is None or financials.error:
                continue
            values = [y.item(item) if y else None for y in aligned_years(financials, count)]
            if all(v is None for v in values):
                continue
            color = series_style(n)[0]
            series.append((color, f"{code} {self.app.stock_name(code)}", [None if v is None else v / 1e8 for v in values]))
        width = 0.8 / max(len(series), 1)
        labeled = len(series) * count <= BAR_LABEL_LIMIT
        labels = period_labels(count)
        for k, (color, name, values) in enumerate(series):
            xs = [i - 0.4 + width * (k + 0.5) for i in range(count)]
            pairs = [(x, v) for x, v in zip(xs, values) if v is not None]
            bars = ax.bar([x for x, _ in pairs], [v for _, v in pairs], width=width * 0.9, color=color, label=name)
            periods = [labels[i] for i, v in enumerate(values) if v is not None]
            for bar, period, (_, v) in zip(bars, periods, pairs):
                self.hover_items.append((bar, lambda _x, t=f"{name}\n{period}: {format_oku(v)} 億円": t))
            if labeled:
                ax.bar_label(bars, labels=[format_oku(v) for _, v in pairs], fontsize=8, color=TEXT_SECONDARY,
                             padding=2, rotation=90 if len(series) > 3 else 0)
        ax.set_xticks(range(count), labels)
        ax.set_xlim(-0.6, count - 0.4)
        if series:
            ax.margins(y=0.12)  # 棒の上の数値が枠からはみ出さないよう、上下に余白を取る
            self._legend(ax)
        elif not self.app.fin_pending:
            ax.text(0.5, 0.5, "表示できる業績がありません", transform=ax.transAxes, ha="center", va="center",
                    color=TEXT_SECONDARY)
        self.canvas.draw_idle()
        notes = [] if labeled or not series else [f"棒が {BAR_LABEL_LIMIT} 本より多いので、数値は省いています(数値表で見られます)。"]
        self.note_var.set("\n".join([*notes, *self._status_notes()]))

    # ---- 折れ線 ----

    def _draw_chart(self):
        item = self.item_names[self.item_var.get()]
        count = self._count()
        labels = period_labels(count)
        ax = self._style_axes(f"{ITEMS[item]}の推移(一番古い期 = 100)")
        ax.axhline(100, color="#8a8984", linewidth=1, linestyle=":")
        notes, drawn = [], []
        for n, code in enumerate(self.app.codes):
            years = aligned_years(self.app.financials.get(code), count)
            amounts = [y.item(item) if y else None for y in years]
            values, reason = indexed(amounts)
            name = f"{code} {self.app.stock_name(code)}"
            if self.app.financials.get(code) is None:
                continue
            if reason:
                notes.append(f"{name}: {reason}")
                continue
            color, style = series_style(n)
            ys = [np.nan if v is None else v for v in values]
            (line,) = ax.plot(range(count), ys, color=color, linewidth=2, linestyle=style,
                              marker="o", markersize=5, label=name)
            drawn.append((line, ys, name))
            self.hover_items.append((line, lambda x, name=name, ys=ys, amounts=amounts:
                                     self._line_text(name, labels, ys, amounts, x)))
        ax.set_xticks(range(count), labels)
        ax.set_xlim(-0.2, count - 1 + (0.9 if len(drawn) <= 4 else 0.2))
        if drawn:
            self._legend(ax)
            if len(drawn) <= 4:  # 4 銘柄以下なら、線の右端に名前も書く
                for line, ys, name in drawn:
                    last = next((i for i in range(len(ys) - 1, -1, -1) if not math.isnan(ys[i])), None)
                    if last is not None:
                        ax.annotate(f" {name[:12]}", (last, ys[last]), fontsize=9, color=TEXT_SECONDARY,
                                    va="center")
        elif not self.app.fin_pending:
            ax.text(0.5, 0.5, "表示できる業績がありません", transform=ax.transAxes, ha="center", va="center",
                    color=TEXT_SECONDARY)
        self.canvas.draw_idle()
        self.note_var.set("\n".join([*notes, *self._status_notes()]))

    @staticmethod
    def _line_text(name: str, labels: list[str], ys: list[float], amounts: list, x: float) -> str:
        """折れ線の吹き出しの文字(銘柄名と、マウスに一番近い期の指数・実額)。"""
        i = min(max(round(x), 0), len(ys) - 1)
        if math.isnan(ys[i]):
            return name
        amount = f"(実額 {format_oku(amounts[i] / 1e8)} 億円)" if amounts[i] is not None else ""
        return f"{name}\n{labels[i]}: {ys[i]:.0f}{amount}"

    # ---- 数値表 ----

    def _draw_table(self):
        kind = self.table_names[self.table_var.get()]
        item = self.item_names[self.item_var.get()]
        count = self._count()
        labels = period_labels(count)
        columns = {"code": ("コード", 60, "center"), "name": ("銘柄", 170, "w"), "month": ("決算月", 70, "center")}
        if kind == "amount":
            columns |= {f"p{i}": (f"{label}(億円)", 100, "e") for i, label in enumerate(labels)}
            columns |= {"yoy": ("前期比", 80, "e"), "cagr": (f"{count - 1}年CAGR", 86, "e")}
        elif kind == "growth":
            columns |= {f"p{i}": (label, 90, "e") for i, label in enumerate(labels) if i > 0}
        else:
            columns |= {f"p{i}": (label, 90, "e") for i, label in enumerate(labels)}
        self.table = SortableTree(self.body, columns, stretch="name", height=12)
        self.table.pack(fill="both", expand=True)
        rows = []
        for code in self.app.codes:
            financials = self.app.financials.get(code)
            years = aligned_years(financials, count)
            latest = next((y for y in reversed(years) if y), None)
            display = [code, self.app.stock_name(code), fiscal_month(latest.end) if latest else MISSING]
            keys = [code, display[1], latest.end[5:7] if latest else None]
            if kind == "amount":
                values = [y.item(item) if y else None for y in years]
                yoy = growth(values[-2], values[-1]) if count >= 2 else None
                rate = cagr(values[0], values[-1], count - 1)
                display += [MISSING if v is None else f"{v / 1e8:,.1f}" for v in values]
                display += [MISSING if yoy is None else f"{yoy:+.1f}%", MISSING if rate is None else f"{rate:+.1f}%"]
                keys += [*values, yoy, rate]
            elif kind == "growth":
                values = [y.item(item) if y else None for y in years]
                rates = [growth(values[i - 1], values[i]) for i in range(1, count)]
                display += [MISSING if r is None else f"{r:+.1f}%" for r in rates]
                keys += rates
            else:
                margins = [y.operating_margin if y else None for y in years]
                display += [MISSING if m is None else f"{m:.1f}%" for m in margins]
                keys += margins
            if financials is None:
                display[3:] = ["取得中…" if code in self.app.fin_pending else MISSING] * (len(display) - 3)
            rows.append((code, tuple(display), tuple(keys), ()))
        self.table.set_rows(rows)
        title = {"amount": f"{ITEMS[item]}の実額", "growth": f"{ITEMS[item]}の前期比",
                 "margin": "営業利益率(営業利益 ÷ 売上高。項目の欄には関係しません)"}[kind]
        self.note_var.set("\n".join([title + "。列は「何期前か」でそろえています(決算月は銘柄ごとに違います)。",
                                     *self._status_notes()]))


# ---- Excel 出力 ----

class ExcelWriter:
    """比較表・相関・ベータ・業績・情報の 5 シートを書く。"""

    def __init__(self, app: CompareApp, now: datetime):
        self.app = app
        self.now = now
        thin = Side(style="thin", color="B0B7C0")
        self.border = Border(top=thin, bottom=thin, left=thin, right=thin)

    def write(self, path: Path) -> None:
        book = Workbook()
        self._table(book.active)
        self._correlation(book.create_sheet("相関"))
        self._beta(book.create_sheet("ベータ"))
        self._financials(book.create_sheet("業績"))
        self._info(book.create_sheet("情報"))
        book.save(path)

    # ---- 共通 ----

    def _header(self, sheet, row: int, titles: list[str], widths: list[int] | None = None) -> None:
        for i, title in enumerate(titles, start=1):
            cell = sheet.cell(row=row, column=i, value=title)
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor=EXCEL_HEADER_FILL)
            cell.alignment = Alignment(horizontal="center", wrap_text=True)
            cell.border = self.border
            if widths:
                sheet.column_dimensions[get_column_letter(i)].width = widths[i - 1]

    def _cell(self, sheet, row: int, column: int, value, number_format: str | None = None, **style):
        cell = sheet.cell(row=row, column=column, value=value)
        cell.border = self.border
        if number_format:
            cell.number_format = number_format
        for key, item in style.items():
            setattr(cell, key, item)
        return cell

    # ---- 比較表 ----

    def _table(self, sheet):
        app = self.app
        sheet.title = "比較表"
        # 保有の列は、保有資産のグループのときだけ(銘柄の基本の列のすぐ右)
        columns = list(BASE_COLUMNS) + (list(HOLDING_COLUMNS) if app.is_holdings else []) + [
            c for c in COLUMNS if c not in BASE_COLUMNS and c not in HOLDING_COLUMNS]
        extras = list(EXCEL_EXTRAS)
        self._header(sheet, 1, [COLUMNS[c][0] for c in columns] + [EXCEL_EXTRAS[e][0] for e in extras],
                     [EXCEL_FORMATS[c][1] for c in columns] + [EXCEL_EXTRAS[e][2] for e in extras])
        colors = {(code, column): color.lstrip("#").upper() for code, column, color in app.table.colored_cells()}
        row = 2
        for code in app.table.sorted_codes():  # 画面の並び順
            quote = app.quotes.get(code)
            tech = app.techs.get(code)
            texts = {"name": app.stock_name(code), "code": code, "market": app.stock_market(code)}
            for i, column in enumerate(columns, start=1):
                if column in texts:
                    value = texts[column]
                elif column not in TECH_COLUMNS and (quote is None or quote.error):
                    value = ("取得中" if quote is None else "取得失敗") if column == "price" else None
                else:
                    value = excel_value(column, app.value(code, column))
                cell = self._cell(sheet, row, i, value, EXCEL_FORMATS[column][0])
                if column in ("code", "market"):
                    cell.alignment = Alignment(horizontal="center")
                if (code, column) in colors:
                    cell.font = Font(color=colors[(code, column)])
            for j, extra in enumerate(extras, start=len(columns) + 1):
                if tech is None or tech.error:
                    value = None
                elif extra == "base_date":
                    value = tech.base_date
                else:
                    value = tech.extras.get(extra)
                self._cell(sheet, row, j, value, EXCEL_EXTRAS[extra][1])
            row += 1

        values = app.table.summary_values()
        for label, function in SUMMARIES.items():
            for i, column in enumerate(columns + extras, start=1):
                if column == "name":
                    value = label
                elif column in values and values[column] and i <= len(columns):
                    value = excel_value(column, function(values[column]))
                else:
                    value = None
                number_format = EXCEL_FORMATS[column][0] if i <= len(columns) else None
                cell = self._cell(sheet, row, i, value, number_format,
                                  fill=PatternFill("solid", fgColor=EXCEL_SUMMARY_FILL))
                if column == "name":
                    cell.font = Font(bold=True)
            row += 1
        sheet.cell(row=row + 1, column=1, value=(
            f"出力: {self.now:%Y-%m-%d %H:%M}。バリュエーションは yfinance から取得、騰落率から右は保存済みの日足から計算。"
            "バリュエーションの集計はマイナスの値を除いて計算し、赤い文字は列で一番高い値。テクニカルの集計はマイナスも含む"
        )).font = Font(color="666666", size=9)
        sheet.freeze_panes = "B2"  # 見出しの行と銘柄名の列を固定する

    # ---- 相関 ----

    def _correlation(self, sheet):
        app = self.app
        codes = app.matrix_codes()
        closes = {c: close for c in codes if (close := app.close(c)) is not None}
        labels = [app.label_of(c) for c in codes]
        sheet.column_dimensions["A"].width = 26
        row = 1
        for period, window in CORRELATION_PERIODS.items():
            matrix = correlation_matrix(closes, window)
            for title, use_days in ((f"相関係数({period})", False), (f"計算に使った日数({period})", True)):
                sheet.cell(row=row, column=1, value=title).font = Font(bold=True)
                row += 1
                self._header(sheet, row, ["", *(f"{lab.code} {lab.name}" for lab in labels)])
                row += 1
                for a in labels:
                    self._cell(sheet, row, 1, f"{a.code} {a.name}", font=Font(bold=True))
                    for j, b in enumerate(labels, start=2):
                        if a.key == b.key:
                            value = "" if use_days else 1.0
                            fill = "E6E5E1"
                        else:
                            result = matrix.get((a.key, b.key), Correlation(None, 0))
                            if use_days:
                                value, fill = result.days, None
                            else:
                                value = result.value if result.value is not None else "算出不能"
                                fill = correlation_color(result.value).lstrip("#").upper()
                        cell = self._cell(sheet, row, j, value, "0.00" if not use_days else "#,##0")
                        if fill:
                            cell.fill = PatternFill("solid", fgColor=fill)
                    row += 1
                row += 1
        for j in range(2, len(labels) + 2):
            sheet.column_dimensions[get_column_letter(j)].width = 14
        sheet.cell(row=row, column=1, value=(
            "日次の終値の前日比リターンの相関。2 銘柄で両方に値がある日だけでそろえて計算。"
            "20 日未満・値動きがない場合は算出不能。★はベンチマーク"
        )).font = Font(color="666666", size=9)

    # ---- ベータ ----

    def _beta(self, sheet):
        app = self.app
        bench = app.close(app.benchmark)
        titles = ["コード", "銘柄"]
        for period in CORRELATION_PERIODS:
            titles += [f"β({period})", f"日数({period})"]
        sheet.cell(row=1, column=1, value=f"ベンチマーク: {benchmark_label(app.benchmark)}"
                   + ("(ベータは計算しません)" if app.benchmark == NO_BENCHMARK else "")).font = Font(bold=True)
        if app.benchmark == NO_BENCHMARK:
            return
        self._header(sheet, 2, titles, [8, 28] + [11, 11] * len(CORRELATION_PERIODS))
        for row, code in enumerate(app.codes, start=3):
            self._cell(sheet, row, 1, code)
            self._cell(sheet, row, 2, app.stock_name(code))
            close = app.close(code)
            for k, window in enumerate(CORRELATION_PERIODS.values()):
                result = beta(close, bench, window) if close is not None else Correlation(None, 0)
                self._cell(sheet, row, 3 + k * 2, result.value if result.value is not None else "算出不能", "0.00")
                self._cell(sheet, row, 4 + k * 2, result.days, "#,##0")

    # ---- 業績 ----

    def _financials(self, sheet):
        app = self.app
        titles = ["コード", "銘柄", "何期前", "決算期末", "通貨", "売上高(億)", "営業利益(億)", "純利益(億)",
                  "営業利益率", "売上高 前期比", "営業利益 前期比", "純利益 前期比"]
        self._header(sheet, 1, titles, [8, 26, 8, 12, 7, 13, 13, 13, 11, 13, 14, 13])
        row = 2
        cagr_rows = []
        for code in app.codes:
            financials = app.financials.get(code)
            years = financials.years[-MAX_YEARS:] if financials else []
            for k, year in enumerate(years):
                previous = years[k - 1] if k else None
                values = [
                    code, app.stock_name(code), period_labels(len(years))[k], year.end, financials.currency,
                    oku(year.revenue), oku(year.operating_income), oku(year.net_income),
                    None if year.operating_margin is None else year.operating_margin / 100,
                ]
                for item in ITEMS:
                    rate = growth(previous.item(item), year.item(item)) if previous else None
                    values.append(None if rate is None else rate / 100)
                formats = [None] * 5 + ["#,##0.0"] * 3 + ["0.0%"] * 4
                for i, (value, number_format) in enumerate(zip(values, formats), start=1):
                    self._cell(sheet, row, i, value, number_format)
                row += 1
            if not years:
                reason = financials.error if financials else "未取得"
                self._cell(sheet, row, 1, code)
                self._cell(sheet, row, 2, f"{app.stock_name(code)}(業績なし: {reason})")
                row += 1
            spans = len(years) - 1
            rates = [cagr(years[0].item(i), years[-1].item(i), spans) if spans > 0 else None for i in ITEMS]
            cagr_rows.append((code, spans, rates))

        row += 1
        sheet.cell(row=row, column=1, value="年平均成長率(CAGR。一番古い期から直近期まで)").font = Font(bold=True)
        row += 1
        self._header(sheet, row, ["コード", "銘柄", "年数", "", "", "売上高", "営業利益", "純利益"])
        for code, spans, rates in cagr_rows:
            row += 1
            self._cell(sheet, row, 1, code)
            self._cell(sheet, row, 2, app.stock_name(code))
            self._cell(sheet, row, 3, spans if spans > 0 else None)
            for k, rate in enumerate(rates):
                self._cell(sheet, row, 6 + k, None if rate is None else rate / 100, "0.0%")
        sheet.freeze_panes = "C2"

    # ---- 情報 ----

    def _info(self, sheet):
        app = self.app
        lines = [
            ("出力した日時", f"{self.now:%Y-%m-%d %H:%M}"),
            ("ベンチマーク", benchmark_label(app.benchmark)),
            ("グループ", app.settings["selected_group"] + ("(資産管理の保有から自動)" if app.is_holdings else "")),
            ("銘柄数", len(app.codes)),
        ]
        dates = {t.base_date for c in app.codes if (t := app.techs.get(c)) and t.base_date}
        if dates:
            lines.append(("株価データの基準日(最新)", max(dates)))
        lines.append(("", ""))
        for code in app.codes:
            quote, tech, financials = app.quotes.get(code), app.techs.get(code), app.financials.get(code)
            notes = []
            if quote is None:
                notes.append("指標: 取得中")
            elif quote.error:
                notes.append(f"指標の取得に失敗: {quote.error}")
            if tech is not None and tech.error:
                notes.append(f"テクニカル: {tech.error}")
            elif tech is not None:
                notes.append(f"株価の基準日 {tech.base_date}")
            if app.glitches.get(code):
                notes.append("異常値として除いた日: " + "、".join(app.glitches[code]))
            if financials is not None:
                notes.append(f"業績の取得 {financials.fetched_at.replace('T', ' ')[:16]}"
                             + (f"(失敗: {financials.error})" if financials.error else f"({financials.currency})"))
            lines.append((f"{code} {app.stock_name(code)}", " / ".join(notes)))
        if app.glitches.get(app.benchmark):
            lines.append((f"ベンチマーク {app.benchmark}", "異常値として除いた日: " + "、".join(app.glitches[app.benchmark])))
        sheet.column_dimensions["A"].width = 30
        sheet.column_dimensions["B"].width = 110
        for row, (key, value) in enumerate(lines, start=1):
            sheet.cell(row=row, column=1, value=key).font = Font(bold=True)
            sheet.cell(row=row, column=2, value=value)


def main() -> int:
    root = tk.Tk()
    apply_theme(root)  # ToreCan の色・フォント(gui_theme.py)
    root.title(app_title("compare"))
    root.geometry("1300x700")
    try:
        settings = read_compare()
    except (OSError, ValueError) as e:
        messagebox.showerror(APP_NAME, f"{COMPARE_PATH.name} を読み込めません。\n{e}", parent=root)
        return 1
    try:
        # 前の版の compare.json にあったグループを、ウォッチリストと共有のグループ(watchlist.json)に移す
        legacy = settings.pop("legacy_groups")
        groups = merge_groups(legacy) if legacy else load_groups()
        if legacy:
            write_compare(settings)  # 移したので、compare.json からはグループを消す
        args = sys.argv[1:]
        names = [g["name"] for g in shared_groups(groups)]
        if "--group" in args[:-1]:  # グループを選んで起動する(python fina_compare.py --group 保有資産)
            index = args.index("--group")
            name = args[index + 1]
            if name in (HOLDINGS_GROUP, HOLDINGS_LABEL, *names):
                settings["selected_group"] = HOLDINGS_GROUP if name == HOLDINGS_LABEL else name
            args = args[:index] + args[index + 2:]
        if settings["selected_group"] not in (HOLDINGS_GROUP, *names):
            settings["selected_group"] = names[0] if names else HOLDINGS_GROUP
        added = [code.upper().removesuffix(".T") for code in args]
        if added and names:  # 選んでいるグループ(保有資産のときは先頭のふつうのグループ)に追加する
            target = settings["selected_group"] if settings["selected_group"] in names else names[0]
            add_codes(target, added)
            groups = load_groups()
    except (OSError, ValueError) as e:
        messagebox.showerror(APP_NAME, f"{WATCHLIST_PATH.name} を読み書きできません。\n{e}", parent=root)
        return 1
    try:
        app = CompareApp(root, settings, groups)
    except FileNotFoundError as e:
        messagebox.showerror(APP_NAME, str(e), parent=root)
        return 1
    app.pack(fill="both", expand=True)
    app.refresh()

    def on_close():
        app.save()
        app.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
