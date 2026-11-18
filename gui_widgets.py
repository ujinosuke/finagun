"""複数のプログラムで使い回す tkinter の部品。

StockSearchBox
    銘柄の検索欄。銘柄コードまたは銘柄名の一部を入力すると、入力欄のすぐ下に候補が出る。
    上下キーで選んで Enter、候補をダブルクリック、またはボタンで、選んだ銘柄を on_pick に渡す。
    空白で区切ると、すべての語を含む銘柄に絞り込む。
    全角・半角、大文字・小文字、ひらがな・カタカナは区別しない(検索の処理は core_stocks.py)。

SortableTree
    見出しをクリックして並べ替えのできる一覧(ttk.Treeview とスクロールバー)。

add_to_watchlist
    銘柄をウォッチリスト(watchlist.json)のグループに足す。グループを尋ねる小さな画面を出し、結果を知らせる。

使い方(他のプログラムから):
    index = stock_index(load_listed())
    box = StockSearchBox(parent, index, on_pick=lambda stock: print(stock.code))
    box.pack(side="top", fill="x")
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from core_stocks import MAX_CANDIDATES, Stock, search_stocks
from core_watchlist import WATCHLIST_PATH, add_codes, group_names

CANDIDATE_ROWS = 8   # 候補の欄の高さ(行数)
DROPDOWN_WIDTH = 44  # 重ねて出す候補の欄の幅(文字数)
PROBLEM_BACKGROUND = "#fff3cd"  # SortableTree で、問題のある行(タグ problem)の背景


class StockSearchBox(ttk.Frame):
    """銘柄の検索欄(見出し・入力欄・ボタン)と、その下に出る候補の欄をまとめた部品。

    on_pick     候補を決めたときに、選んだ銘柄(Stock)を渡して呼ぶ。入力欄は空にしてから呼ぶ
    on_message  利用者への案内(「該当する銘柄がありません。」など)を渡して呼ぶ。
                案内を消すときは空文字を渡す。省略したときは案内を出さない
    on_unmatched 候補がないまま Enter・ボタンを押したときに、入力した文字を渡して呼ぶ
                (銘柄一覧にないコードを直接指定したいときに使う)。省略したときは何もしない
    dropdown    True なら、候補の欄を入力欄の下に重ねて出す(ツールバーの中など、
                部品の高さを変えたくないところで使う)。False なら、部品の中に入れて下の部品を押し下げる
    entry_width 入力欄の幅(文字数)。None なら、部品の幅いっぱいに広げる
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
        on_unmatched: Callable[[str], None] | None = None,
        dropdown: bool = False,
        entry_width: int | None = None,
    ):
        super().__init__(master)
        self.index = index
        self.on_pick = on_pick
        self.on_message = on_message or (lambda text: None)
        self.on_unmatched = on_unmatched
        self.limit = limit
        self.dropdown = dropdown
        self.candidates: list[Stock] = []  # 候補の欄に出している銘柄

        self.bar = ttk.Frame(self)
        self.bar.pack(side="top", fill="x", padx=(0, 4))
        ttk.Label(self.bar, text=label).pack(side="left")
        self.query_var = tk.StringVar()
        if entry_width is None:
            self.entry = ttk.Entry(self.bar, textvariable=self.query_var)
            self.entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        else:
            self.entry = ttk.Entry(self.bar, textvariable=self.query_var, width=entry_width)
            self.entry.pack(side="left", padx=(4, 4))
        ttk.Button(self.bar, text=button, width=len(button) * 2 + 1, command=self.pick).pack(side="left")
        self.query_var.trace_add("write", lambda *_: self._update_candidates())
        self.entry.bind("<Return>", lambda e: self.pick())
        self.entry.bind("<Escape>", lambda e: self.clear())
        self.entry.bind("<Down>", lambda e: self._move_candidate(1))
        self.entry.bind("<Up>", lambda e: self._move_candidate(-1))

        # 候補の欄。入力があるときだけ、入力欄のすぐ下に出す。
        # dropdown のときは、ウィンドウの直下の部品にして、他の部品(チャートなど)の上に重ねる
        parent = self.winfo_toplevel() if dropdown else self
        self.candidate_frame = ttk.Frame(parent, relief="solid", borderwidth=1) if dropdown else ttk.Frame(parent)
        self.candidate_list = tk.Listbox(
            self.candidate_frame, height=rows, exportselection=False, activestyle="none",
            width=DROPDOWN_WIDTH if dropdown else 20,
        )
        scrollbar = ttk.Scrollbar(self.candidate_frame, orient="vertical", command=self.candidate_list.yview)
        self.candidate_list.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.candidate_list.pack(side="left", fill="both", expand=True)
        self.candidate_list.bind("<Double-Button-1>", lambda e: self.pick())
        self.candidate_list.bind("<Return>", lambda e: self.pick())
        if dropdown:
            # 入力欄から他の所へ移ったら、重ねた候補の欄を隠す(候補をクリックしたときは隠さない)
            self.entry.bind("<FocusOut>", lambda e: self.after(150, self._hide_if_unfocused), add="+")
            self.entry.bind("<FocusIn>", lambda e: self._update_candidates(), add="+")
            self.bind("<Destroy>", lambda e: e.widget is self and self.candidate_frame.destroy(), add="+")

    def focus_set(self) -> None:
        """入力欄にカーソルを置く。"""
        self.entry.focus_set()

    def clear(self) -> None:
        """入力欄を空にする(候補の欄も隠れる)。"""
        self.query_var.set("")

    def pick(self) -> None:
        """候補の欄で選んでいる銘柄(選んでいなければ先頭の銘柄)を on_pick に渡す。

        候補がなく、on_unmatched があれば、入力した文字を on_unmatched に渡す。
        """
        if not self.candidates:
            query = self.query_var.get().strip()
            if query and self.on_unmatched:
                self.clear()
                self.on_unmatched(query)
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
            self._show_candidates()
            self.on_message("")
        else:
            self._hide_candidates()
            self.on_message("該当する銘柄がありません。" if query.strip() else "")

    def _show_candidates(self):
        if self.dropdown:
            # 入力欄の左下に合わせて重ね、一番手前に出す
            self.candidate_frame.place(in_=self.entry, relx=0, rely=1, x=0, y=2, anchor="nw")
            self.candidate_frame.lift()
        else:
            self.candidate_frame.pack(side="top", fill="x", pady=(2, 0))

    def _hide_candidates(self):
        if self.dropdown:
            self.candidate_frame.place_forget()
        else:
            self.candidate_frame.pack_forget()

    def _hide_if_unfocused(self):
        """入力欄にも候補の欄にもカーソルがなければ、重ねた候補の欄を隠す。"""
        if not self.winfo_exists():
            return
        try:
            focused = self.focus_get()
        except KeyError:  # 他のウィンドウ(ダイアログなど)にカーソルがある
            focused = None
        if focused not in (self.entry, self.candidate_list):
            self._hide_candidates()

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


