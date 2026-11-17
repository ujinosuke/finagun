"""バックテスト。ストラテジー(core_strategies.py / usr_strategies.py)の成績を、保存済みの株価で調べる。

上の欄で、ストラテジー・期間・約定の値を選ぶ(2 つのモードで共通)。モードはタブで切り替える。

スクリーニング
    選んだ市場の全銘柄でストラテジーを動かし、成績の良かった 20 銘柄と悪かった 5 銘柄を表に出す。
    成績は、期間中の取引の損益を複利で掛け合わせた「合計損益」で比べる(取引がない銘柄は除く)。
    表の銘柄をダブルクリックすると、個別銘柄の深掘りに切り替わり、その銘柄を同じ条件で調べ直す。
    市場は プライム / スタンダード / グロース / その他(ETF・REIT など)から選ぶ。
    その他は、株価データに異常な値が混じる銘柄があるので、最初はチェックを外してある。

個別銘柄の深掘り
    1 銘柄でストラテジーを動かし、チャート(gui_chart.py の ChartFrame)に売買ポイント
    (買い ▲・売り ▼。買いと売りを結ぶ点線は、勝ちが緑、負けが赤)を重ねる。
    チャートの下に、売買の一覧と成績を出す。
    売買をダブルクリック(または選んで Enter)すると、チャートがその売買の位置へ動き、
    利確・損切りの値を横線で示す。

約束ごと:
    - 約定の値は、上の欄で選ぶ(core_strategies.FILL_MODES)。手数料は含めない
        終値      : ストラテジーが決めた値(初期の実装では判定した足の終値)で売買する
        翌日の始値: 買い・売りを判定した足の、次の足の始値で売買する
    - 期間の始まりより前の買いの合図は使わない。インジケーターは期間より前の足も使って計算する
    - 期間の終わりの足までに売っていない取引は、その足の終値で売ったことにする(理由は「期間終了」)
    - 同時に持つのは 1 銘柄につき 1 つだけ(core_strategies.Strategy.trades と同じ)

効率: スクリーニングでは、各銘柄の成績(数値いくつか)だけを複数のプロセスで計算し、
売買の一覧は持たない。売買の一覧は、深掘りで 1 銘柄を調べるときにだけ計算し直す。

株価は取得しない。stock_database フォルダの保存済みデータだけを使うので、
先に core_gather.py で株価を最新にしておくこと。

使い方:
    python fina_bktest.py
"""

import math
import os
import queue
import sys
import threading
import tkinter as tk
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import cache
from tkinter import messagebox, ttk

import pandas as pd

from gui_chart import ChartFrame, TradeMark
from core_gather import START_DATE, csv_path_for, load_saved
from core_stocks import load_listed, stock_index
from core_strategies import FILL_CLOSE, FILL_MODES, Exit, Strategy, Trade, all_strategies
from gui_widgets import StockSearchBox

# 市場の選択肢(表示名 -> 最初にチェックを入れるか)。「市場・商品区分」の先頭で分け、どれでもなければ「その他」
MARKETS = {
    "プライム": True,
    "スタンダード": True,
    "グロース": True,
    "その他": False,  # ETF・ETN、REIT など。データに異常な値が混じる銘柄があるので、最初は外す
}
OTHER_MARKET = "その他"
# 期間の選択肢(表示名 -> 終わりの日から何年さかのぼるか。None はデータの最初から)
PERIODS = {
    "全期間": None,
    "直近 10 年": 10,
    "直近 5 年": 5,
    "直近 3 年": 3,
    "直近 1 年": 1,
}
PERIOD_END_REASON = "期間終了"
TOP_COUNT = 20     # 成績の良かった銘柄の数
BOTTOM_COUNT = 5   # 成績の悪かった銘柄の数
# 同時に動かすプロセスの数
WORKERS = max(1, min((os.cpu_count() or 2) - 1, 8))
# 1 つのプロセスにまとめて渡す銘柄数。小さいほど進み具合が細かく分かり、中止も早く効く
CHUNK_SIZE = 16
POLL_INTERVAL = 100  # 結果を画面に反映する間隔(ミリ秒)

