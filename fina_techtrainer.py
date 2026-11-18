"""テクニカルトレードの訓練。過去のチャートを 1 日ずつ進めながら売買し、最後に成績のレポートを出す。

銘柄と開始日を選んで「開始」を押すと、開始日までのチャートが出る(先の足は見えない)。
「1 日進む」で 1 本ずつ足が増える。好きなところで買い、好きなところで売る。
最後に(または「終了」で)レポートを出し、HTML ファイルに保存できる。

売買のルール:
    - 買いのみ(売りから入る取引はしない)
    - 同時に持てるのは 1 つだけ。買い増し・ナンピン・分割での売りはしない(全部まとめて売る)
    - 株数は毎回同じ(設定の「株数」)
    - 約定の値は、設定で選ぶ
        終値      : 今見えている最後の足の終値で約定する
        翌日の始値: 次の足の始値で約定する(注文を出すと 1 日進む)
    - 手数料は考えない
    - 終わりの足で保有していれば、その足の終値で売ったことにする(理由は「終了時決済」)

ストラテジーの買い場・売り場まで進める:
    設定で選んだストラテジー(core_strategies.py / usr_strategies.py)が買い(売り)の合図を出す足まで、
    時間を進める。売買は自分で行う(進めるだけ)。
    買い場は、ストラテジーの買いの合図(entries)。売り場は、ストラテジー自身が取引したときの売りの日(trades)。
    合図がもうない(終わりまで進むことになる)ときは、終わりまで進めて終了してよいかを尋ねる。
    「いいえ」なら、その場から続ける。

レポート:
    取引の一覧、損益(円)、勝率、最大ドローダウンなどの成績と、同じ期間の比較を出す。
        バイ・アンド・ホールド: 開始日に買い、終わりまで持ち続けた場合
        ストラテジー          : 選んだストラテジーで同じ株数を売買した場合(約定の値は、手動の売買と同じ設定)
    「HTML に保存」で、チャートの画像も含めた 1 つの HTML ファイルに保存する。

操作:
    1 日進む  「1日 ▶」ボタン、または → キー(「5日 ▶▶」で 5 日)
    買う      「買う」ボタン、または B キー
    売る      「売る」ボタン、または S キー
    チャート  拡大・縮小・スクロール・図形の描画は gui_chart.py と同じ

株価は取得しない。stock_database フォルダの保存済みデータだけを使うので、
先に core_gather.py で株価を取得しておくこと。

使い方:
    python fina_techtrainer.py
"""

import base64
import html
import io
import math
import sys
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from gui_theme import apply_theme
from gui_chart import BUY_COLOR, DOWN_COLOR, SELL_COLOR, UP_COLOR, ChartFrame, TradeMark
from core_settings import app_title
from core_gather import BASE_DIR, csv_path_for, load_saved
from core_stocks import load_listed, stock_index
from core_strategies import FILL_MODES, Strategy, all_strategies
from gui_widgets import StockSearchBox

DEFAULT_SHARES = 100
SHARES_STEP = 100
DEFAULT_START_YEARS = 3       # 開始日の初期値(データの最後から何年前か)
MIN_HISTORY = 200             # 開始日より前に必要な足の本数(SMA200 が引けるように)
FAST_STEP = 5                 # 「5日 ▶▶」で進む本数
NO_STRATEGY = "(使わない)"
REPORT_CONTEXT_BARS = 60      # レポートのチャートで、開始日より前に見せる足の本数
FINAL_REASON = "終了時決済"

PRICE_MODES = FILL_MODES  # 約定の値(名前 -> 表示名)。ストラテジーの売買と同じ選択肢

# レポートの取引の表の列(列の名前 -> (見出し、幅、文字の寄せ方))
TRADE_COLUMNS = {
    "number": ("No", 40, "center"),
    "entry_date": ("買日", 90, "center"),
    "entry_price": ("買値", 84, "e"),
    "exit_date": ("売日", 90, "center"),
    "exit_price": ("売値", 84, "e"),
    "reason": ("理由", 90, "center"),
    "held": ("保有(日)", 66, "e"),
    "return": ("損益率", 72, "e"),
    "pnl": ("損益(円)", 100, "e"),
}


# ---- 訓練の進行(画面を使わない部分) ----

