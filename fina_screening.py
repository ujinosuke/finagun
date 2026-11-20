"""条件セットに合う銘柄を、全銘柄から探す(スクリーニング)。

設計は pico_strategy-screening-design.md(7 章)。画面は左右に分かれる:
    左    条件パネル。プリセットを選ぶと条件の行に展開される。シグナル(どれか 1 つ)とフィルター(すべて。
          除外は成り立ったら見送る)の行を足し・直し・消して、条件セットを作る。名前を付けて保存もできる
    右    結果の一覧。点灯したシグナル・合図の日・理由(コンディションの note)を出す
    チャートは別のウィンドウ(gui_chart.py の ChartFrame)。小さい画面にも収まるよう、一覧とは分けている

判定(core_strategies.ConditionSet):
    シグナル    直近 N 本以内(新鮮さ。N は画面で決める)に、どれか 1 つが点灯した
    フィルター  最新の足で、すべて成り立っている(除外の行は成り立っていない)
    シグナルの行がないときは、フィルターだけで判定する(今の状態で銘柄を探す)

プリセット:
    組み込み      core_strategies.BUILTIN_SETS
    ストラテジー  core_strategies.all_strategies() の定義の買い条件(出口ルールはスクリーニングでは使わない)
    保存          利用者が名前を付けて保存した条件セット(screening_presets.json)

株価は取得しない。stock_database フォルダの保存済みデータだけを使うので、
先に core_gather.py で全銘柄の株価を最新にしておくこと。

使い方:
    python fina_screening.py                   # プライムを選んだ状態で起動
    python fina_screening.py --market グロース  # 市場を選んだ状態で起動(プライム・スタンダード・グロース・すべて)

操作:
    スクリーニング  条件を決め、市場を選んで「スクリーニング」ボタン(実行中は「中止」になる)
    絞り込み        「最新の取引日の銘柄だけ」のチェック。スクリーニングをやり直さずに、一覧だけが変わる
    並べ替え        一覧の見出しをクリック(もう一度クリックで逆順)
    ウォッチリスト  「…ウォッチリストへ」で、選んだ銘柄(または一覧の全銘柄)をウォッチリストのグループに足す
    銘柄比較        「銘柄比較に送る」で、一覧の全銘柄をウォッチリストのグループに足し、
                    銘柄比較(fina_compare.py)をそのグループで開く
    チャートの表示  一覧の銘柄をダブルクリック(または Enter)で、チャートのウィンドウを開く。
                    開いている間は、一覧で銘柄を選び直す(上下キーでも)と、チャートもその銘柄に変わる。
                    点灯したシグナルが、チャートの選択肢にあるコンディションなら最下段に出す

判定は 1 銘柄ごとに独立しているので、複数のプロセスに分けて同時に行う。
"""

import math
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial
from tkinter import messagebox, simpledialog, ttk

import numpy as np
import pandas as pd

from core_condition import CONDITIONS
from core_gather import APP_DIR, csv_path_for, load_saved
from core_settings import app_title
from core_stocks import load_listed
from core_strategies import (
    BUILTIN_SETS, DEFAULT_FRESHNESS, MAX_FRESHNESS, ConditionSet, RuleStrategy, all_strategies, load_user_presets,
    save_user_presets,
)
from core_watchlist import WATCHLIST_PATH, add_codes
from gui_chart import ChartFrame
from gui_conditionset import ConditionSetEditor
from gui_theme import PAD, apply_theme
from gui_widgets import add_to_watchlist, ask_watchlist_group

# 市場の選択肢(表示名 -> 「市場・商品区分」の先頭の文字)。先頭が最初に選ばれる。
# 空文字はすべての市場(ETF や REIT も含む。core_gather.py が取得しない市場は除く)
MARKETS = {
    "プライム": "プライム",
    "スタンダード": "スタンダード",
    "グロース": "グロース",
    "すべて": "",
}
# 同時に動かすプロセスの数
WORKERS = max(1, min((os.cpu_count() or 2) - 1, 8))
# 1 つのプロセスにまとめて渡す銘柄数。小さいほど進み具合が細かく分かり、中止も早く効く
CHUNK_SIZE = 16
POLL_INTERVAL = 100  # 判定結果を画面に反映する間隔(ミリ秒)
PANEL_WIDTH = 400    # 条件パネルの幅(ピクセル)
CHART_GEOMETRY = "1100x760"  # チャートのウィンドウの最初の大きさ