# スクリーニングの表の列(列の名前 -> (見出し、幅、文字の寄せ方))
RANK_COLUMNS = {
    "rank": ("順位", 44, "center"),
    "code": ("コード", 60, "center"),
    "name": ("銘柄名", 190, "w"),
    "market": ("市場", 90, "center"),
    "trades": ("取引", 50, "e"),
    "win_rate": ("勝率", 60, "e"),
    "average": ("平均損益", 80, "e"),
    "total": ("合計損益", 90, "e"),
    "best": ("最良", 76, "e"),
    "worst": ("最悪", 76, "e"),
    "drawdown": ("最大DD", 76, "e"),
}
# 深掘りの売買の表の列
TRADE_COLUMNS = {
    "number": ("No", 40, "center"),
    "entry_date": ("買日", 90, "center"),
    "entry_price": ("買値", 84, "e"),
    "exit_date": ("売日", 90, "center"),
    "exit_price": ("売値", 84, "e"),
    "reason": ("理由", 76, "center"),
    "return": ("損益", 72, "e"),
    "held": ("保有(本)", 66, "e"),
    "take_profit": ("利確", 84, "e"),
    "stop_loss": ("損切り", 84, "e"),
    "note": ("メモ", 260, "w"),
}


# ---- バックテストの計算(画面を使わない部分。スクリーニングの各プロセスでも動く) ----

@dataclass(frozen=True)
class Summary:
    """1 銘柄の成績。損益はすべて %(手数料は含まない)。"""
    code: str
    trades: int = 0
    wins: int = 0
    average: float = math.nan   # 1 取引の平均損益
    total: float = math.nan     # 全取引の損益を複利で掛け合わせたもの
    best: float = math.nan      # 一番良かった取引
    worst: float = math.nan     # 一番悪かった取引
    drawdown: float = math.nan  # 最大ドローダウン(取引ごとの資産の推移で、高値からの一番大きな下落。マイナスの値)
    error: str = ""

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades * 100 if self.trades else math.nan


def market_group(market: str) -> str:
    """「市場・商品区分」を、MARKETS の表示名に直す。"""
    for name in MARKETS:
        if name != OTHER_MARKET and isinstance(market, str) and market.startswith(name):
            return name
    return OTHER_MARKET


def load_bars(code: str) -> pd.DataFrame | None:
    """保存済みの日足。終値がない足は除く。データがなければ None。"""
    daily = load_saved(csv_path_for(code))
    return None if daily is None else daily.dropna(subset=["Close"])


def run_backtest(strategy: Strategy, bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                 fill: str = FILL_CLOSE) -> list[Trade]:
    """期間 start〜end でストラテジーを動かし、取引の一覧を返す。fill は約定の値(FILL_MODES)。

    end より後の足は渡さない(未来の足で売買を決めないため)。
    end までに売っていない取引は、end までの最後の足の終値で売ったことにする。
    """
    bars = bars.loc[:end]
    if bars.empty:
        return []
    trades = strategy.trades(bars, start, fill)
    if trades and trades[-1].exit is None:
        last = len(bars) - 1
        trades[-1] = Trade(trades[-1].entry, Exit(last, bars.index[last], bars["Close"].iloc[last], PERIOD_END_REASON))
    return trades


def summarize(code: str, trades: list[Trade]) -> Summary:
    """取引の一覧から成績を出す。"""
    returns = [t.return_pct for t in trades if t.exit is not None]
    if not returns:
        return Summary(code)
    equity, peak, drawdown = 1.0, 1.0, 0.0
    for r in returns:
        equity *= 1 + r / 100
        peak = max(peak, equity)
        drawdown = min(drawdown, (equity / peak - 1) * 100)
    return Summary(
        code, len(returns), sum(r > 0 for r in returns), sum(returns) / len(returns),
        (equity - 1) * 100, max(returns), min(returns), drawdown,
    )


