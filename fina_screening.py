"""コンディションが成り立っている銘柄を、全銘柄から探す(スクリーニング)。

core_condition.py に登録されているすべてのコンディション(CONDITIONS)について、
各銘柄の最新の足(日足)でその状態が成り立っているかを調べる。
どれか 1 つでも成り立っている銘柄を一覧に出し、どのコンディションが当てはまったかを示す。
選んだ銘柄のチャートは、右側(gui_chart.py の ChartFrame)に表示する。

株価は取得しない。stock_database フォルダの保存済みデータだけを使うので、
先に core_gather.py で全銘柄の株価を最新にしておくこと。

使い方:
    python fina_screening.py                   # プライムを選んだ状態で起動
    python fina_screening.py --market グロース  # 市場を選んだ状態で起動(プライム・スタンダード・グロース・すべて)

操作:
    スクリーニング  市場を選んで「スクリーニング」ボタン(実行中は「中止」になる)
    絞り込み        「最新の取引日の銘柄だけ」のチェック。
                    スクリーニングをやり直さずに、一覧だけが変わる
    並べ替え        一覧の見出しをクリック(もう一度クリックで逆順)。
                    「コンディション」で並べ替えると、同じコンディション・状態の銘柄がまとまる
    ウォッチリスト  「選んだ銘柄を追加」「一覧の全銘柄を追加」ボタンで、ウォッチリスト(watchlist.json)の
                    グループに足す(fina_watchlist.py で見られる)
    チャートの表示  一覧の銘柄をクリック、または上下キーで選ぶ。
                    当てはまったコンディションがチャートの最下段に出る(複数あれば先頭のもの)。
                    チャートの操作は gui_chart.py と同じ

判定は 1 銘柄ごとに独立しているので、複数のプロセスに分けて同時に行う。
"""

import math
import os
import queue
import sys
import threading
import tkinter as tk
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from tkinter import messagebox, ttk

import pandas as pd

from gui_theme import apply_theme
from gui_chart import ChartFrame
from core_settings import app_title
from core_condition import CONDITIONS
from core_gather import csv_path_for, load_saved
from core_stocks import load_listed
from gui_widgets import add_to_watchlist

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
LIST_WIDTH = 640     # 一覧の最初の幅(ピクセル)

# 一覧の列(列の名前 -> (見出し、幅、文字の寄せ方))
LIST_COLUMNS = {
    "code": ("コード", 54, "center"),
    "name": ("銘柄名", 170, "w"),
    "condition": ("コンディション", 170, "w"),
    "close": ("終値", 74, "e"),
    "change": ("前日比", 62, "e"),
    "date": ("日付", 84, "center"),
    "industry": ("業種", 110, "w"),
}


@dataclass(frozen=True, order=True)
class Hit:
    """最新の足で成り立っていた、コンディションの状態 1 つ。"""
    condition: str  # コンディションの表示名(CONDITIONS のキー)
    state: str      # 状態の名前
    note: str = ""  # コンディションが付けた説明

    @property
    def label(self) -> str:
        """一覧に出す名前(例: RSIダイバージェンス 強気)。"""
        return f"{self.condition} {CONDITIONS[self.condition].states[self.state].label}"

    @property
    def tag(self) -> str:
        """一覧の行に色を付けるための名前。"""
        return f"{self.condition}/{self.state}"


@dataclass(frozen=True)
class Judgement:
    """1 銘柄の判定結果。最新の足で成り立っているコンディションの状態を持つ。"""
    code: str
    date: pd.Timestamp | None = None  # 最新の足の日付
    close: float = math.nan
    change: float = math.nan          # 前日比(%)
    hits: tuple[Hit, ...] = ()        # 成り立っているコンディションの状態。なければ空
    error: str = ""                   # 判定できなかった理由。判定できたら空


