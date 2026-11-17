"""複数のプログラムで使い回す tkinter の部品。

StockSearchBox
    銘柄の検索欄。銘柄コードまたは銘柄名の一部を入力すると、入力欄のすぐ下に候補が出る。
    上下キーで選んで Enter、候補をダブルクリック、またはボタンで、選んだ銘柄を on_pick に渡す。
    空白で区切ると、すべての語を含む銘柄に絞り込む。
    全角・半角、大文字・小文字、ひらがな・カタカナは区別しない(検索の処理は fina_stocks.py)。

使い方(他のプログラムから):
    index = stock_index(load_listed())
    box = StockSearchBox(parent, index, on_pick=lambda stock: print(stock.code))
    box.pack(side="top", fill="x")
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from fina_stocks import MAX_CANDIDATES, Stock, search_stocks

CANDIDATE_ROWS = 8  # 候補の欄の高さ(行数)


class StockSearchBox(ttk.Frame):
    """銘柄の検索欄(見出し・入力欄・ボタン)と、その下に出る候補の欄をまとめた部品。

    on_pick     候補を決めたときに、選んだ銘柄(Stock)を渡して呼ぶ。入力欄は空にしてから呼ぶ
    on_message  利用者への案内(「該当する銘柄がありません。」など)を渡して呼ぶ。
                案内を消すときは空文字を渡す。省略したときは案内を出さない
    """

    def __init__(
        self,
        master,
        index: list[Stock],
        on_pick: Callable[[Stock], None],
        on_message: Callable[[str], None] | None = None,
        label: str = "銘柄",
        button: str = "決定",
        rows: int = CANDIDATE_ROWS,
        limit: int = MAX_CANDIDATES,
    ):
        super().__init__(master)
        self.index = index
        self.on_pick = on_pick
        self.on_message = on_message or (lambda text: None)
        self.limit = limit
        self.candidates: list[Stock] = []  # 候補の欄に出している銘柄

        self.bar = ttk.Frame(self)
        self.bar.pack(side="top", fill="x", padx=(0, 4))
        ttk.Label(self.bar, text=label).pack(side="left")
        self.query_var = tk.StringVar()
        self.entry = ttk.Entry(self.bar, textvariable=self.query_var)
        self.entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        ttk.Button(self.bar, text=button, width=len(button) * 2 + 1, command=self.pick).pack(side="left")
        self.query_var.trace_add("write", lambda *_: self._update_candidates())
        self.entry.bind("<Return>", lambda e: self.pick())
        self.entry.bind("<Escape>", lambda e: self.clear())
        self.entry.bind("<Down>", lambda e: self._move_candidate(1))
        self.entry.bind("<Up>", lambda e: self._move_candidate(-1))

        # 候補の欄。入力があるときだけ、入力欄のすぐ下に出す
        self.candidate_frame = ttk.Frame(self)
        self.candidate_list = tk.Listbox(
            self.candidate_frame, height=rows, exportselection=False, activestyle="none",
        )
        scrollbar = ttk.Scrollbar(self.candidate_frame, orient="vertical", command=self.candidate_list.yview)
        self.candidate_list.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.candidate_list.pack(side="left", fill="both", expand=True)
        self.candidate_list.bind("<Double-Button-1>", lambda e: self.pick())
        self.candidate_list.bind("<Return>", lambda e: self.pick())

    def focus_set(self) -> None:
        """入力欄にカーソルを置く。"""
        self.entry.focus_set()

    def clear(self) -> None:
        """入力欄を空にする(候補の欄も隠れる)。"""
        self.query_var.set("")

    def pick(self) -> None:
        """候補の欄で選んでいる銘柄(選んでいなければ先頭の銘柄)を on_pick に渡す。"""
        if not self.candidates:
            return
        current = self.candidate_list.curselection()
        stock = self.candidates[current[0] if current else 0]
        self.clear()  # 先に欄を空にする(後にすると、on_pick が出した案内が消える)
        self.on_pick(stock)
        self.focus_set()

    def _update_candidates(self):
        """入力に合う銘柄を候補の欄に出す。入力がなければ欄を隠す。"""
        query = self.query_var.get()
        self.candidates = search_stocks(self.index, query, self.limit)
        self.candidate_list.delete(0, "end")
        for stock in self.candidates:
            self.candidate_list.insert("end", f"{stock.code}  {stock.name}  ({stock.market})")
        if self.candidates:
            self.candidate_list.selection_set(0)
            self.candidate_frame.pack(side="top", fill="x", pady=(2, 0))
            self.on_message("")
        else:
            self.candidate_frame.pack_forget()
            self.on_message("該当する銘柄がありません。" if query.strip() else "")

    def _move_candidate(self, step: int):
        """候補の選択を上下に動かす(入力欄にカーソルを置いたまま選べるようにする)。"""
        if not self.candidates:
            return "break"
        current = self.candidate_list.curselection()
        i = min(max((current[0] if current else -1) + step, 0), len(self.candidates) - 1)
        self.candidate_list.selection_clear(0, "end")
        self.candidate_list.selection_set(i)
        self.candidate_list.see(i)
        return "break"