@dataclass(frozen=True)
class UserTrade:
    """取引 1 つ。index は日足の行番号。"""
    entry_index: int
    entry_date: pd.Timestamp
    entry_price: float
    exit_index: int
    exit_date: pd.Timestamp
    exit_price: float
    shares: int
    reason: str = "手動"

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.shares

    @property
    def return_pct(self) -> float:
        return (self.exit_price / self.entry_price - 1) * 100

    @property
    def held(self) -> int:
        return self.exit_index - self.entry_index


@dataclass(frozen=True)
class Position:
    index: int
    date: pd.Timestamp
    price: float


class Session:
    """訓練 1 回分の状態。cur 本目(日足の行番号)までが見えている。"""

    def __init__(self, code: str, daily: pd.DataFrame, start: int, end: int, shares: int, mode: str,
                 strategy: Strategy | None = None):
        self.code = code
        self.daily = daily
        self.start = start
        self.end = end
        self.cur = start
        self.shares = shares
        self.mode = mode
        self.strategy = strategy
        self.position: Position | None = None
        self.trades: list[UserTrade] = []
        self.finished = False
        # ストラテジーの買い場・売り場(行番号)。終わりの日までの足だけで計算する(先の足は見ない)
        self.buy_points: list[int] = []
        self.sell_points: list[int] = []
        if strategy is not None:
            bars = daily.iloc[: end + 1]
            self.buy_points = [e.index for e in strategy.entries(bars) if e.index >= start]
            self.sell_points = [t.exit.index for t in strategy.trades(bars) if t.exit is not None and t.exit.index > start]

    # ---- 見えている足 ----

    def visible(self) -> pd.DataFrame:
        return self.daily.iloc[: self.cur + 1]

    @property
    def date(self) -> pd.Timestamp:
        return self.daily.index[self.cur]

    @property
    def close(self) -> float:
        return float(self.daily["Close"].iloc[self.cur])

    @property
    def at_end(self) -> bool:
        return self.cur >= self.end

    def unrealized(self) -> float:
        """保有中の評価損益(円。今の終値で計算)。持っていなければ 0。"""
        return 0.0 if self.position is None else (self.close - self.position.price) * self.shares

    def realized(self) -> float:
        return sum(t.pnl for t in self.trades)

    # ---- 進める ----

    def step(self, bars: int = 1) -> int:
        """bars 本進める(終わりの足で止まる)。進んだ本数を返す。"""
        target = min(self.cur + bars, self.end)
        moved = target - self.cur
        self.cur = target
        return moved

    def next_point(self, kind: str) -> int | None:
        """今より後で一番近い、ストラテジーの買い場(kind="buy")・売り場(kind="sell")の行番号。なければ None。"""
        points = self.buy_points if kind == "buy" else self.sell_points
        return next((i for i in points if self.cur < i <= self.end), None)

    # ---- 売買 ----

    def can_buy(self) -> bool:
        if self.finished or self.position is not None:
            return False
        return not (self.mode == "next_open" and self.at_end)  # 翌日の始値で買うには、次の足が要る

    def can_sell(self) -> bool:
        if self.finished or self.position is None:
            return False
        if self.mode == "next_open":
            return not self.at_end
        return self.cur > self.position.index  # 終値で買った足では売れない(同じ値になる)

    def _fill_price(self) -> float:
        """約定の値。翌日の始値なら、1 本進めてその足の始値。"""
        if self.mode == "next_open":
            self.step()
            return float(self.daily["Open"].iloc[self.cur])
        return self.close

    def buy(self) -> Position:
        if not self.can_buy():
            raise ValueError("今は買えません")
        price = self._fill_price()
        self.position = Position(self.cur, self.date, price)
        return self.position

    def sell(self, reason: str = "手動") -> UserTrade:
        if self.position is None:
            raise ValueError("株を持っていません")
        price = self.close if reason == FINAL_REASON else self._fill_price()
        p = self.position
        trade = UserTrade(p.index, p.date, p.price, self.cur, self.date, price, self.shares, reason)
        self.trades.append(trade)
        self.position = None
        return trade

    def finish(self) -> None:
        """訓練を終える。保有していれば、今の足の終値で売ったことにする。"""
        if self.finished:
            return
        if self.position is not None:
            self.sell(FINAL_REASON)
        self.finished = True


# ---- レポート ----

@dataclass(frozen=True)
class Benchmark:
    """比べる相手の成績。"""
    label: str
    pnl: float
    note: str


