"""相関マトリクスの描画部品。銘柄 × 銘柄の相関係数を、色分けしたマス目で示す。

色は 0 を無色(白)にし、+1 に近いほど濃い青、-1 に近いほど濃い赤にする(片側 4 段)。
各マスには相関係数と、その下に計算に使った日数を出す。
日数が期間の日数より少ない(重なる期間が短い)マスは、日数を目立つ色にする。
右端に、色分けしない補助の列(ベータなど)を 1 つ付けられる。

使い方(他のプログラムから):
    matrix = CorrMatrix(parent, on_open=lambda a, b: print(a, b))
    matrix.pack(fill="both", expand=True)
    matrix.set_data(
        [MatrixLabel("1306", "★1306", "TOPIX"), MatrixLabel("7203", "7203", "トヨタ自動車")],
        {("1306", "7203"): Correlation(0.62, 250), ("7203", "1306"): Correlation(0.62, 250)},
        window=250, extra_title="β", extra={"7203": Correlation(0.85, 250)},
    )
"""

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk

from core_compare import Correlation
from gui_treemap import text_color

# 色(dataviz の分岐配色: + は青、- は赤、0 は無色)
POSITIVE_STEPS = ("#cde2fb", "#86b6ef", "#3987e5", "#1c5cab")
NEGATIVE_STEPS = ("#fbdcdc", "#f2a7a6", "#e34948", "#b3201f")
NEUTRAL = "#ffffff"
DIAGONAL = "#e6e5e1"
UNAVAILABLE = "#f4f3f0"
GRID = "#d9d8d4"
HEADER_FILL = "#f4f3f0"
TEXT = "#1b1b1a"
TEXT_MUTED = "#6b6a66"
SHORT_DAYS = "#b25e00"   # 期間の日数より少ない日数の文字の色
STEP_EDGES = (0.2, 0.4, 0.6, 0.8)  # |r| がこの値以上で 1 段ずつ濃くなる(0.2 未満は無色)

CELL_WIDTH = 76
CELL_HEIGHT = 42
ROW_HEADER = 170   # 行の見出し(左)の幅
COLUMN_HEADER = 44  # 列の見出し(上)の高さ
EXTRA_WIDTH = 76   # 補助の列の幅
UNAVAILABLE_TEXT = "算出不能"


@dataclass(frozen=True)
class MatrixLabel:
    """マトリクスの行・列 1 つ。"""
    key: str         # 銘柄コードなど(cells のキー)
    code: str        # 見出しの 1 行目
    name: str = ""   # 見出しの 2 行目(銘柄名)


def correlation_color(value: float | None) -> str:
    """相関係数の塗りの色。"""
    if value is None:
        return UNAVAILABLE
    level = sum(abs(value) >= edge for edge in STEP_EDGES)
    if level == 0:
        return NEUTRAL
    return (POSITIVE_STEPS if value > 0 else NEGATIVE_STEPS)[level - 1]