def judge(code: str) -> Judgement:
    """1 銘柄について、最新の足ですべてのコンディションが成り立っているかを調べる。

    別のプロセスで動かすので、1 銘柄の失敗で全体が止まらないよう、例外は結果として返す。
    """
    try:
        daily = load_saved(csv_path_for(code))
        if daily is None or daily.dropna().empty:
            return Judgement(code, error="データがありません")
        daily = daily.dropna()
        hits = []
        for name, condition_class in CONDITIONS.items():
            last = condition_class().compute(daily).iloc[-1]
            hits += [
                Hit(name, state, str(last.get("note", "")))
                for state in condition_class.states if last[state]
            ]
        close = daily["Close"]
        change = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) > 1 else math.nan
        return Judgement(code, daily.index[-1], float(close.iloc[-1]), float(change), tuple(hits))
    except Exception as e:  # noqa: BLE001  壊れた CSV など、理由を問わず次の銘柄へ進む
        return Judgement(code, error=f"{type(e).__name__}: {e}")


class ScreeningFrame(ttk.Frame):
    """スクリーニングの操作・結果の一覧(左)と、チャート(右)をまとめた部品。"""

    def __init__(self, master):
        super().__init__(master)
        self.listed = load_listed()
        self.results: list[Judgement] = []  # 判定した全銘柄の結果(該当しない銘柄も含む)
        self.shown: dict[str, Judgement] = {}  # 一覧に出している銘柄
        self.scanned = False           # スクリーニングを 1 度でも行ったか
        self.total = 0                 # 判定する銘柄数
        self.queue = queue.Queue()     # 判定用のスレッドから画面への受け渡し
        self.cancel = threading.Event()
        self.running = False
        self.sort_column, self.sort_reverse = "code", False

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, width=LIST_WIDTH)
        left.pack_propagate(False)
        self._build_controls(left)
        self._build_list(left)
        self.chart = ChartFrame(panes)
        panes.add(left, weight=0)
        panes.add(self.chart, weight=1)
        # 一覧の行を、当てはまった状態の色で表示する
        for name, condition_class in CONDITIONS.items():
            for state, style in condition_class.states.items():
                self.tree.tag_configure(Hit(name, state).tag, foreground=style.color)

    # ---- 画面の組み立て ----

    def _build_controls(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(side="top", fill="x", padx=4, pady=(4, 0))
        ttk.Label(bar, text="市場").pack(side="left")
        self.market_var = tk.StringVar(value=next(iter(MARKETS)))
        self.market_box = ttk.Combobox(
            bar, textvariable=self.market_var, values=list(MARKETS), state="readonly", width=11,
        )
        self.market_box.pack(side="left", padx=(4, 8))
        self.run_button = ttk.Button(bar, text="スクリーニング", command=self._on_run_button)
        self.run_button.pack(side="left", padx=(0, 12))

        # 売買停止や上場廃止でデータが古い銘柄を、一覧から除く
        self.latest_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bar, text="最新の取引日の銘柄だけ", variable=self.latest_only_var, command=self._refresh_list,
        ).pack(side="left")

        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(side="top", fill="x", padx=4, pady=(6, 0))
        self.status_var = tk.StringVar(
            value=f"市場を選んで「スクリーニング」を押してください。コンディションは {len(CONDITIONS)} 種類すべてを調べます。",
        )
        ttk.Label(parent, textvariable=self.status_var, wraplength=LIST_WIDTH - 12, justify="left").pack(
            side="top", fill="x", padx=6, pady=2,
        )

    def _build_list(self, parent):
        # 選択中の銘柄に当てはまったコンディションと、その説明(コンディションが付けた note)
        self.note_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.note_var, wraplength=LIST_WIDTH - 12, justify="left").pack(
            side="bottom", fill="x", padx=6, pady=4,
        )
        bar = ttk.Frame(parent)
        bar.pack(side="bottom", fill="x", padx=4, pady=(4, 0))
        ttk.Label(bar, text="ウォッチリストに").pack(side="left")
        ttk.Button(bar, text="選んだ銘柄を追加", command=lambda: self._add_to_watchlist(False)).pack(
            side="left", padx=(4, 0))
        ttk.Button(bar, text="一覧の全銘柄を追加", command=lambda: self._add_to_watchlist(True)).pack(
            side="left", padx=(4, 0))
        frame = ttk.Frame(parent)
        frame.pack(side="top", fill="both", expand=True, padx=(4, 0))
        self.tree = ttk.Treeview(frame, columns=list(LIST_COLUMNS), show="headings", selectmode="browse")
        for column, (heading, width, anchor) in LIST_COLUMNS.items():
            self.tree.heading(column, text=heading, command=lambda c=column: self._on_heading(c))
            self.tree.column(column, width=width, anchor=anchor, stretch=column in ("name", "industry"))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())

    # ---- スクリーニングの実行 ----

    def _on_run_button(self):
        if self.running:
            self.cancel.set()
            self.run_button.configure(state="disabled")
        else:
            self.start()

    def start(self) -> None:
        """選択中の市場の銘柄を、すべてのコンディションでスクリーニングする。"""
        market = self.listed["市場・商品区分"].fillna("")
        codes = self.listed.index[market.str.startswith(MARKETS[self.market_var.get()])].tolist()
        if not codes:
            messagebox.showinfo("fina_screening", "この市場の銘柄が、銘柄一覧にありません。", parent=self)
            return
        self.results, self.total, self.scanned = [], len(codes), True
        self.cancel.clear()
        self._set_running(True)
        self._refresh_list()
        self.progress.configure(maximum=self.total, value=0)
        self.status_var.set(f"判定中: 0 / {self.total} 銘柄")
        threading.Thread(target=self._scan, args=(codes,), daemon=True).start()
        self.after(POLL_INTERVAL, self._poll)

    def _scan(self, codes: list[str]) -> None:
        """全銘柄を判定し、結果を queue に入れる(判定用のスレッドで動く)。

        最後に、終わりの合図として None(正常・中止)または例外(失敗)を入れる。
        """
        try:
            with ProcessPoolExecutor(max_workers=WORKERS) as pool:
                for judgement in pool.map(judge, codes, chunksize=CHUNK_SIZE):
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
        self.market_box.configure(state="disabled" if running else "readonly")

    def stop(self) -> None:
        """実行中のスクリーニングを止める(ウィンドウを閉じるときに呼ぶ)。"""
        self.cancel.set()

    # ---- 結果の一覧 ----

    def _refresh_list(self):
        """絞り込みと並び順に合わせて、一覧を作り直す。"""
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.shown = {}
        self.note_var.set("")
        if self.running or not self.scanned:
            return

        judged = [j for j in self.results if not j.error]
        latest = max((j.date for j in judged), default=None)
        hits = [j for j in judged if j.hits]
        if self.latest_only_var.get():
            hits = [j for j in hits if j.date == latest]
        hits.sort(key=self._sort_key, reverse=self.sort_reverse)

        for j in hits:
            self.shown[j.code] = j
            self.tree.insert("", "end", iid=j.code, tags=[j.hits[0].tag], values=(
                j.code,
                self._listed_value(j.code, "銘柄名"),
                "、".join(hit.label for hit in j.hits),
                f"{j.close:,.1f}",
                "" if math.isnan(j.change) else f"{j.change:+.2f}%",
                f"{j.date:%Y-%m-%d}",
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
        if column == "condition":
            return j.hits
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

    def _on_select(self):
        selected = self.tree.selection()
        if not selected:
            return
        code = selected[0]
        hits = self.shown[code].hits
        self.note_var.set("\n".join(f"【{hit.label}】 {hit.note}".rstrip() for hit in hits))
        # チャートにも当てはまったコンディションを表示し、判定の根拠が見えるようにする
        self.chart.show_condition(hits[0].condition)
        if code != self.chart.code:
            self.chart.set_code(code)


def main() -> int:
    root = tk.Tk()
    apply_theme(root)  # ToreCan の色・フォント(gui_theme.py)
    root.title(app_title("screening"))
    root.geometry("1700x880")
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