@dataclass
class Report:
    code: str
    name: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    bars: int                  # 訓練した足の本数(開始日から終わりまで)
    mode: str
    shares: int
    strategy: str
    trades: list[UserTrade]
    benchmarks: list[Benchmark]
    chart_png: bytes
    created: datetime

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def wins(self) -> int:
        return sum(t.pnl > 0 for t in self.trades)

    def stats(self) -> list[tuple[str, str]]:
        """成績の一覧(見出し、値)。"""
        trades = self.trades
        if not trades:
            return [("取引", "0 回")]
        pnls = [t.pnl for t in trades]
        cumulative, peak, drawdown = 0.0, 0.0, 0.0
        for pnl in pnls:
            cumulative += pnl
            peak = max(peak, cumulative)
            drawdown = min(drawdown, cumulative - peak)
        gains = sum(p for p in pnls if p > 0)
        losses = -sum(p for p in pnls if p < 0)
        held = sum(t.held for t in trades)
        return [
            ("損益の合計", yen(self.total_pnl)),
            ("取引", f"{len(trades)} 回(勝ち {self.wins} 回、負け {len(trades) - self.wins} 回)"),
            ("勝率", f"{self.wins / len(trades):.0%}"),
            ("平均の損益率", f"{sum(t.return_pct for t in trades) / len(trades):+.2f}%"),
            ("最大の利益 / 最大の損失", f"{yen(max(pnls))} / {yen(min(pnls))}"),
            ("プロフィットファクター", "—" if losses == 0 else f"{gains / losses:.2f}"),
            ("最大ドローダウン", yen(drawdown)),
            ("平均の保有日数", f"{held / len(trades):.1f} 日"),
            ("保有していた割合", f"{held / max(self.bars, 1):.0%}"),
        ]


def yen(value: float) -> str:
    return f"{value:+,.0f} 円"


def benchmarks(session: Session) -> list[Benchmark]:
    """同じ期間・同じ株数での比較。"""
    daily, start, end = session.daily, session.start, session.cur
    close = daily["Close"]
    result = []
    if session.mode == "next_open" and start + 1 <= end:
        buy_price, when = float(daily["Open"].iloc[start + 1]), "開始日の翌日の始値"
    else:
        buy_price, when = float(close.iloc[start]), "開始日の終値"
    result.append(Benchmark(
        "バイ・アンド・ホールド", (float(close.iloc[end]) - buy_price) * session.shares,
        f"{when} {buy_price:,.1f} で買い、終わりの終値 {float(close.iloc[end]):,.1f} まで保有",
    ))
    if session.strategy is not None:
        bars = daily.iloc[: end + 1]
        pnl, count = 0.0, 0
        for t in session.strategy.trades(bars, daily.index[start], session.mode):
            exit_price = t.exit.price if t.exit is not None else float(close.iloc[end])
            pnl += (exit_price - t.entry.price) * session.shares
            count += 1
        result.append(Benchmark(
            f"ストラテジー {session.strategy.name}", pnl, f"{count} 回の取引({PRICE_MODES[session.mode]}で売買。終わりで保有中なら終値で決済)",
        ))
    return result