@cache
def strategy_instance(name: str) -> Strategy:
    """名前からストラテジーを作る(プロセスごとに 1 回だけ)。"""
    return all_strategies()[name]()


def evaluate(task: tuple[str, str, pd.Timestamp, pd.Timestamp, str]) -> Summary:
    """1 銘柄の成績を出す(スクリーニングの各プロセスで動く)。task は (コード, ストラテジー名, 始まり, 終わり, 約定の値)。"""
    code, name, start, end, fill = task
    try:
        bars = load_bars(code)
        if bars is None:
            return Summary(code, error="データがありません")
        return summarize(code, run_backtest(strategy_instance(name), bars, start, end, fill))
    except Exception as e:  # noqa: BLE001  1 銘柄の失敗で全体を止めない
        return Summary(code, error=f"{type(e).__name__}: {e}")


def format_pct(value: float, sign: bool = True) -> str:
    if math.isnan(value):
        return "—"
    return f"{value:+.2f}%" if sign else f"{value:.1f}%"


# ---- 画面 ----

@dataclass(frozen=True)
class Settings:
    """バックテストの条件(ストラテジー・期間・約定の値)。"""
    strategy: str
    start: pd.Timestamp
    end: pd.Timestamp
    fill: str = FILL_CLOSE  # 約定の値(FILL_MODES)

    def describe(self) -> str:
        return f"{self.strategy} / {self.start:%Y-%m-%d}〜{self.end:%Y-%m-%d} / {FILL_MODES[self.fill]}で約定"