def ask_watchlist_group(parent, count: int) -> str | None:
    """銘柄を足すウォッチリストのグループを尋ねる。新しい名前を入力すると、そのグループを作る。取り消しなら None。"""
    window = tk.Toplevel(parent)
    window.title("ウォッチリストに追加")
    window.transient(parent.winfo_toplevel())
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=f"{count} 銘柄を追加するグループ(新しい名前を入力すると作ります)").pack(side="top", anchor="w")
    names = group_names()
    name_var = tk.StringVar(value=names[0])
    box = ttk.Combobox(frame, textvariable=name_var, values=names, width=30)
    box.pack(side="top", fill="x", pady=(6, 10))
    result: list[str] = []

    def accept(_event=None):
        name = name_var.get().strip()
        if name:
            result.append(name)
            window.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack(side="top", anchor="e")
    ttk.Button(buttons, text="追加", command=accept).pack(side="left")
    ttk.Button(buttons, text="取消", command=window.destroy).pack(side="left", padx=(4, 0))
    box.bind("<Return>", accept)
    window.bind("<Escape>", lambda e: window.destroy())
    box.focus_set()
    window.grab_set()
    window.wait_window()
    return result[0] if result else None


def add_to_watchlist(parent, codes: list[str], app_name: str) -> str:
    """銘柄をウォッチリストのグループに足す(グループは尋ねる)。結果の案内を返す(取り消したときは空文字)。"""
    if not codes:
        return "追加する銘柄を選んでください。"
    group = ask_watchlist_group(parent, len(codes))
    if group is None:
        return ""
    try:
        added = add_codes(group, codes)
    except (OSError, ValueError) as e:
        messagebox.showerror(app_name, f"{WATCHLIST_PATH.name} に保存できません。\n{e}", parent=parent)
        return ""
    skipped = len(set(codes)) - added
    return f"ウォッチリストの「{group}」に {added} 銘柄を追加しました" + (f"(登録済み {skipped} 銘柄)。" if skipped else "。")


class SortableTree(ttk.Frame):
    """見出しをクリックして並べ替えのできる一覧(ttk.Treeview とスクロールバー)。

    行は (iid, 表示する値, 並べ替えに使う値, タグ) で渡す。並べ替えに使う値が None の行は、常に最後に置く。
    """

    def __init__(self, master, columns: dict, stretch: str, height: int = 10):
        super().__init__(master)
        self.columns = list(columns)
        self.rows: list[tuple] = []
        self.sort_column: str | None = None
        self.sort_reverse = False
        self.tree = ttk.Treeview(self, columns=self.columns, show="headings", selectmode="browse", height=height)
        for column, (heading, width, anchor) in columns.items():
            self.tree.heading(column, text=heading, command=lambda c=column: self._on_heading(c))
            self.tree.column(column, width=width, anchor=anchor, stretch=column == stretch)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

    def set_rows(self, rows: list[tuple]) -> None:
        """行を入れ替える。選んでいた行が残っていれば、選んだままにする。"""
        selected = self.selected()
        self.rows = rows
        self._render()
        if selected is not None and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.see(selected)

    def selected(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def set_colors(self, plus: str, minus: str) -> None:
        """損益の色(タグ plus・minus の文字の色)と、問題のある行(タグ problem)の背景を決める。"""
        self.tree.tag_configure("plus", foreground=plus)
        self.tree.tag_configure("minus", foreground=minus)
        self.tree.tag_configure("problem", background=PROBLEM_BACKGROUND)

    def _on_heading(self, column: str):
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column, self.sort_reverse = column, False
        self._render()

    def _render(self):
        rows = self.rows
        if self.sort_column is not None:
            i = self.columns.index(self.sort_column)
            known = [r for r in rows if r[2][i] is not None]
            unknown = [r for r in rows if r[2][i] is None]
            rows = sorted(known, key=lambda r: r[2][i], reverse=self.sort_reverse) + unknown
        for column in self.columns:
            heading = self.tree.heading(column, "text").rstrip(" ▲▼")
            mark = (" ▼" if self.sort_reverse else " ▲") if column == self.sort_column else ""
            self.tree.heading(column, text=heading + mark)
        self.tree.delete(*self.tree.get_children())
        for iid, values, _keys, tags in rows:
            self.tree.insert("", "end", iid=iid, values=values, tags=tags)