class CorrMatrix(ttk.Frame):
    """相関マトリクス(スクロールできる Canvas)。セルのダブルクリックで on_open(行のキー, 列のキー) を呼ぶ。"""

    def __init__(self, master, on_open: Callable[[str, str], None] | None = None):
        super().__init__(master)
        self.on_open = on_open
        self.canvas = tk.Canvas(self, background="#ffffff", highlightthickness=0)
        x_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        y_scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        y_scroll.pack(side="right", fill="y")
        x_scroll.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.labels: list[MatrixLabel] = []
        self.cells: dict[tuple[str, str], Correlation] = {}
        self.extra: dict[str, Correlation] = {}
        self.window: int | None = None
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("tooltip"))
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    def set_data(self, labels: list[MatrixLabel], cells: dict[tuple[str, str], Correlation], window: int | None,
                 extra_title: str = "", extra: dict[str, Correlation] | None = None) -> None:
        """表示する内容を入れ替える。window は期間の日数(None は全期間)。extra は右端の補助の列。"""
        self.labels, self.cells, self.window = labels, cells, window
        self.extra_title, self.extra = extra_title, extra or {}
        self._draw()

    # ---- 描画 ----

    def _draw(self):
        canvas = self.canvas
        canvas.delete("all")
        if not self.labels:
            canvas.create_text(20, 20, text="銘柄を登録すると、相関を表示します", anchor="nw", fill=TEXT_MUTED)
            canvas.configure(scrollregion=(0, 0, 400, 100))
            return
        n = len(self.labels)
        for j, label in enumerate(self.labels):  # 列の見出し
            x = ROW_HEADER + j * CELL_WIDTH
            canvas.create_rectangle(x, 0, x + CELL_WIDTH, COLUMN_HEADER, fill=HEADER_FILL, outline=GRID)
            canvas.create_text(x + CELL_WIDTH / 2, 14, text=label.code, font=("", 9, "bold"), fill=TEXT)
            canvas.create_text(x + CELL_WIDTH / 2, 30, text=self._fit(label.name, 7), font=("", 8), fill=TEXT_MUTED)
        for i, row in enumerate(self.labels):
            y = COLUMN_HEADER + i * CELL_HEIGHT
            canvas.create_rectangle(0, y, ROW_HEADER, y + CELL_HEIGHT, fill=HEADER_FILL, outline=GRID)
            canvas.create_text(8, y + CELL_HEIGHT / 2, text=f"{row.code}  {self._fit(row.name, 9)}", anchor="w",
                               font=("", 9), fill=TEXT)
            for j, column in enumerate(self.labels):
                x = ROW_HEADER + j * CELL_WIDTH
                if i == j:
                    self._cell(x, y, DIAGONAL, "1.00", "")
                    continue
                value = self.cells.get((row.key, column.key), Correlation(None, 0))
                self._value_cell(x, y, value, correlation_color(value.value))
        if self.extra_title:
            x = ROW_HEADER + n * CELL_WIDTH + 8
            canvas.create_rectangle(x, 0, x + EXTRA_WIDTH, COLUMN_HEADER, fill=HEADER_FILL, outline=GRID)
            canvas.create_text(x + EXTRA_WIDTH / 2, COLUMN_HEADER / 2, text=self.extra_title, font=("", 9, "bold"),
                               fill=TEXT)
            for i, row in enumerate(self.labels):
                y = COLUMN_HEADER + i * CELL_HEIGHT
                value = self.extra.get(row.key)
                if value is None:
                    self._cell(x, y, "#ffffff", "—", "", width=EXTRA_WIDTH)
                else:
                    self._value_cell(x, y, value, "#ffffff", width=EXTRA_WIDTH)
        width = ROW_HEADER + n * CELL_WIDTH + (EXTRA_WIDTH + 16 if self.extra_title else 8)
        canvas.configure(scrollregion=(0, 0, width, COLUMN_HEADER + n * CELL_HEIGHT + 8))

    def _value_cell(self, x, y, value: Correlation, fill: str, width: int = CELL_WIDTH):
        days = f"{value.days}日" if value.days else ""
        short = self.window is not None and value.days < self.window
        if value.value is None:
            self._cell(x, y, UNAVAILABLE, UNAVAILABLE_TEXT, days, short, width, muted=True)
        else:
            self._cell(x, y, fill, f"{value.value:.2f}", days, short, width)

    def _cell(self, x, y, fill, text, days, short=False, width=CELL_WIDTH, muted=False):
        canvas = self.canvas
        canvas.create_rectangle(x, y, x + width, y + CELL_HEIGHT, fill=fill, outline=GRID)
        color = TEXT_MUTED if muted else text_color(fill)
        canvas.create_text(x + width / 2, y + 15, text=text, font=("", 10, "bold") if not muted else ("", 9),
                           fill=color)
        if days:
            days_color = SHORT_DAYS if short and color != "#ffffff" else color
            canvas.create_text(x + width / 2, y + 31, text=days, font=("", 8), fill=days_color)

    @staticmethod
    def _fit(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[: limit - 1] + "…"

    # ---- マウス ----

    def _cell_at(self, event) -> tuple[MatrixLabel, MatrixLabel] | None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        i, j = int((y - COLUMN_HEADER) // CELL_HEIGHT), int((x - ROW_HEADER) // CELL_WIDTH)
        if y < COLUMN_HEADER or x < ROW_HEADER or not (0 <= i < len(self.labels) and 0 <= j < len(self.labels)):
            return None
        return self.labels[i], self.labels[j]

    def _on_motion(self, event):
        self.canvas.delete("tooltip")
        cell = self._cell_at(event)
        if cell is None or cell[0] is cell[1]:
            return
        row, column = cell
        value = self.cells.get((row.key, column.key), Correlation(None, 0))
        result = UNAVAILABLE_TEXT if value.value is None else f"{value.value:+.3f}"
        text = (f"{row.code} {row.name}\n{column.code} {column.name}\n"
                f"相関 {result}(計算に使った日数 {value.days} 日)")
        x, y = self.canvas.canvasx(event.x) + 14, self.canvas.canvasy(event.y) + 14
        label = self.canvas.create_text(x + 6, y + 4, text=text, anchor="nw", font=("", 9), fill=TEXT, tags="tooltip")
        x0, y0, x1, y1 = self.canvas.bbox(label)
        box = self.canvas.create_rectangle(x0 - 6, y0 - 4, x1 + 6, y1 + 4, fill="#ffffff", outline="#8a8984",
                                           tags="tooltip")
        self.canvas.tag_raise(label, box)

    def _on_double_click(self, event):
        cell = self._cell_at(event)
        if cell is not None and self.on_open is not None:
            self.on_open(cell[0].key, cell[1].key)