class BacktestFrame(ttk.Frame):
    """共通の条件の欄と、スクリーニング・個別銘柄の深掘りの 2 つのタブをまとめた部品。"""

    def __init__(self, master):
        super().__init__(master)
        self.listed = load_listed()
        self.strategies = all_strategies()
        if not self.strategies:
            raise ValueError("ストラテジーが 1 つもありません。")
        self._build_settings()
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(side="top", fill="both", expand=True, padx=4, pady=(4, 4))
        self.screening = ScreeningTab(self.tabs, self)
        self.detail = DetailTab(self.tabs, self)
        self.tabs.add(self.screening, text="スクリーニング")
        self.tabs.add(self.detail, text="個別銘柄の深掘り")

    # ---- 共通の条件 ----

    def _build_settings(self):
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=6, pady=(6, 0))
        ttk.Label(bar, text="ストラテジー").pack(side="left")
        self.strategy_var = tk.StringVar(value=next(iter(self.strategies)))
        box = ttk.Combobox(bar, textvariable=self.strategy_var, values=list(self.strategies), state="readonly", width=16)
        box.pack(side="left", padx=(4, 6))
        box.bind("<<ComboboxSelected>>", lambda e: self._show_description())
        self.description_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.description_var, foreground="#555555").pack(side="left", padx=(0, 16))

        ttk.Label(bar, text="期間").pack(side="left")
        self.period_var = tk.StringVar(value=next(iter(PERIODS)))
        period_box = ttk.Combobox(bar, textvariable=self.period_var, values=list(PERIODS), state="readonly", width=10)
        period_box.pack(side="left", padx=(4, 6))
        period_box.bind("<<ComboboxSelected>>", lambda e: self._apply_period())
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.start_var, width=11).pack(side="left")
        ttk.Label(bar, text="〜").pack(side="left", padx=2)
        ttk.Entry(bar, textvariable=self.end_var, width=11).pack(side="left")
        ttk.Label(bar, text="(YYYY-MM-DD)", foreground="#777777").pack(side="left", padx=(4, 0))

        ttk.Label(bar, text="約定").pack(side="left", padx=(16, 0))
        self.fill_var = tk.StringVar(value=FILL_CLOSE)
        for fill, label in FILL_MODES.items():
            ttk.Radiobutton(bar, text=label, value=fill, variable=self.fill_var).pack(side="left", padx=(4, 0))
        self._apply_period()
        self._show_description()

    def _show_description(self):
        self.description_var.set(self.strategies[self.strategy_var.get()].description)

    def _apply_period(self):
        """期間の選択肢に合わせて、始まりと終わりの日を入れる。終わりは今日。"""
        end = pd.Timestamp.today().normalize()
        years = PERIODS[self.period_var.get()]
        start = pd.Timestamp(START_DATE) if years is None else end - pd.DateOffset(years=years)
        self.start_var.set(f"{start:%Y-%m-%d}")
        self.end_var.set(f"{end:%Y-%m-%d}")

    def settings(self) -> Settings | None:
        """今の条件。日付が正しくなければ、理由を出して None を返す。"""
        try:
            start = pd.Timestamp(self.start_var.get().strip())
            end = pd.Timestamp(self.end_var.get().strip())
        except ValueError:
            messagebox.showerror("fina_bktest", "期間の日付は YYYY-MM-DD の形で入力してください。", parent=self)
            return None
        if start > end:
            messagebox.showerror("fina_bktest", "期間の始まりが、終わりより後になっています。", parent=self)
            return None
        return Settings(self.strategy_var.get(), start, end, self.fill_var.get())

    def set_settings(self, settings: Settings) -> None:
        """条件の欄を settings に合わせる(スクリーニングの結果から深掘りへ移るときに使う)。"""
        self.strategy_var.set(settings.strategy)
        self._show_description()
        self.start_var.set(f"{settings.start:%Y-%m-%d}")
        self.end_var.set(f"{settings.end:%Y-%m-%d}")
        self.fill_var.set(settings.fill)

    # ---- 共通の部品 ----

    def stock_name(self, code: str) -> str:
        name = self.listed["銘柄名"].get(code)
        return name if isinstance(name, str) else ""

    def stock_market(self, code: str) -> str:
        return market_group(self.listed["市場・商品区分"].get(code, ""))

    def open_detail(self, code: str, settings: Settings) -> None:
        """個別銘柄の深掘りに切り替えて、code を settings の条件で調べる。"""
        self.set_settings(settings)
        self.tabs.select(self.detail)
        self.detail.run(code, settings)

    def stop(self) -> None:
        self.screening.stop()


def make_table(parent, columns: dict, height: int) -> ttk.Treeview:
    """見出し付きの表と縦のスクロールバーを parent に置く。"""
    frame = ttk.Frame(parent)
    frame.pack(side="top", fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=list(columns), show="headings", selectmode="browse", height=height)
    for column, (heading, width, anchor) in columns.items():
        tree.heading(column, text=heading)
        tree.column(column, width=width, anchor=anchor, stretch=column in ("name", "note"))
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    tree.tag_configure("plus", foreground="#1b7f5f")
    tree.tag_configure("minus", foreground="#c62828")
    return tree


