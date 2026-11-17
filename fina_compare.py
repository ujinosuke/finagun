"""銘柄の指標を表にして比べる(表計算ソフトのような比較表)。

行に銘柄、列に 銘柄 / コード / 市場 / 株価 / 時価総額 / EV/Rev / EV/EBITDA / PER(実績) / PER(予想) /
PSR / 配当利回り を出す。
表の最後の 4 行には、EV/Rev から配当利回りまでの列について、
登録した銘柄の High(最大)・Average(平均)・Median(中央値)・Low(最小)を出す。
値を取得できなかった銘柄と、値がマイナスの銘柄(赤字で PER(予想) がマイナスなど)は、
その列の計算から除く(表にはマイナスの値もそのまま出す)。
EV/Rev から右の列では、銘柄の中で一番高い値(High と同じ値)を赤い文字で出す。

指標は yfinance(Yahoo Finance)から取得する(起動時と「更新」ボタンで取り直す)。
    株価        現在値(currentPrice)
    時価総額    億円単位(marketCap)
    EV/Rev      企業価値 / 売上高(enterpriseToRevenue)
    EV/EBITDA   企業価値 / EBITDA(enterpriseToEbitda)
    PER(実績)   株価収益率。直近 12 か月の実績 EPS から(trailingPE)
    PER(予想)   株価収益率。今期予想 EPS から(forwardPE)
    PSR         株価売上高倍率。直近 12 か月の実績(priceToSalesTrailing12Months)
    配当利回り  予想配当利回り(dividendYield)
Yahoo に値がないときは「—」を出す(赤字で PER がない、無配で配当利回りがない、など)。

登録した銘柄は、ルートディレクトリの compare.json に保存し、次回の起動時に自動で読み込む。

使い方:
    python fina_compare.py              # 前回の銘柄で起動
    python fina_compare.py 7203 7267    # 指定した銘柄を追加して起動

操作:
    銘柄の登録  「銘柄の追加」欄に銘柄コードまたは銘柄名の一部を入力すると、候補が出る。
                上下キーで選んで Enter、または候補をダブルクリックで登録する
    並べ替え    見出しをクリック(もう一度クリックで逆順)。集計の 4 行は常に最後
    銘柄の削除  行を選んで「削除」ボタン、または Delete キー(Ctrl・Shift で複数選べる)
    コピー      Ctrl+C で、選んだ行(選んでいなければ表全体)を見出し付きでコピーする。
                タブ区切りなので、表計算ソフトにそのまま貼り付けられる
    Excel 出力  「Excel 出力」ボタンで、表を画面と同じ並び順で .xlsx ファイルに保存する。
                数値は数値のセルとして入れ(時価総額は億円、配当利回りは % の表示形式)、
                一番高い値は赤い文字にする
    更新        「更新」ボタンで、すべての銘柄の指標を取り直す
    チャート    行をダブルクリック(または選んで Enter)で、その銘柄のチャート(gui_chart.py)を
                別のウィンドウで開く。株価は stock_database フォルダの保存済みデータ(core_gather.py で取得)を使う。
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
from tkinter import filedialog, messagebox, ttk

import yfinance as yf
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from gui_chart import ChartFrame
from core_gather import BASE_DIR, csv_path_for
from core_stocks import load_listed, stock_index
from gui_widgets import StockSearchBox

COMPARE_PATH = BASE_DIR / "compare.json"
FORMAT_VERSION = 1
WORKERS = 4          # 指標を同時に取得する銘柄数
POLL_INTERVAL = 100  # 取得結果を画面に反映する間隔(ミリ秒)
MISSING = "—"        # 値がないときの表示

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
    "psr": ("PSR", 84, "e"),
    "dividend_yield": ("配当利回り", 92, "e"),
}
# 集計しない値の列(列の名前 -> yfinance の info の項目名)
FIELDS = {
    "price": "currentPrice",
    "market_cap": "marketCap",
}
# 指標の列(列の名前 -> yfinance の info の項目名)。集計の 4 行を出すのはこの列
METRICS = {
    "ev_rev": "enterpriseToRevenue",
    "ev_ebitda": "enterpriseToEbitda",
    "per": "trailingPE",
    "forward_per": "forwardPE",
    "psr": "priceToSalesTrailing12Months",
    "dividend_yield": "dividendYield",  # yfinance 1.x では % の値(2.92 なら 2.92%)
}
# 集計の行(行の名前 -> 計算のしかた)
SUMMARIES = {
    "High": max,
    "Average": statistics.mean,
    "Median": statistics.median,
    "Low": min,
}
SUMMARY_PREFIX = "summary:"  # 集計の行の iid の頭(銘柄コードと区別する)

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
    "psr": ("#,##0.00", 10),
    "dividend_yield": ("0.00%", 12),
}
EXCEL_RED = "D00000"         # 一番高い値の文字の色
EXCEL_HEADER_FILL = "DDE3EA"  # 見出しの背景
EXCEL_SUMMARY_FILL = "EEF1F5"  # 集計の行の背景(画面と同じ)


def excel_value(column: str, value: float | None):
    """Excel のセルに入れる値。時価総額は億円、配当利回りは割合(2.92% なら 0.0292)にする。"""
    if value is None:
        return None
    if column == "market_cap":
        return value / 1e8
    if column == "dividend_yield":
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
    return f"{value:,.2f}"


def read_codes() -> list[str]:
    """compare.json から登録済みの銘柄コードを読み込む。ファイルがなければ空。"""
    if not COMPARE_PATH.exists():
        return []
    with open(COMPARE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    codes = data.get("codes") if isinstance(data, dict) else None
    if not isinstance(codes, list):
        raise ValueError("ファイルの形式が違います")
    return list(dict.fromkeys(c for c in codes if isinstance(c, str) and c))


def write_codes(codes: list[str]) -> None:
    """compare.json に保存する。途中で止まっても元のファイルが壊れないよう、別のファイルから置き換える。"""
    temporary = COMPARE_PATH.with_name(COMPARE_PATH.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump({"version": FORMAT_VERSION, "codes": codes}, f, ensure_ascii=False, indent=2)
    os.replace(temporary, COMPARE_PATH)


class CompareFrame(ttk.Frame):
    """銘柄の比較表と、銘柄の追加欄をまとめた部品。"""

    def __init__(self, master, codes: list[str]):
        super().__init__(master)
        self.listed = load_listed()
        self.codes = list(codes)
        self.quotes: dict[str, Quote] = {}  # 取得済みの銘柄だけが入る
        self.pending: set[str] = set()      # 取得中の銘柄
        self.queue = queue.Queue()          # 取得用のスレッドから画面への受け渡し
        self.pool = ThreadPoolExecutor(max_workers=WORKERS)
        self.sort_column, self.sort_reverse = None, False  # None は登録順
        self.chart_windows: dict[str, tk.Toplevel] = {}  # 銘柄コード -> 開いているチャートのウィンドウ

        self._build_controls()
        self._build_table()
        self.fetch(self.codes)
        self.after(POLL_INTERVAL, self._poll)

    # ---- 画面の組み立て ----

    def _build_controls(self):
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", padx=4, pady=(4, 0))
        ttk.Button(bar, text="Excel 出力", command=self.export_excel).pack(side="right", padx=(12, 0))
        ttk.Button(bar, text="削除", width=5, command=self.remove_selected).pack(side="right")
        self.refresh_button = ttk.Button(bar, text="更新", width=5, command=lambda: self.fetch(self.codes))
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

    def _build_table(self):
        frame = ttk.Frame(self)
        frame.pack(side="top", fill="both", expand=True, padx=4, pady=(6, 0))
        self.tree = ttk.Treeview(frame, columns=list(COLUMNS), show="headings", selectmode="extended")
        for column, (heading, width, anchor) in COLUMNS.items():
            self.tree.heading(column, text=heading, command=lambda c=column: self._on_heading(c))
            self.tree.column(column, width=width, anchor=anchor, stretch=column == "name")
        self.tree.tag_configure("summary", background="#eef1f5")
        self.tree.tag_configure("error", foreground="#b22222")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)

        def on_scroll(first, last):
            scrollbar.set(first, last)
            self._schedule_highlights()

        self.tree.configure(yscrollcommand=on_scroll)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        # 一番高い値のセルに重ねて出す、赤い文字の欄(Treeview はセルごとに色を変えられないため)
        self.highlights: list[tk.Label] = []
        self.highlight_job = None
        self.highlight_font = ttk.Style().lookup("Treeview", "font") or "TkDefaultFont"
        self.tree.bind("<Configure>", lambda e: self._schedule_highlights(), add="+")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._schedule_highlights(), add="+")
        self.tree.bind("<B1-Motion>", lambda e: self._schedule_highlights(), add="+")  # 列の幅の変更
        self.tree.bind("<ButtonRelease-1>", lambda e: self._schedule_highlights(), add="+")

        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<Control-c>", lambda e: self.copy())
        self.tree.bind("<Control-a>", lambda e: self.tree.selection_set(self.codes))
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Return>", lambda e: self.open_chart(self.tree.focus()))

    # ---- 銘柄の登録と削除 ----

    def add_stock(self, code: str) -> None:
        """銘柄を表に加えて指標を取得する。登録済みなら選ぶだけ。"""
        if code not in self.codes:
            self.codes.append(code)
            self.save()
            self.fetch([code])
        self._refresh_table()
        self.tree.selection_set(code)
        self.tree.see(code)

    def remove_selected(self) -> None:
        """選んでいる銘柄を表から外す(集計の行は外せない)。"""
        removed = {iid for iid in self.tree.selection() if iid in self.codes}
        if not removed:
            return
        self.codes = [c for c in self.codes if c not in removed]
        for code in removed:
            self.quotes.pop(code, None)
        self.save()
        self._refresh_table()

    def save(self) -> None:
        try:
            write_codes(self.codes)
        except OSError as e:
            messagebox.showerror("fina_compare", f"{COMPARE_PATH.name} に保存できません。\n{e}", parent=self)

    # ---- 指標の取得 ----

    def fetch(self, codes: list[str]) -> None:
        """銘柄の指標を、取得用のスレッドで取得する(取得中の銘柄は除く)。"""
        for code in codes:
            if code in self.pending:
                continue
            self.pending.add(code)
            future = self.pool.submit(fetch_quote, code)
            # 閉じるときに取り消した取得は、結果を返さない
            future.add_done_callback(lambda f, c=code: f.cancelled() or self.queue.put((c, f.result())))
        self._refresh_table()

    def _poll(self):
        """取得用のスレッドから届いた結果を、表に反映する。"""
        changed = False
        while True:
            try:
                code, quote = self.queue.get_nowait()
            except queue.Empty:
                break
            self.pending.discard(code)
            if code in self.codes:  # 取得中に削除された銘柄は捨てる
                self.quotes[code] = quote
                changed = True
        if changed:
            self._refresh_table()
        self.after(POLL_INTERVAL, self._poll)

    def stop(self) -> None:
        """取得を止める(ウィンドウを閉じるときに呼ぶ)。"""
        self.pool.shutdown(wait=False, cancel_futures=True)

    # ---- 表 ----

    def _stock_name(self, code: str) -> str:
        name = self.listed["銘柄名"].get(code)
        if isinstance(name, str):
            return name
        quote = self.quotes.get(code)
        return quote.long_name if quote else ""

    def _stock_market(self, code: str) -> str:
        market = self.listed["市場・商品区分"].get(code)
        return market.split("（")[0] if isinstance(market, str) else ""

    def _value(self, code: str, column: str) -> float | None:
        quote = self.quotes.get(code)
        if quote is None or quote.error:
            return None
        return quote.values.get(column)

    def _row(self, code: str) -> tuple[str, ...]:
        quote = self.quotes.get(code)
        columns = (*FIELDS, *METRICS)
        if quote is None:
            numbers = ["取得中…"] + [""] * (len(columns) - 1)
        elif quote.error:
            numbers = ["取得失敗"] + [""] * (len(columns) - 1)
        else:
            numbers = [format_value(c, self._value(code, c)) for c in columns]
        return (self._stock_name(code), code, self._stock_market(code), *numbers)

    def _sorted_codes(self) -> list[str]:
        column = self.sort_column
        if column is None:
            return list(self.codes)
        if column in ("name", "code", "market"):
            key = {"name": self._stock_name, "code": str, "market": self._stock_market}[column]
            return sorted(self.codes, key=key, reverse=self.sort_reverse)
        # 値がない銘柄は、昇順でも降順でも最後に置く
        with_value = [c for c in self.codes if self._value(c, column) is not None]
        without = [c for c in self.codes if self._value(c, column) is None]
        with_value.sort(key=lambda c: self._value(c, column), reverse=self.sort_reverse)
        return with_value + without

    def _summary_values(self) -> dict[str, list[float]]:
        """集計に使う値(指標の列の名前 -> 値の一覧)。

        マイナスの値(赤字の PER、EBITDA が赤字の EV/EBITDA など)は、倍率として比べられないので除く。
        """
        return {
            column: [v for c in self.codes if (v := self._value(c, column)) is not None and v >= 0]
            for column in METRICS
        }

    def _summary_rows(self) -> dict[str, tuple[str, ...]]:
        """集計の 4 行(行の名前 -> 表示する値)。"""
        values = self._summary_values()
        rows = {}
        for label, function in SUMMARIES.items():
            cells = [format_value(column, function(values[column]) if values[column] else None) for column in METRICS]
            rows[label] = (label, "", "", *[""] * len(FIELDS), *cells)
        return rows

    def _refresh_table(self):
        """並び順に合わせて、表を作り直す。"""
        selected = [iid for iid in self.tree.selection() if iid in self.codes]
        self.tree.delete(*self.tree.get_children())
        for code in self._sorted_codes():
            quote = self.quotes.get(code)
            tags = ["error"] if quote and quote.error else []
            self.tree.insert("", "end", iid=code, values=self._row(code), tags=tags)
        for label, values in self._summary_rows().items():
            self.tree.insert("", "end", iid=SUMMARY_PREFIX + label, values=values, tags=["summary"])
        if selected:
            self.tree.selection_set(selected)
        self.status_var.set(self._status())
        self._schedule_highlights()

    # ---- 一番高い値の赤い文字 ----

    def _highest_cells(self) -> list[tuple[str, str]]:
        """指標の列ごとに、一番高い値(集計の High と同じ値)の (銘柄コード, 列の名前) の一覧。同じ値は全部。"""
        cells = []
        for column, values in self._summary_values().items():
            if values:
                high = max(values)
                cells += [(code, column) for code in self.codes if self._value(code, column) == high]
        return cells

    def _schedule_highlights(self):
        """赤い文字の欄を、表の描き直しが終わってから置き直す(続けて呼ばれても 1 回にまとめる)。"""
        if self.highlight_job is None:
            self.highlight_job = self.after_idle(self._place_highlights)

    def _place_highlights(self):
        """一番高い値のセルに、同じ値を赤い文字で出す欄を重ねる。見えていないセルの欄は隠す。"""
        self.highlight_job = None
        style = ttk.Style()
        selected = set(self.tree.selection())
        cells = [(code, column) for code, column in self._highest_cells() if self.tree.exists(code)]
        while len(self.highlights) < len(cells):
            self.highlights.append(self._new_highlight())
        for label, (code, column) in zip(self.highlights, cells):
            bbox = self.tree.bbox(code, column)
            if not bbox:  # スクロールで見えていない
                label.place_forget()
                continue
            state = ["selected"] if code in selected else []
            label.code = code
            label.configure(
                text=self.tree.set(code, column),
                background=style.lookup("Treeview", "background", state) or "white",
                foreground="#ffb3b3" if code in selected else "#d00000",  # 選択中の濃い背景でも読める赤
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
        label.bind("<Double-Button-1>", lambda e: self.open_chart(label.code) or "break")
        label.code = None
        label.bind("<MouseWheel>", lambda e: self.tree.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        return label

    def _status(self) -> str:
        if not self.codes:
            return "「銘柄の追加」欄に銘柄コードまたは銘柄名を入力して、比べる銘柄を登録してください。"
        text = f"{len(self.codes)} 銘柄"
        if self.pending:
            text += f"(取得中: 残り {len(self.pending)} 銘柄)"
        failed = [c for c in self.codes if (q := self.quotes.get(c)) and q.error]
        if failed:
            reasons = "、".join(f"{c}: {self.quotes[c].error}" for c in failed[:3])
            text += f"\n取得に失敗した銘柄があります({reasons}{' ほか' if len(failed) > 3 else ''})。「更新」で取り直せます"
        return text

    def _on_heading(self, column: str):
        """見出しのクリックで並べ替える。同じ見出しをもう一度クリックすると逆順。"""
        self.sort_reverse = column == self.sort_column and not self.sort_reverse
        self.sort_column = column
        self._refresh_table()

    # ---- チャート ----

    def _on_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell":  # 見出しのダブルクリックは並べ替えだけ
            self.open_chart(self.tree.identify_row(event.y))

    def open_chart(self, code: str) -> None:
        """銘柄のチャート(gui_chart.py の ChartFrame)を別のウィンドウで開く。開いていれば前に出す。"""
        if code not in self.codes:  # 集計の行など
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
                f"{f'{code} {self._stock_name(code)}'.strip()} の株価データがありません。"
                f"先に python core_gather.py {code} を実行してください。",
            )
            return
        window = tk.Toplevel(self)
        window.title(f"ToreCan chart - {code} {self._stock_name(code)}")
        window.geometry("1200x820")
        ChartFrame(window, code).pack(fill="both", expand=True)
        window.bind("<Destroy>", lambda e: e.widget is window and self.chart_windows.pop(code, None))
        self.chart_windows[code] = window

    # ---- Excel 出力 ----

    def export_excel(self) -> None:
        """表を、画面と同じ並び順で Excel ファイル(.xlsx)に保存する。"""
        if not self.codes:
            self.status_var.set("出力する銘柄がありません。")
            return
        if self.pending and not messagebox.askyesno(
            "fina_compare", f"まだ取得中の銘柄が {len(self.pending)} 銘柄あります。このまま出力しますか?", parent=self,
        ):
            return
        now = datetime.now()
        path = filedialog.asksaveasfilename(
            parent=self, title="Excel に出力",
            initialdir=BASE_DIR, initialfile=f"compare_{now:%Y%m%d}.xlsx",
            defaultextension=".xlsx", filetypes=[("Excel ブック", "*.xlsx")],
        )
        if not path:
            return
        try:
            self.write_excel(Path(path), now)
        except OSError as e:  # Excel で開いたままのファイルに上書きしようとした、など
            messagebox.showerror("fina_compare", f"保存できません。\n{e}", parent=self)
            return
        self.status_var.set(f"{Path(path).name} に出力しました。")

    def write_excel(self, path: Path, now: datetime) -> None:
        """表を Excel ファイルに書く。数値はセルに数値として入れ、一番高い値は赤い文字にする。"""
        book = Workbook()
        sheet = book.active
        sheet.title = "比較"
        columns = list(COLUMNS)
        thin = Side(style="thin", color="B0B7C0")
        border = Border(top=thin, bottom=thin, left=thin, right=thin)

        for i, column in enumerate(columns, start=1):
            cell = sheet.cell(row=1, column=i, value=COLUMNS[column][0])
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor=EXCEL_HEADER_FILL)
            cell.alignment = Alignment(horizontal="center")
            cell.border = border
            sheet.column_dimensions[get_column_letter(i)].width = EXCEL_FORMATS[column][1]

        highest = set(self._highest_cells())
        codes = [iid for iid in self.tree.get_children() if iid in self.codes]  # 画面の並び順
        row = 2
        for code in codes:
            quote = self.quotes.get(code)
            texts = {"name": self._stock_name(code), "code": code, "market": self._stock_market(code)}
            for i, column in enumerate(columns, start=1):
                if column in texts:
                    value = texts[column]
                elif quote is None or quote.error:
                    value = ("取得中" if quote is None else "取得失敗") if column == "price" else None
                else:
                    value = excel_value(column, self._value(code, column))
                cell = sheet.cell(row=row, column=i, value=value)
                cell.border = border
                if EXCEL_FORMATS[column][0]:
                    cell.number_format = EXCEL_FORMATS[column][0]
                if column == "code" or column == "market":
                    cell.alignment = Alignment(horizontal="center")
                if (code, column) in highest:
                    cell.font = Font(color=EXCEL_RED)
            row += 1

        values = self._summary_values()
        for label, function in SUMMARIES.items():
            for i, column in enumerate(columns, start=1):
                if column == "name":
                    value = label
                elif column in METRICS and values[column]:
                    value = excel_value(column, function(values[column]))
                else:
                    value = None
                cell = sheet.cell(row=row, column=i, value=value)
                cell.fill = PatternFill("solid", fgColor=EXCEL_SUMMARY_FILL)
                cell.border = border
                if column == "name":
                    cell.font = Font(bold=True)
                elif EXCEL_FORMATS[column][0]:
                    cell.number_format = EXCEL_FORMATS[column][0]
            row += 1

        sheet.cell(row=row + 1, column=1, value=(
            f"出力: {now:%Y-%m-%d %H:%M}(指標は yfinance から取得)。"
            "集計はマイナスの値を除いて計算。赤い文字は列で一番高い値"
        )).font = Font(color="666666", size=9)
        sheet.freeze_panes = "B2"  # 見出しの行と銘柄名の列を固定する
        book.save(path)

    def copy(self) -> str:
        """選んだ行(選んでいなければ表全体)を、見出し付きのタブ区切りでクリップボードに入れる。"""
        rows = self.tree.selection() or self.tree.get_children()
        lines = ["\t".join(heading for heading, _, _ in COLUMNS.values())]
        lines += ["\t".join(str(v) for v in self.tree.item(iid, "values")) for iid in rows]
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.status_var.set(f"{len(rows)} 行をコピーしました。")
        return "break"


def main() -> int:
    root = tk.Tk()
    root.title("ToreCan compare")
    root.geometry("1100x600")
    try:
        codes = read_codes()
    except (OSError, ValueError) as e:
        messagebox.showerror("fina_compare", f"{COMPARE_PATH.name} を読み込めません。\n{e}", parent=root)
        return 1
    added = [code.upper().removesuffix(".T") for code in sys.argv[1:]]
    codes += [c for c in dict.fromkeys(added) if c not in codes]
    try:
        frame = CompareFrame(root, codes)
    except FileNotFoundError as e:
        messagebox.showerror("fina_compare", str(e), parent=root)
        return 1
    frame.pack(fill="both", expand=True)
    if added:
        frame.save()

    def on_close():
        frame.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