# プリセットの表示名の頭(種類の区別)
NEW_PRESET = "(新しい条件セット)"
PREFIX_BUILTIN = "組み込み: "
PREFIX_STRATEGY = "ストラテジー: "
PREFIX_USER = "保存: "

# 一覧の列(列の名前 -> (見出し、幅、文字の寄せ方))
LIST_COLUMNS = {
    "code": ("コード", 54, "center"),
    "name": ("銘柄名", 150, "w"),
    "signal": ("シグナル", 260, "w"),
    "signal_date": ("合図の日", 84, "center"),
    "close": ("終値", 74, "e"),
    "change": ("前日比", 62, "e"),
    "industry": ("業種", 130, "w"),
}


@dataclass(frozen=True)
class SignalHit:
    """直近 N 本以内に点灯したシグナル 1 つ(点灯した最後の足)。"""
    label: str           # 条件の行の表示(例: SMAクロス(5,25) の「ゴールデン」)
    condition: str       # パラメータ込みのコンディションの名前(チャートに出すときに使う)
    date: pd.Timestamp
    note: str = ""


@dataclass(frozen=True)
class Judgement:
    """1 銘柄の判定結果。"""
    code: str
    date: pd.Timestamp | None = None  # 最新の足の日付
    close: float = math.nan
    change: float = math.nan          # 前日比(%)
    matched: bool = False             # 条件セットに合ったか
    signals: tuple[SignalHit, ...] = ()  # 点灯したシグナル(合図の日の新しい順)
    reasons: tuple[str, ...] = ()     # フィルターの根拠(最新の足の note)
    error: str = ""                   # 判定できなかった理由。判定できたら空

    @property
    def signal_date(self) -> pd.Timestamp | None:
        """合図の日(いちばん新しいシグナルの日)。フィルターだけの条件なら最新の足の日。"""
        return self.signals[0].date if self.signals else self.date


def judge(condition_set: ConditionSet, code: str) -> Judgement:
    """1 銘柄が条件セットに合うかを調べる。

    別のプロセスで動かすので、1 銘柄の失敗で全体が止まらないよう、例外は結果として返す。
    """
    try:
        daily = load_saved(csv_path_for(code))
        if daily is None or daily.dropna().empty:
            return Judgement(code, error="データがありません")
        daily = daily.dropna()
        close = daily["Close"]
        change = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) > 1 else math.nan
        base = dict(code=code, date=daily.index[-1], close=float(close.iloc[-1]), change=float(change))

        result = condition_set.evaluate(daily)
        frames = result.frames
        start = max(len(daily) - condition_set.freshness_days, 0)  # 直近 N 本の最初の行
        signals = []
        for row, hit, frame in zip(condition_set.signals, result.signal_hits, frames):
            rows = np.flatnonzero(hit.to_numpy()[start:])
            if rows.size:
                i = start + int(rows[-1])
                note = str(frame["note"].iloc[i]) if "note" in frame else ""
                signals.append(SignalHit(row.label, row.built.name, daily.index[i], note))
        signals.sort(key=lambda s: s.date, reverse=True)
        filters_ok = bool(result.filter_ok.iloc[-1])
        matched = filters_ok and (bool(signals) or not condition_set.signals)
        reasons = []
        if matched:
            for row, frame in zip(condition_set.filters, frames[len(condition_set.signals):]):
                note = "" if row.exclude or "note" not in frame else str(frame["note"].iloc[-1])
                reasons.append(f"{'除外(当てはまらず)' if row.exclude else '条件'}: {row.label} {note}".rstrip())
        return Judgement(**base, matched=matched, signals=tuple(signals), reasons=tuple(reasons))
    except Exception as e:  # noqa: BLE001  壊れた CSV など、理由を問わず次の銘柄へ進む
        return Judgement(code, error=f"{type(e).__name__}: {e}")