class ScreeningTab(ttk.Frame):
    """スクリーニングのモード。選んだ市場の全銘柄の成績を調べ、上位と下位を表に出す。"""

    def __init__(self, master, app: BacktestFrame):
        super().__init__(master)
        self.app = app
        self.queue = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.results: list[Summary] = []
        self.total = 0
        self.run_settings: Settings | None = None  # 表に出している結果の条件

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=(6, 0))
        ttk.Label(bar, text="市場").pack(side="left")
        self.market_vars = {name: tk.BooleanVar(value=checked) for name, checked in MARKETS.items()}
        self.market_checks = []
        for name, var in self.market_vars.items():
            check = ttk.Checkbutton(bar, text=name, variable=var)
            check.pack(side="left", padx=(6, 0))
            self.market_checks.append(check)
        self.run_button = ttk.Button(bar, text="スクリーニング", command=self._on_run_button)
        self.run_button.pack(side="left", padx=(16, 0))

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(side="top", fill="x", padx=4, pady=(6, 0))
        self.status_var = tk.StringVar(value="市場を選んで「スクリーニング」を押してください。")
        ttk.Label(self, textvariable=self.status_var, justify="left").pack(side="top", fill="x", padx=6, pady=2)

        self.tables = {}
        for key, title, height in (
            ("top", f"成績の良かった {TOP_COUNT} 銘柄(合計損益の高い順)", TOP_COUNT),
            ("bottom", f"成績の悪かった {BOTTOM_COUNT} 銘柄(合計損益の低い順)", BOTTOM_COUNT),
        ):
            ttk.Label(self, text=title, font=("TkDefaultFont", 9, "bold")).pack(side="top", anchor="w", padx=6, pady=(8, 2))
            box = ttk.Frame(self)
            box.pack(side="top", fill="both", expand=key == "top", padx=4)
            tree = make_table(box, RANK_COLUMNS, height)
            tree.bind("<Double-Button-1>", lambda e, t=tree: self._on_double_click(t, e))
            tree.bind("<Return>", lambda e, t=tree: self._open(t.focus()))
            self.tables[key] = tree
        ttk.Label(
            self, text="銘柄をダブルクリック(または Enter)で、個別銘柄の深掘りに切り替わります。", foreground="#777777",
        ).pack(side="top", anchor="w", padx=6, pady=(2, 4))

    # ---- 実行 ----

    def _on_run_button(self):
        if self.running:
            self.cancel.set()
            self.run_button.configure(state="disabled")
        else:
            self.start()

    def start(self) -> None:
        settings = self.app.settings()
        if settings is None:
            return
        markets = {name for name, var in self.market_vars.items() if var.get()}
        if not markets:
            messagebox.showinfo("fina_bktest", "市場を 1 つ以上選んでください。", parent=self)
            return
        groups = self.app.listed["市場・商品区分"].map(market_group)
        codes = self.app.listed.index[groups.isin(markets)].tolist()
        if not codes:
            messagebox.showinfo("fina_bktest", "選んだ市場の銘柄が、銘柄一覧にありません。", parent=self)
            return
        self.run_settings = settings
        self.results, self.total = [], len(codes)
        self.cancel.clear()
        self._set_running(True)
        for tree in self.tables.values():
            tree.delete(*tree.get_children())
        self.progress.configure(maximum=self.total, value=0)
        self.status_var.set(f"検証中: 0 / {self.total} 銘柄({settings.describe()})")
        tasks = [(code, settings.strategy, settings.start, settings.end, settings.fill) for code in codes]
        threading.Thread(target=self._scan, args=(tasks,), daemon=True).start()
        self.after(POLL_INTERVAL, self._poll)

    def _scan(self, tasks) -> None:
        """全銘柄の成績を出し、queue に入れる(スレッドで動く)。最後に None(終わり)か例外(失敗)を入れる。"""
        try:
            with ProcessPoolExecutor(max_workers=WORKERS) as pool:
                for summary in pool.map(evaluate, tasks, chunksize=CHUNK_SIZE):
                    if self.cancel.is_set():
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    self.queue.put(summary)
            self.queue.put(None)
        except Exception as e:  # noqa: BLE001  プロセスを起動できないなど。画面に出して終える
            self.queue.put(e)

    def _poll(self):
        finished = False
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, Summary):
                self.results.append(item)
            else:
                finished = True
                if isinstance(item, Exception):
                    messagebox.showerror("fina_bktest", f"スクリーニングに失敗しました。\n{item}", parent=self)
        self.progress.configure(value=len(self.results))
        if finished:
            self._set_running(False)
            self._show_results()
        else:
            self.status_var.set(f"検証中: {len(self.results)} / {self.total} 銘柄({self.run_settings.describe()})")
            self.after(POLL_INTERVAL, self._poll)

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.run_button.configure(text="中止" if running else "スクリーニング", state="normal")
        for check in self.market_checks:
            check.configure(state="disabled" if running else "normal")

    def stop(self) -> None:
        self.cancel.set()

    # ---- 結果 ----

    def _show_results(self):
        traded = [s for s in self.results if s.trades]
        ranked = sorted(traded, key=lambda s: s.total, reverse=True)
        top = ranked[:TOP_COUNT]
        top_codes = {s.code for s in top}
        bottom = [s for s in reversed(ranked) if s.code not in top_codes][:BOTTOM_COUNT]  # 上位と重ねない
        for key, summaries in (("top", top), ("bottom", bottom)):
            tree = self.tables[key]
            tree.delete(*tree.get_children())
            for rank, s in enumerate(summaries, 1):
                tree.insert("", "end", iid=s.code, tags=["plus" if s.total > 0 else "minus"], values=(
                    rank if key == "top" else len(ranked) - rank + 1,
                    s.code, self.app.stock_name(s.code), self.app.stock_market(s.code),
                    s.trades, format_pct(s.win_rate, sign=False), format_pct(s.average), format_pct(s.total),
                    format_pct(s.best), format_pct(s.worst), format_pct(s.drawdown),
                ))
        self.status_var.set(self._summary_text(traded))

    def _summary_text(self, traded: list[Summary]) -> str:
        text = f"{self.run_settings.describe()}: {len(self.results)} 銘柄を検証"
        if len(self.results) < self.total:
            text += f"(中止。全 {self.total} 銘柄)"
        trades = sum(s.trades for s in traded)
        if trades:
            wins = sum(s.wins for s in traded)
            average = sum(s.average * s.trades for s in traded) / trades
            text += (
                f"。取引があった銘柄 {len(traded)}、全 {trades} 取引: 勝率 {wins / trades:.0%}、"
                f"1 取引の平均損益 {average:+.2f}%"
            )
        else:
            text += "。取引は 1 つもありませんでした"
        missing = sum(s.error == "データがありません" for s in self.results)
        failed = sum(bool(s.error) for s in self.results) - missing
        notes = [f"{label} {count} 銘柄" for label, count in (("データなし", missing), ("失敗", failed)) if count]
        return text + ("\n" + "、".join(notes) if notes else "")

    def _on_double_click(self, tree: ttk.Treeview, event):
        if tree.identify_region(event.x, event.y) == "cell":
            self._open(tree.identify_row(event.y))

    def _open(self, code: str):
        if code and self.run_settings is not None:
            self.app.open_detail(code, self.run_settings)