def report_chart(session: Session, width: float = 11, height: float = 5) -> Figure:
    """レポートのチャート。上段は終値と売買(保有した期間に色を付ける)、下段は確定損益の累計。"""
    daily = session.daily
    first = max(session.start - REPORT_CONTEXT_BARS, 0)
    span = daily.iloc[first: session.cur + 1]
    x = range(len(span))
    fig = Figure(figsize=(width, height), dpi=100)
    grid = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.08)
    ax = fig.add_subplot(grid[0])
    ax_pnl = fig.add_subplot(grid[1], sharex=ax)
    ax.plot(x, span["Close"], color="#455a64", linewidth=1.1)
    ax.axvspan(-0.5, session.start - first - 0.5, color="#000000", alpha=0.05)  # 開始日より前
    for t in session.trades:
        x0, x1 = t.entry_index - first, t.exit_index - first
        ax.axvspan(x0, x1, color=UP_COLOR if t.pnl > 0 else DOWN_COLOR, alpha=0.12)
        ax.plot([x0], [t.entry_price], marker="^", color=BUY_COLOR, markersize=9, markeredgecolor="white")
        ax.plot([x1], [t.exit_price], marker="v", color=SELL_COLOR, markersize=9, markeredgecolor="white")
    ax.set_title(f"{session.code}  終値と売買(▲買い ▼売り。緑は勝ち、赤は負けの保有期間)", fontsize=10, loc="left")
    ax.grid(True, color="#e6e6e6", linewidth=0.6)
    ax.tick_params(labelbottom=False)

    cumulative = pd.Series(0.0, index=range(len(span)))
    for t in session.trades:
        cumulative.iloc[t.exit_index - first:] += t.pnl
    ax_pnl.plot(x, cumulative, color=BUY_COLOR, linewidth=1.2)
    ax_pnl.axhline(0, color="#9e9e9e", linewidth=0.8)
    ax_pnl.set_ylabel("確定損益(円)", fontsize=8)
    ax_pnl.grid(True, color="#e6e6e6", linewidth=0.6)
    ticks = [int(i) for i in pd.Series(x).iloc[:: max(len(span) // 8, 1)]]
    ax_pnl.set_xticks(ticks)
    ax_pnl.set_xticklabels([f"{span.index[i]:%Y-%m-%d}" for i in ticks], fontsize=8)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.93, bottom=0.08)
    return fig


def make_report(session: Session, name: str) -> Report:
    fig = report_chart(session)
    FigureCanvasAgg(fig)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png")
    return Report(
        session.code, name, session.daily.index[session.start], session.date, session.cur - session.start + 1,
        PRICE_MODES[session.mode], session.shares,
        session.strategy.name if session.strategy else NO_STRATEGY,
        list(session.trades), benchmarks(session), buffer.getvalue(), datetime.now(),
    )


def report_html(report: Report) -> str:
    """レポートを、画像も中に入れた 1 つの HTML にする。"""
    e = html.escape

    def cls(value: float) -> str:
        return "plus" if value > 0 else "minus" if value < 0 else ""

    conditions = [
        ("銘柄", f"{report.code} {report.name}"),
        ("期間", f"{report.start_date:%Y-%m-%d} 〜 {report.end_date:%Y-%m-%d}({report.bars} 日)"),
        ("約定の値", report.mode),
        ("株数", f"{report.shares:,} 株(毎回同じ)"),
        ("ストラテジー", report.strategy),
    ]
    rows = "\n".join(
        f"<tr class='{cls(t.pnl)}'><td>{i}</td><td>{t.entry_date:%Y-%m-%d}</td><td>{t.entry_price:,.1f}</td>"
        f"<td>{t.exit_date:%Y-%m-%d}</td><td>{t.exit_price:,.1f}</td><td>{e(t.reason)}</td><td>{t.held}</td>"
        f"<td>{t.return_pct:+.2f}%</td><td>{yen(t.pnl)}</td></tr>"
        for i, t in enumerate(report.trades, 1)
    ) or "<tr><td colspan='9'>取引はありません</td></tr>"
    bench_rows = "\n".join(
        f"<tr><th>{e(b.label)}</th><td class='{cls(b.pnl)}'>{yen(b.pnl)}</td><td class='note'>{e(b.note)}</td></tr>"
        for b in [Benchmark("あなた", report.total_pnl, f"{len(report.trades)} 回の取引"), *report.benchmarks]
    )

    def table(items):
        return "\n".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in items)

    image = base64.b64encode(report.chart_png).decode("ascii")
    headers = "".join(f"<th>{e(h)}</th>" for h, _, _ in TRADE_COLUMNS.values())
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>トレード訓練レポート {e(report.code)}</title>
<style>
  body {{ font-family: "Yu Gothic", "Meiryo", sans-serif; color: #222; background: #fff; margin: 24px auto; max-width: 1100px; padding: 0 16px; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  h2 {{ font-size: 17px; margin-top: 28px; border-bottom: 2px solid #dde3ea; padding-bottom: 4px; }}
  .sub {{ color: #666; font-size: 13px; }}
  .total {{ font-size: 28px; font-weight: bold; margin: 12px 0; }}
  table {{ border-collapse: collapse; font-size: 14px; }}
  th, td {{ border: 1px solid #d0d7de; padding: 4px 10px; }}
  th {{ background: #f3f5f8; text-align: left; font-weight: normal; }}
  .trades td {{ text-align: right; }}
  .trades th {{ text-align: center; }}
  .plus {{ color: #1b7f5f; }}
  .minus {{ color: #c62828; }}
  .note {{ color: #555; font-size: 13px; }}
  .grid {{ display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }}
  img {{ max-width: 100%; border: 1px solid #e3e3e3; }}
</style>
</head>
<body>
<h1>トレード訓練レポート</h1>
<div class="sub">{e(report.code)} {e(report.name)} / 作成 {report.created:%Y-%m-%d %H:%M} / ToreCan fina_techtrainer</div>
<div class="total {cls(report.total_pnl)}">損益の合計 {yen(report.total_pnl)}</div>
<div class="grid">
<table>{table(conditions)}</table>
<table>{table(report.stats())}</table>
</div>
<h2>比較(同じ期間・同じ株数)</h2>
<table>{bench_rows}</table>
<h2>チャート</h2>
<img src="data:image/png;base64,{image}" alt="終値と売買のチャート">
<h2>取引の一覧</h2>
<table class="trades"><tr>{headers}</tr>
{rows}
</table>
<p class="note">手数料は含みません。保有日数は足の本数です。</p>
</body>
</html>
"""


# ---- 画面 ----

class TrainerFrame(ttk.Frame):
    """設定の欄、チャート、操作の欄をまとめた部品。"""

    def __init__(self, master):
        super().__init__(master)
        self.listed = load_listed()
        self.strategies = all_strategies()
        self.code: str | None = None
        self.daily: pd.DataFrame | None = None
        self.session: Session | None = None

        self._build_settings()
        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)
        self._build_controls(body)
        self.chart = ChartFrame(body, searchable=False)
        self.chart.pack(side="left", fill="both", expand=True)
        self._update_controls()

        top = self.winfo_toplevel()
        for key, action in (("<Right>", lambda: self.step(1)), ("b", self.buy), ("B", self.buy),
                            ("s", self.sell), ("S", self.sell)):
            top.bind(key, lambda e, a=action: self._on_key(e, a), add="+")

    # ---- 画面の組み立て ----

    def _build_settings(self):
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=6, pady=(6, 4))
        self.search = StockSearchBox(
            bar, stock_index(self.listed), on_pick=lambda stock: self.choose(stock.code),
            on_unmatched=lambda text: self.choose(text.upper().removesuffix(".T")),
            label="銘柄", button="選ぶ", dropdown=True, entry_width=16,
        )
        self.search.pack(side="left")
        self.stock_var = tk.StringVar(value="(未選択)")
        ttk.Label(bar, textvariable=self.stock_var, width=26).pack(side="left", padx=(4, 12))

        self.inputs = []
        ttk.Label(bar, text="開始日").pack(side="left")
        self.start_var = tk.StringVar()
        self._add_input(ttk.Entry(bar, textvariable=self.start_var, width=11))
        ttk.Label(bar, text="終わり").pack(side="left", padx=(8, 0))
        self.end_var = tk.StringVar()
        self._add_input(ttk.Entry(bar, textvariable=self.end_var, width=11))
        ttk.Label(bar, text="株数").pack(side="left", padx=(12, 0))
        self.shares_var = tk.IntVar(value=DEFAULT_SHARES)
        self._add_input(ttk.Spinbox(bar, from_=SHARES_STEP, to=1_000_000, increment=SHARES_STEP,
                                    textvariable=self.shares_var, width=8))
        ttk.Label(bar, text="約定").pack(side="left", padx=(12, 0))
        self.mode_var = tk.StringVar(value="close")
        for mode, label in PRICE_MODES.items():
            self._add_input(ttk.Radiobutton(bar, text=label, value=mode, variable=self.mode_var), padx=(4, 0))
        ttk.Label(bar, text="ストラテジー").pack(side="left", padx=(12, 0))
        self.strategy_var = tk.StringVar(value=next(iter(self.strategies), NO_STRATEGY))
        self._add_input(ttk.Combobox(bar, textvariable=self.strategy_var, state="readonly", width=14,
                                     values=[*self.strategies, NO_STRATEGY]))
        self.start_button = ttk.Button(bar, text="開始", command=self.start)
        self.start_button.pack(side="left", padx=(16, 0))

    def _add_input(self, widget, padx=(4, 0)):
        widget.pack(side="left", padx=padx)
        self.inputs.append(widget)

    def _build_controls(self, parent):
        panel = ttk.Frame(parent, width=230)
        panel.pack(side="right", fill="y", padx=(4, 6), pady=4)
        panel.pack_propagate(False)

        ttk.Label(panel, text="時間を進める", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        row = ttk.Frame(panel)
        row.pack(fill="x", pady=(2, 0))
        self.step_button = ttk.Button(row, text="1日 ▶  (→)", command=lambda: self.step(1))
        self.step_button.pack(side="left", fill="x", expand=True)
        self.fast_button = ttk.Button(row, text=f"{FAST_STEP}日 ▶▶", command=lambda: self.step(FAST_STEP))
        self.fast_button.pack(side="left", fill="x", expand=True, padx=(2, 0))
        self.buy_point_button = ttk.Button(panel, text="次の買い場まで", command=lambda: self.jump("buy"))
        self.buy_point_button.pack(fill="x", pady=(4, 0))
        self.sell_point_button = ttk.Button(panel, text="次の売り場まで", command=lambda: self.jump("sell"))
        self.sell_point_button.pack(fill="x", pady=(2, 0))

        ttk.Label(panel, text="売買", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", pady=(14, 0))
        self.buy_button = ttk.Button(panel, text="買う  (B)", command=self.buy)
        self.buy_button.pack(fill="x", pady=(2, 0))
        self.sell_button = ttk.Button(panel, text="売る  (S)", command=self.sell)
        self.sell_button.pack(fill="x", pady=(2, 0))

        ttk.Label(panel, text="状況", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", pady=(14, 0))
        self.info_var = tk.StringVar()
        ttk.Label(panel, textvariable=self.info_var, justify="left", wraplength=220).pack(anchor="w", fill="x")
        self.message_var = tk.StringVar(value="銘柄と開始日を選んで「開始」を押してください。")
        ttk.Label(panel, textvariable=self.message_var, justify="left", wraplength=220,
                  foreground="#555555").pack(anchor="w", fill="x", pady=(10, 0))

        self.finish_button = ttk.Button(panel, text="終了してレポート", command=self.finish)
        self.finish_button.pack(side="bottom", fill="x", pady=(0, 4))

    # ---- 設定 ----

    def choose(self, code: str) -> None:
        """訓練する銘柄を選ぶ。開始日・終わりの初期値も入れる。"""
        if self.session is not None and not self.session.finished:
            self.message_var.set("訓練中は銘柄を変えられません。先に「終了してレポート」を押してください。")
            return
        daily = load_saved(csv_path_for(code))
        if daily is None or len(daily.dropna()) <= MIN_HISTORY:
            self.message_var.set(f"{code} の株価データがない、または短すぎます。先に python core_gather.py {code} を実行してください。")
            return
        self.code, self.daily = code, daily.dropna()
        self.stock_var.set(f"{code} {self.stock_name(code)}")
        last = self.daily.index[-1]
        start = max(last - pd.DateOffset(years=DEFAULT_START_YEARS), self.daily.index[MIN_HISTORY])
        self.start_var.set(f"{start:%Y-%m-%d}")
        self.end_var.set(f"{last:%Y-%m-%d}")
        self.message_var.set("開始日などを決めて「開始」を押してください。")

    def stock_name(self, code: str) -> str:
        name = self.listed["銘柄名"].get(code)
        return name if isinstance(name, str) else ""

    def start(self) -> None:
        """設定どおりに訓練を始める。"""
        if self.daily is None:
            self.message_var.set("先に銘柄を選んでください。")
            return
        if self.session is not None and not self.session.finished and not messagebox.askyesno(
            "fina_techtrainer", "訓練の途中です。やめて最初からやり直しますか?", parent=self,
        ):
            return
        try:
            start_date = pd.Timestamp(self.start_var.get().strip())
            end_date = pd.Timestamp(self.end_var.get().strip())
            shares = int(self.shares_var.get())
        except (ValueError, tk.TclError):
            messagebox.showerror("fina_techtrainer", "日付は YYYY-MM-DD、株数は整数で入力してください。", parent=self)
            return
        index = self.daily.index
        start = int(index.searchsorted(start_date))          # 開始日(休日なら次の取引日)
        end = int(index.searchsorted(end_date, side="right")) - 1  # 終わり(休日なら前の取引日)
        if shares <= 0 or start >= len(index) or end <= start:
            messagebox.showerror("fina_techtrainer", "開始日が終わりより前になるように、株数は 1 以上にしてください。", parent=self)
            return
        name = self.strategy_var.get()
        strategy = self.strategies[name]() if name in self.strategies else None
        self.session = Session(self.code, self.daily, start, end, shares, self.mode_var.get(), strategy)
        self.message_var.set(
            f"{index[start]:%Y-%m-%d} から開始。{index[end]:%Y-%m-%d} まで {end - start} 日進められます。"
        )
        self._refresh()
        self.chart.canvas.get_tk_widget().focus_set()

    # ---- 操作 ----

    def _on_key(self, event, action):
        # 入力欄で文字を打っているときは、キーの操作にしない
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox, tk.Listbox)):
            return None
        action()
        return "break"

    def step(self, bars: int) -> None:
        s = self.session
        if s is None or s.finished:
            return
        if s.at_end:
            self.message_var.set("最後の足です。「終了してレポート」でレポートを出します。")
            return
        s.step(bars)
        self.message_var.set("最後の足です。「終了してレポート」でレポートを出します。" if s.at_end else "")
        self._refresh()

    def jump(self, kind: str) -> None:
        """ストラテジーの次の買い場(売り場)まで進める。なければ、終わりまで進めて終えるかを尋ねる。"""
        s = self.session
        if s is None or s.finished or s.strategy is None:
            return
        label = "買い場" if kind == "buy" else "売り場"
        target = s.next_point(kind)
        if target is None:
            if messagebox.askyesno(
                "fina_techtrainer",
                f"ストラテジーの{label}は、もうありません。\n"
                f"終わり({s.daily.index[s.end]:%Y-%m-%d})まで進めて、訓練を終了しますか?\n\n"
                "「いいえ」なら、今の位置から続けます。",
                parent=self,
            ):
                s.step(s.end - s.cur)
                self._refresh()
                self.finish(confirm=False)
            return
        s.step(target - s.cur)
        self.message_var.set(f"ストラテジーの{label}です({s.date:%Y-%m-%d})。")
        self._refresh()

    def buy(self) -> None:
        s = self.session
        if s is None or not s.can_buy():
            return
        p = s.buy()
        self.message_var.set(f"{p.date:%Y-%m-%d} に {p.price:,.1f} 円で {s.shares:,} 株買いました。")
        self._refresh()

    def sell(self) -> None:
        s = self.session
        if s is None or not s.can_sell():
            return
        t = s.sell()
        self.message_var.set(
            f"{t.exit_date:%Y-%m-%d} に {t.exit_price:,.1f} 円で売りました。損益 {yen(t.pnl)}({t.return_pct:+.2f}%)"
        )
        self._refresh()

    def finish(self, confirm: bool = True) -> None:
        """訓練を終えて、レポートを出す。"""
        s = self.session
        if s is None or s.finished:
            return
        if confirm and not s.at_end and not messagebox.askyesno(
            "fina_techtrainer", f"{s.date:%Y-%m-%d} で訓練を終了して、レポートを出しますか?", parent=self,
        ):
            return
        s.finish()
        self._refresh()
        self.message_var.set("訓練を終了しました。新しく始めるときは、設定を決めて「開始」を押してください。")
        ReportWindow(self, make_report(s, self.stock_name(s.code)))

    # ---- 表示 ----

    def _refresh(self):
        s = self.session
        self.chart.set_data(s.code, s.visible())
        marks = [TradeMark(t.entry_date, t.entry_price, t.exit_date, t.exit_price) for t in s.trades]
        if s.position is not None:
            marks.append(TradeMark(s.position.date, s.position.price))
        self.chart.set_trades(marks)
        lines = [
            f"日付  {s.date:%Y-%m-%d}({s.cur - s.start} / {s.end - s.start} 日目)",
            f"終値  {s.close:,.1f}",
        ]
        if s.position is None:
            lines.append("保有  なし")
        else:
            change = (s.close / s.position.price - 1) * 100
            lines += [
                f"保有  {s.shares:,} 株(買値 {s.position.price:,.1f})",
                f"評価損益  {yen(s.unrealized())}({change:+.2f}%)",
            ]
        lines += [f"確定損益  {yen(s.realized())}", f"取引  {len(s.trades)} 回"]
        self.info_var.set("\n".join(lines))
        self._update_controls()

    def _update_controls(self):
        s = self.session
        active = s is not None and not s.finished
        has_strategy = active and s.strategy is not None

        def enable(widget, on: bool):
            widget.configure(state="normal" if on else "disabled")

        enable(self.step_button, active and not s.at_end)
        enable(self.fast_button, active and not s.at_end)
        enable(self.buy_point_button, has_strategy and s.position is None)
        enable(self.sell_point_button, has_strategy and s.position is not None)
        enable(self.buy_button, active and s.can_buy())
        enable(self.sell_button, active and s.can_sell())
        enable(self.finish_button, active)
        for widget in self.inputs:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="disabled" if active else "readonly")
            else:
                enable(widget, not active)


class ReportWindow(tk.Toplevel):
    """レポートの画面。成績・比較・チャート・取引の一覧を出し、HTML に保存できる。"""

    def __init__(self, master, report: Report):
        super().__init__(master)
        self.report = report
        self.title(f"ToreCan techtrainer レポート - {report.code} {report.name}")
        self.geometry("1150x900")

        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=10, pady=(10, 4))
        color = "#1b7f5f" if report.total_pnl > 0 else "#c62828" if report.total_pnl < 0 else "#222222"
        ttk.Label(top, text=f"損益の合計 {yen(report.total_pnl)}", font=("TkDefaultFont", 16, "bold"),
                  foreground=color).pack(side="left")
        ttk.Button(top, text="閉じる", command=self.destroy).pack(side="right")
        ttk.Button(top, text="HTML に保存", command=self.save).pack(side="right", padx=(0, 6))

        info = ttk.Frame(self)
        info.pack(side="top", fill="x", padx=10)
        conditions = (
            f"{report.code} {report.name}   {report.start_date:%Y-%m-%d} 〜 {report.end_date:%Y-%m-%d}({report.bars} 日)   "
            f"約定: {report.mode}   株数: {report.shares:,}   ストラテジー: {report.strategy}"
        )
        ttk.Label(info, text=conditions).pack(anchor="w")
        ttk.Label(info, text="   ".join(f"{k}: {v}" for k, v in report.stats()), wraplength=1100,
                  justify="left").pack(anchor="w", pady=(4, 0))
        compare = "   ".join(f"{b.label}: {yen(b.pnl)}" for b in report.benchmarks)
        ttk.Label(info, text=f"比較(同じ期間・同じ株数)  あなた: {yen(report.total_pnl)}   {compare}").pack(anchor="w", pady=(4, 0))

        # チャートは、HTML に入れるのと同じ画像を出す
        self.image = tk.PhotoImage(data=base64.b64encode(report.chart_png).decode("ascii"), format="png")
        ttk.Label(self, image=self.image).pack(side="top", padx=10, pady=(6, 4))

        frame = ttk.Frame(self)
        frame.pack(side="top", fill="x", padx=10, pady=(0, 10))
        tree = ttk.Treeview(frame, columns=list(TRADE_COLUMNS), show="headings", height=8)
        for column, (heading, width, anchor) in TRADE_COLUMNS.items():
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor=anchor)
        tree.tag_configure("plus", foreground="#1b7f5f")
        tree.tag_configure("minus", foreground="#c62828")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(side="left", fill="x", expand=True)
        for i, t in enumerate(report.trades, 1):
            tree.insert("", "end", tags=["plus" if t.pnl > 0 else "minus"], values=(
                i, f"{t.entry_date:%Y-%m-%d}", f"{t.entry_price:,.1f}", f"{t.exit_date:%Y-%m-%d}",
                f"{t.exit_price:,.1f}", t.reason, t.held, f"{t.return_pct:+.2f}%", yen(t.pnl),
            ))

    def save(self) -> None:
        r = self.report
        path = filedialog.asksaveasfilename(
            parent=self, title="レポートを HTML に保存", initialdir=BASE_DIR,
            initialfile=f"techtrainer_{r.code}_{r.created:%Y%m%d_%H%M}.html",
            defaultextension=".html", filetypes=[("HTML ファイル", "*.html")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(report_html(r))
        except OSError as e:
            messagebox.showerror("fina_techtrainer", f"保存できません。\n{e}", parent=self)
            return
        messagebox.showinfo("fina_techtrainer", f"保存しました。\n{path}", parent=self)


def main() -> int:
    root = tk.Tk()
    apply_theme(root)  # ToreCan の色・フォント(gui_theme.py)
    root.title(app_title("techtrainer"))
    root.geometry("1600x900")
    try:
        frame = TrainerFrame(root)
    except FileNotFoundError as e:
        messagebox.showerror("fina_techtrainer", str(e), parent=root)
        return 1
    frame.pack(fill="both", expand=True)
    if len(sys.argv) > 1:
        frame.choose(sys.argv[1].upper().removesuffix(".T"))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