def open_compare(group: str) -> None:
    """銘柄比較(fina_compare.py)を、ウォッチリストのグループを選んだ状態で開く。"""
    executable, flags = sys.executable, 0
    if sys.platform == "win32":  # 黒いコンソール窓を出さない
        pythonw = os.path.join(os.path.dirname(executable), "pythonw.exe")
        if os.path.exists(pythonw):
            executable = pythonw
        else:
            flags = subprocess.CREATE_NO_WINDOW
    subprocess.Popen([executable, str(APP_DIR / "fina_compare.py"), "--group", group], cwd=APP_DIR,
                     creationflags=flags)


class ScreeningFrame(ttk.Frame):
    """条件パネル(左)と結果の一覧(右)をまとめた部品。チャートは別のウィンドウで開く。"""

    def __init__(self, master):
        super().__init__(master)
        self.listed = load_listed()
        # 定義で書かれたストラテジーだけ、買い条件をプリセットにできる(コードで書いたストラテジーは除く)
        self.strategies = {name: s for name, s in all_strategies().items() if isinstance(s, RuleStrategy)}
        self.user_presets, problems = load_user_presets()
        self.results: list[Judgement] = []  # 判定した全銘柄の結果(合わなかった銘柄も含む)
        self.shown: dict[str, Judgement] = {}  # 一覧に出している銘柄
        self.scanned: ConditionSet | None = None  # 最後にスクリーニングした条件セット
        self.total = 0                 # 判定する銘柄数
        self.queue = queue.Queue()     # 判定用のスレッドから画面への受け渡し
        self.cancel = threading.Event()
        self.running = False
        self.sort_column, self.sort_reverse = "signal_date", True

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        panel = ttk.Frame(panes, width=PANEL_WIDTH, padding=(PAD, PAD, 0, PAD))
        panel.pack_propagate(False)
        self._build_panel(panel)
        results = ttk.Frame(panes)
        self._build_list(results)
        panes.add(panel, weight=0)
        panes.add(results, weight=1)
        self.chart_window: tk.Toplevel | None = None  # チャートのウィンドウ(開いていなければ None)
        self.chart: ChartFrame | None = None
        self._load_preset()
        if problems:
            self.status_var.set("\n".join(problems))

    # ---- 条件パネル ----

    def _preset_names(self) -> list[str]:
        return ([NEW_PRESET]
                + [PREFIX_BUILTIN + s.name for s in BUILTIN_SETS]
                + [PREFIX_STRATEGY + name for name in self.strategies]
                + [PREFIX_USER + p.name for p in self.user_presets])

    def _preset(self, label: str) -> ConditionSet:
        """プリセットの表示名から条件セットを返す。"""
        if label.startswith(PREFIX_BUILTIN):
            return next(s for s in BUILTIN_SETS if s.name == label.removeprefix(PREFIX_BUILTIN))
        if label.startswith(PREFIX_STRATEGY):
            return self.strategies[label.removeprefix(PREFIX_STRATEGY)].definition.buy
        if label.startswith(PREFIX_USER):
            return next(p for p in self.user_presets if p.name == label.removeprefix(PREFIX_USER))
        return ConditionSet()

    def _build_panel(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(side="top", fill="x")
        ttk.Label(bar, text="プリセット").pack(side="left")
        self.preset_var = tk.StringVar(value=NEW_PRESET)
        self.preset_box = ttk.Combobox(bar, textvariable=self.preset_var, values=self._preset_names(),
                                       state="readonly", width=30)
        self.preset_box.pack(side="left", padx=(4, 0), fill="x", expand=True)
        self.preset_box.bind("<<ComboboxSelected>>", lambda e: self._load_preset())
        bar = ttk.Frame(parent)
        bar.pack(side="top", fill="x", pady=(4, 0))
        self.save_button = ttk.Button(bar, text="名前を付けて保存", command=self._save_preset)
        self.save_button.pack(side="left")
        self.delete_button = ttk.Button(bar, text="保存した条件を削除", command=self._delete_preset)
        self.delete_button.pack(side="left", padx=(4, 0))
        self.description_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.description_var, style="Muted.TLabel", wraplength=PANEL_WIDTH - 24,
                  justify="left").pack(side="top", fill="x", pady=(4, 0))

        self.editor = ConditionSetEditor(parent, on_change=self._on_rows_changed, wraplength=PANEL_WIDTH - 190)
        self.editor.pack(side="top", fill="x", pady=(PAD, 0))

        options = ttk.Frame(parent)
        options.pack(side="top", fill="x", pady=(PAD, 0))
        ttk.Label(options, text="シグナルの新鮮さ: 直近").pack(side="left")
        self.freshness_var = tk.IntVar(value=DEFAULT_FRESHNESS)
        self.freshness_box = ttk.Spinbox(options, from_=1, to=MAX_FRESHNESS, textvariable=self.freshness_var, width=5)
        self.freshness_box.pack(side="left", padx=4)
        ttk.Label(options, text="本以内").pack(side="left")

        bar = ttk.Frame(parent)
        bar.pack(side="top", fill="x", pady=(PAD, 0))
        ttk.Label(bar, text="市場").pack(side="left")
        self.market_var = tk.StringVar(value=next(iter(MARKETS)))
        self.market_box = ttk.Combobox(bar, textvariable=self.market_var, values=list(MARKETS), state="readonly",
                                       width=11)
        self.market_box.pack(side="left", padx=(4, 8))
        self.run_button = ttk.Button(bar, text="スクリーニング", style="Accent.TButton", command=self._on_run_button)
        self.run_button.pack(side="left")

        # 売買停止や上場廃止でデータが古い銘柄を、一覧から除く
        self.latest_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(parent, text="最新の取引日の銘柄だけ", variable=self.latest_only_var,
                        command=self._refresh_list).pack(side="top", anchor="w", pady=(4, 0))
        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(side="top", fill="x", pady=(PAD, 0))
        self.status_var = tk.StringVar(value="条件を決めて「スクリーニング」を押してください。")
        ttk.Label(parent, textvariable=self.status_var, wraplength=PANEL_WIDTH - 24, justify="left").pack(
            side="top", fill="x", pady=(4, 0))

    def _load_preset(self):
        """選んだプリセットを、条件の行に展開する。"""
        preset = self._preset(self.preset_var.get())
        self.editor.set_rows(preset.signals, preset.filters)
        self.freshness_var.set(preset.freshness_days)
        self._show_description()

    def _show_description(self):
        label = self.preset_var.get()
        text = ""
        if label.startswith(PREFIX_STRATEGY):
            strategy = self.strategies[label.removeprefix(PREFIX_STRATEGY)]
            text = strategy.description + "\n(出口ルールはスクリーニングでは使いません)"
        self.delete_button.configure(state="normal" if label.startswith(PREFIX_USER) and not self.running
                                     else "disabled")
        self.description_var.set(text)

    def _on_rows_changed(self):
        """条件を変えたら、プリセットの選択は「新しい条件セット」に戻す(元のプリセットは変えない)。"""
        if self.preset_var.get() != NEW_PRESET:
            self.preset_var.set(NEW_PRESET)
            self._show_description()

    def condition_set(self, name: str = "") -> ConditionSet | None:
        """画面の条件セット。新鮮さが正しくなければ、理由を出して None を返す。"""
        try:
            freshness = int(self.freshness_var.get())
        except (tk.TclError, ValueError):
            freshness = 0
        if not 1 <= freshness <= MAX_FRESHNESS:
            messagebox.showerror("fina_screening", f"新鮮さは 1 - {MAX_FRESHNESS} の整数で入力してください。", parent=self)
            return None
        return ConditionSet(name, tuple(self.editor.signals), tuple(self.editor.filters), freshness)

    def _save_preset(self):
        """今の条件セットに名前を付けて保存する(ユーザープリセット)。同じ名前なら上書きを確かめる。"""
        current = self.condition_set()
        if current is None:
            return
        if not current.signals and not current.filters:
            messagebox.showinfo("fina_screening", "保存する条件がありません。", parent=self)
            return
        name = simpledialog.askstring("条件セットを保存", "名前", parent=self)
        name = (name or "").strip()
        if not name:
            return
        names = [p.name for p in self.user_presets]
        if name in names and not messagebox.askyesno(
                "fina_screening", f"「{name}」はもうあります。上書きしますか?", parent=self):
            return
        preset = ConditionSet(name, current.signals, current.filters, current.freshness_days)
        presets = [p for p in self.user_presets if p.name != name] + [preset]
        try:
            save_user_presets(presets)
        except OSError as e:
            messagebox.showerror("fina_screening", f"保存できません。\n{e}", parent=self)
            return
        self.user_presets = presets
        self.preset_box.configure(values=self._preset_names())
        self.preset_var.set(PREFIX_USER + name)
        self._show_description()
        self.status_var.set(f"条件セット「{name}」を保存しました。")

    def _delete_preset(self):
        label = self.preset_var.get()
        if not label.startswith(PREFIX_USER):
            return
        name = label.removeprefix(PREFIX_USER)
        if not messagebox.askyesno("fina_screening", f"保存した条件セット「{name}」を削除しますか?", parent=self):
            return
        presets = [p for p in self.user_presets if p.name != name]
        try:
            save_user_presets(presets)
        except OSError as e:
            messagebox.showerror("fina_screening", f"保存できません。\n{e}", parent=self)
            return
        self.user_presets = presets
        self.preset_box.configure(values=self._preset_names())
        self.preset_var.set(NEW_PRESET)
        self._load_preset()

    # ---- 結果の一覧 ----

    def _build_list(self, parent):
        # 選んだ銘柄の、点灯したシグナルとフィルターの根拠(コンディションが付けた note)
        self.note_var = tk.StringVar()
        note = ttk.Label(parent, textvariable=self.note_var, justify="left")
        note.pack(side="bottom", fill="x", padx=6, pady=4)
        # 一覧の幅に合わせて折り返す
        note.bind("<Configure>", lambda e: note.configure(wraplength=max(e.width - 4, 100)))
        bar = ttk.Frame(parent)
        bar.pack(side="bottom", fill="x", padx=4, pady=(4, 0))
        ttk.Button(bar, text="選んだ銘柄をウォッチリストへ", command=lambda: self._add_to_watchlist(False)).pack(side="left")
        ttk.Button(bar, text="全銘柄をウォッチリストへ", command=lambda: self._add_to_watchlist(True)).pack(
            side="left", padx=(4, 0))
        ttk.Button(bar, text="銘柄比較に送る", command=self._send_to_compare).pack(side="left", padx=(4, 0))
        frame = ttk.Frame(parent)
        frame.pack(side="top", fill="both", expand=True, padx=(4, 0), pady=(PAD, 0))
        self.tree = ttk.Treeview(frame, columns=list(LIST_COLUMNS), show="headings", selectmode="browse")
        for column, (heading, width, anchor) in LIST_COLUMNS.items():
            self.tree.heading(column, text=heading, command=lambda c=column: self._on_heading(c))
            # 列の幅は固定にする(伸び縮みさせると、狭いウィンドウで後ろの列が切れて、横に送れなくなる)
            self.tree.column(column, width=width, minwidth=40, anchor=anchor, stretch=False)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Return>", lambda e: self.open_chart())

    # ---- スクリーニングの実行 ----

    def _on_run_button(self):
        if self.running:
            self.cancel.set()
            self.run_button.configure(state="disabled")
        else:
            self.start()

    def start(self) -> None:
        """選択中の市場の銘柄を、今の条件セットでスクリーニングする。"""
        condition_set = self.condition_set(self.preset_var.get())
        if condition_set is None:
            return
        if not condition_set.signals and not condition_set.filters:
            messagebox.showinfo("fina_screening", "シグナルかフィルターを 1 つ以上足してください。", parent=self)
            return
        market = self.listed["市場・商品区分"].fillna("")
        codes = self.listed.index[market.str.startswith(MARKETS[self.market_var.get()])].tolist()
        if not codes:
            messagebox.showinfo("fina_screening", "この市場の銘柄が、銘柄一覧にありません。", parent=self)
            return
        self.results, self.total, self.scanned = [], len(codes), condition_set
        self.cancel.clear()
        self._set_running(True)
        self._refresh_list()
        self.progress.configure(maximum=self.total, value=0)
        self.status_var.set(f"判定中: 0 / {self.total} 銘柄")
        threading.Thread(target=self._scan, args=(condition_set, codes), daemon=True).start()
        self.after(POLL_INTERVAL, self._poll)

    def _scan(self, condition_set: ConditionSet, codes: list[str]) -> None:
        """全銘柄を判定し、結果を queue に入れる(判定用のスレッドで動く)。

        最後に、終わりの合図として None(正常・中止)または例外(失敗)を入れる。
        """
        try:
            with ProcessPoolExecutor(max_workers=WORKERS) as pool:
                for judgement in pool.map(partial(judge, condition_set), codes, chunksize=CHUNK_SIZE):
                    if self.cancel.is_set():
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    self.queue.put(judgement)
            self.queue.put(None)
        except Exception as e:  # noqa: BLE001  プロセスを起動できないなど。画面に出して終える
            self.queue.put(e)

    def _poll(self):
        """判定用のスレッドから届いた結果を受け取り、進み具合を表示する。"""
        finished = False
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, Judgement):
                self.results.append(item)
            else:
                finished = True
                if isinstance(item, Exception):
                    messagebox.showerror("fina_screening", f"スクリーニングに失敗しました。\n{item}", parent=self)
        self.progress.configure(value=len(self.results))
        if finished:
            self._set_running(False)
            self._refresh_list()
        else:
            self.status_var.set(f"判定中: {len(self.results)} / {self.total} 銘柄")
            self.after(POLL_INTERVAL, self._poll)

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.run_button.configure(text="中止" if running else "スクリーニング", state="normal")
        state = "disabled" if running else "readonly"
        self.market_box.configure(state=state)
        self.preset_box.configure(state=state)
        self.freshness_box.configure(state="disabled" if running else "normal")
        self.save_button.configure(state="disabled" if running else "normal")
        self.editor.set_enabled(not running)
        self._show_description()

    def stop(self) -> None:
        """実行中のスクリーニングを止める(ウィンドウを閉じるときに呼ぶ)。"""
        self.cancel.set()

    # ---- 結果の一覧の表示 ----

    def _refresh_list(self):
        """絞り込みと並び順に合わせて、一覧を作り直す。"""
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.shown = {}
        self.note_var.set("")
        if self.running or self.scanned is None:
            return

        judged = [j for j in self.results if not j.error]
        latest = max((j.date for j in judged), default=None)
        hits = [j for j in judged if j.matched]
        if self.latest_only_var.get():
            hits = [j for j in hits if j.date == latest]
        hits.sort(key=self._sort_key, reverse=self.sort_reverse)

        for j in hits:
            self.shown[j.code] = j
            self.tree.insert("", "end", iid=j.code, values=(
                j.code,
                self._listed_value(j.code, "銘柄名"),
                "、".join(s.label for s in j.signals) or "(フィルターのみ)",
                f"{j.signal_date:%Y-%m-%d}",
                f"{j.close:,.1f}",
                "" if math.isnan(j.change) else f"{j.change:+.2f}%",
                self._listed_value(j.code, "33業種区分"),
            ))
        self.status_var.set(self._summary(len(hits), judged, latest))

        # 選んでいた銘柄が残っていれば選び直し、なければ先頭の銘柄を選ぶ
        if hits:
            code = selected[0] if selected and selected[0] in self.shown else hits[0].code
            self.tree.selection_set(code)
            self.tree.see(code)

    def _summary(self, hit_count: int, judged: list[Judgement], latest: pd.Timestamp | None) -> str:
        """結果のまとめ(該当した銘柄数や、判定できなかった銘柄数)。"""
        if latest is None:
            return "判定できた銘柄がありません。先に core_gather.py で株価を取得してください。"
        text = f"{latest:%Y-%m-%d} 時点: {hit_count} 銘柄が該当 / 判定 {len(judged)} 銘柄"
        if self.scanned.signals:
            text += f"(シグナルは直近 {self.scanned.freshness_days} 本以内)"
        if len(self.results) < self.total:
            text += f"(中止。全 {self.total} 銘柄)"
        stale = sum(j.date < latest for j in judged)
        missing = sum(j.error == "データがありません" for j in self.results)
        failed = len(self.results) - len(judged) - missing
        notes = [
            f"{label} {count} 銘柄"
            for label, count in (("データが古い", stale), ("データなし", missing), ("判定に失敗", failed))
            if count
        ]
        return text + ("\n" + "、".join(notes) if notes else "")

    def _listed_value(self, code: str, column: str) -> str:
        value = self.listed.at[code, column]
        return value if isinstance(value, str) and value != "-" else ""

    def _sort_key(self, j: Judgement):
        column = self.sort_column
        if column == "name":
            return self._listed_value(j.code, "銘柄名")
        if column == "industry":
            return self._listed_value(j.code, "33業種区分")
        if column == "change":
            return -math.inf if math.isnan(j.change) else j.change
        if column == "signal":
            return tuple(s.label for s in j.signals)
        if column == "signal_date":
            return (j.signal_date, j.code)
        return getattr(j, column)

    def _on_heading(self, column: str):
        """見出しのクリックで並べ替える。同じ見出しをもう一度クリックすると逆順。"""
        self.sort_reverse = column == self.sort_column and not self.sort_reverse
        self.sort_column = column
        self._refresh_list()

    def _add_to_watchlist(self, everything: bool):
        """選んだ銘柄(everything なら一覧の全銘柄)を、ウォッチリストのグループに足す。"""
        codes = list(self.tree.get_children()) if everything else list(self.tree.selection())
        message = add_to_watchlist(self, codes, "fina_screening")
        if message:
            self.status_var.set(message)

    def _send_to_compare(self):
        """一覧の全銘柄をウォッチリストのグループに足し、銘柄比較をそのグループで開く(グループは銘柄比較と共有)。"""
        codes = list(self.tree.get_children())
        if not codes:
            self.status_var.set("送る銘柄がありません。")
            return
        group = ask_watchlist_group(self, len(codes))
        if group is None:
            return
        try:
            add_codes(group, codes)
            open_compare(group)
        except (OSError, ValueError) as e:
            messagebox.showerror("fina_screening", f"{WATCHLIST_PATH.name} に保存できないか、銘柄比較を開けません。\n{e}",
                                 parent=self)
            return
        self.status_var.set(f"「{group}」に {len(codes)} 銘柄を入れて、銘柄比較を開きました。")

    def _on_select(self):
        selected = self.tree.selection()
        if not selected:
            return
        code = selected[0]
        j = self.shown[code]
        lines = [f"【{s.label}】 {s.date:%Y-%m-%d} {s.note}".rstrip() for s in j.signals] + list(j.reasons)
        self.note_var.set("\n".join(lines))
        if self.chart_window is not None:  # チャートを開いている間は、選んだ銘柄に合わせる
            self._show_chart(code)

    def _on_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell":  # 見出しのダブルクリックは並べ替えだけ
            self.open_chart()

    def open_chart(self) -> None:
        """選んだ銘柄のチャートを、別のウィンドウで開く(開いていれば、そのウィンドウを使い回して前に出す)。"""
        selected = self.tree.selection()
        if not selected:
            return
        if self.chart_window is None:
            self.chart_window = tk.Toplevel(self)
            self.chart_window.title(app_title("chart"))
            self.chart_window.geometry(CHART_GEOMETRY)
            self.chart = ChartFrame(self.chart_window)
            self.chart.pack(fill="both", expand=True)
            self.chart_window.protocol("WM_DELETE_WINDOW", self._close_chart)
        self._show_chart(selected[0])
        self.chart_window.deiconify()
        self.chart_window.lift()

    def _show_chart(self, code: str) -> None:
        if code != self.chart.code:
            self.chart.set_code(code)
        # 点灯したシグナルが、チャートの選択肢にあるコンディションなら、最下段に出して根拠を見えるようにする
        # (銘柄を表示した後に出す。銘柄のファイルの表示で上書きされないように。この表示は保存されない)
        for signal in self.shown[code].signals:
            if signal.condition in CONDITIONS:
                self.chart.show_condition(signal.condition)
                break

    def _close_chart(self) -> None:
        self.chart_window.destroy()
        self.chart_window, self.chart = None, None


def main() -> int:
    root = tk.Tk()
    apply_theme(root)  # ToreCan の色・フォント(gui_theme.py)
    root.title(app_title("screening"))
    root.geometry("1100x720")
    root.minsize(860, 560)
    try:
        frame = ScreeningFrame(root)
    except FileNotFoundError as e:
        messagebox.showerror("fina_screening", str(e), parent=root)
        return 1
    frame.pack(fill="both", expand=True)
    # 引数の市場を選んだ状態で起動する(python fina_screening.py --market グロース)
    if "--market" in sys.argv[1:-1]:
        market = sys.argv[sys.argv.index("--market") + 1]
        if market in MARKETS:
            frame.market_var.set(market)

    def on_close():
        frame.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