class DetailTab(ttk.Frame):
    """個別銘柄の深掘りのモード。1 銘柄の売買をチャートと表で見る。"""

    def __init__(self, master, app: BacktestFrame):
        super().__init__(master)
        self.app = app
        self.code: str | None = None
        self.trades: list[Trade] = []

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=(6, 0))
        self.search = StockSearchBox(
            bar, stock_index(app.listed),
            on_pick=lambda stock: self.run(stock.code),
            on_unmatched=lambda text: self.run(text.upper().removesuffix(".T")),
            label="銘柄", button="検証", dropdown=True, entry_width=18,
        )
        self.search.pack(side="left")
        self.status_var = tk.StringVar(value="銘柄を入力して「検証」を押すか、スクリーニングの結果をダブルクリックしてください。")
        ttk.Label(bar, textvariable=self.status_var, justify="left").pack(side="left", padx=(12, 0))

        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(side="top", fill="both", expand=True, padx=4, pady=(4, 4))
        self.chart = ChartFrame(panes)
        lower = ttk.Frame(panes)
        self.summary_var = tk.StringVar()
        ttk.Label(lower, textvariable=self.summary_var, justify="left").pack(side="top", anchor="w", padx=2, pady=(4, 2))
        self.table = make_table(lower, TRADE_COLUMNS, 8)
        self.table.bind("<Double-Button-1>", self._on_double_click)
        self.table.bind("<Return>", lambda e: self._show_trade(self.table.focus()))
        ttk.Label(
            lower, text="売買をダブルクリック(または Enter)で、チャートがその位置へ動きます。", foreground="#777777",
        ).pack(side="top", anchor="w", padx=2, pady=(2, 0))
        panes.add(self.chart, weight=3)
        panes.add(lower, weight=1)

    def run(self, code: str, settings: Settings | None = None) -> None:
        """code を settings(省略したら今の条件)で調べ、チャートと売買の一覧を出す。"""
        settings = settings or self.app.settings()
        if settings is None or not code:
            return
        bars = load_bars(code)
        if bars is None:
            self.status_var.set(f"{code} の株価データがありません。先に python core_gather.py {code} を実行してください。")
            return
        strategy = self.app.strategies[settings.strategy]()
        self.code = code
        self.trades = run_backtest(strategy, bars, settings.start, settings.end, settings.fill)
        self.status_var.set(f"{code} {self.app.stock_name(code)}  ({settings.describe()})")
        self._show_summary(summarize(code, self.trades))
        self._fill_table()
        self._show_on_chart()
        if self.trades:
            self.table.selection_set("0")
            self.table.focus("0")

    def _show_summary(self, s: Summary) -> None:
        if not s.trades:
            self.summary_var.set("期間中の取引はありません。")
            return
        self.summary_var.set(
            f"取引 {s.trades} 回(勝ち {s.wins} 回、勝率 {format_pct(s.win_rate, sign=False)})   "
            f"平均損益 {format_pct(s.average)}   合計損益 {format_pct(s.total)}   "
            f"最良 {format_pct(s.best)}   最悪 {format_pct(s.worst)}   最大DD {format_pct(s.drawdown)}"
        )

    def _fill_table(self):
        self.table.delete(*self.table.get_children())
        for number, t in enumerate(self.trades):
            e, x = t.entry, t.exit
            self.table.insert("", "end", iid=str(number), tags=["plus" if t.return_pct > 0 else "minus"], values=(
                number + 1, f"{e.date:%Y-%m-%d}", f"{e.price:,.1f}",
                f"{x.date:%Y-%m-%d}", f"{x.price:,.1f}", x.reason, format_pct(t.return_pct), t.bars_held,
                f"{e.take_profit:,.1f}" if math.isfinite(e.take_profit) else "—",
                f"{e.stop_loss:,.1f}" if e.stop_loss > 0 else "—",
                e.note,
            ))

    def _show_on_chart(self):
        """チャートを表示中の銘柄にして、売買ポイントを重ねる。"""
        if self.chart.code != self.code:
            self.chart.set_code(self.code)
        self.chart.set_trades([
            TradeMark(t.entry.date, t.entry.price, t.exit.date, t.exit.price, t.entry.take_profit, t.entry.stop_loss)
            for t in self.trades
        ])

    def _on_double_click(self, event):
        if self.table.identify_region(event.x, event.y) == "cell":
            self._show_trade(self.table.identify_row(event.y))

    def _show_trade(self, iid: str):
        """売買 1 つを選び、チャートをその位置へ動かす。"""
        if not iid or self.code is None:
            return
        if self.chart.code != self.code:  # チャートの銘柄欄で別の銘柄に変えていたら、戻す
            self._show_on_chart()
        self.chart.select_trade(int(iid))


def main() -> int:
    root = tk.Tk()
    root.title("ToreCan backtest")
    root.geometry("1500x940")
    try:
        frame = BacktestFrame(root)
    except (FileNotFoundError, ValueError) as e:
        messagebox.showerror("fina_bktest", str(e), parent=root)
        return 1
    frame.pack(fill="both", expand=True)

    def on_close():
        frame.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
