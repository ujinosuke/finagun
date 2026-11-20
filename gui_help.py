"""投資用語のヘルプ。マウスを乗せると短い説明の吹き出しを出し、右クリックで Google の検索を開く。

仕様は help-spec.md。説明の文は core_glossary.py の GLOSSARY にまとめてあり、ここは画面の部品だけを持つ。

    attach(widget, "per")                       ボタン・チェック・ラベルなどに付ける
    attach(combo, lambda: 用語のキー or None)   選んでいる項目で用語が変わるもの(コンボボックスなど)
    attach_headings(tree, {列: 用語のキー})      Treeview の列の見出しに付ける
    open_search("per")                          Google の検索をブラウザで開く

- 吹き出しは、マウスを乗せて HOVER_DELAY_MS ミリ秒たつと出る。離す・クリック・スクロールで消える。
  画面全体で 1 つだけ出す。全体設定(settings.json)の help_tooltips が False なら出さない。
- 右クリックは、attach した部品では用語だけのメニュー(「〇〇」を Google で調べる)。
  Treeview の見出し・セルの右クリックは gui_menu.bind_tree が受け持ち、term_at で用語を調べてメニューの下の段に足す。
- 検索で送るのは用語の言葉だけ(銘柄・保有の情報は送らない)。
"""

import re
import tkinter as tk
import webbrowser
from collections.abc import Callable
from tkinter import ttk
from urllib.parse import quote

from core_glossary import GLOSSARY, search_query
from core_settings import current_settings
from gui_theme import BORDER, CARD, PAD, TEXT, TEXT_MUTED, fonts

HOVER_DELAY_MS = 500         # マウスを乗せてから吹き出しを出すまで
WRAP = 330                   # 吹き出しの文の折り返しの幅(ピクセル)
HELP_CURSOR = "question_arrow"  # 説明のある所に乗せたときのマウスの形
SEARCH_URL = "https://www.google.com/search?q="

TermSource = str | Callable[[], str | None]

_heading_terms: dict[str, dict[str, str]] = {}  # Treeview(の名前) -> {列: 用語のキー}


def _for_wrap(text: str) -> str:
    """日本語のとなりの半角の空白を除く(Tk は空白で折り返すので、「株価が 1」で行が切れてしまうため)。"""
    return re.sub(r"(?<=[^\x00-\x7f]) | (?=[^\x00-\x7f])", "", text)


def tooltips_enabled() -> bool:
    """吹き出しを出すか(全体設定。起動したときの設定を使う)。"""
    return current_settings().help_tooltips


def _resolve(term: TermSource) -> str | None:
    key = term() if callable(term) else term
    return key if key in GLOSSARY else None


# ---- 吹き出し ----

class _Tooltip:
    """画面全体で 1 つの吹き出し。"""

    def __init__(self):
        self.window: tk.Toplevel | None = None
        self.job = None
        self.owner: tk.Misc | None = None
        self.key: str | None = None

    def schedule(self, widget: tk.Misc, key: str) -> None:
        """少し待ってから出す。同じ所・同じ用語なら、出したまま(待っている間なら待ったまま)にする。"""
        if self.owner is widget and self.key == key and (self.job or self.window):
            return
        self.hide()
        if not tooltips_enabled():
            return
        self.owner, self.key = widget, key
        self.job = widget.after(HOVER_DELAY_MS, self._show)

    def hide(self, _event=None) -> None:
        if self.job is not None and self.owner is not None:
            try:
                self.owner.after_cancel(self.job)
            except tk.TclError:
                pass
        self.job = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
        self.window = None
        self.owner = self.key = None

    def _show(self) -> None:
        self.job = None
        widget, term = self.owner, GLOSSARY.get(self.key or "")
        if widget is None or term is None or not widget.winfo_exists():
            return
        f = fonts(widget)
        window = tk.Toplevel(widget)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        box = tk.Frame(window, background=CARD, highlightthickness=1, highlightbackground=BORDER,
                       padx=PAD + 2, pady=PAD)
        box.pack(fill="both", expand=True)
        head = tk.Frame(box, background=CARD)
        head.pack(side="top", anchor="w")
        tk.Label(head, text=term.title, font=f["body_bold"], foreground=TEXT, background=CARD).pack(side="left")
        if term.formal:
            tk.Label(head, text=f"  {term.formal}", font=f["small"], foreground=TEXT_MUTED,
                     background=CARD).pack(side="left")
        tk.Label(box, text=_for_wrap(term.summary), font=f["body"], foreground=TEXT, background=CARD, wraplength=WRAP,
                 justify="left").pack(side="top", anchor="w", pady=(4, 0))
        if term.in_app:
            tk.Label(box, text=_for_wrap(f"このアプリでは: {term.in_app}"), font=f["small"], foreground=TEXT_MUTED,
                     background=CARD, wraplength=WRAP, justify="left").pack(side="top", anchor="w", pady=(4, 0))
        tk.Frame(box, background=BORDER, height=1).pack(side="top", fill="x", pady=(PAD - 2, 4))
        tk.Label(box, text="右クリック →「Google で調べる」", font=f["small"], foreground=TEXT_MUTED,
                 background=CARD).pack(side="top", anchor="w")
        window.update_idletasks()
        # マウスの右下に出す。画面からはみ出すときは、内側に寄せる
        x, y = widget.winfo_pointerx() + 16, widget.winfo_pointery() + 18
        width, height = window.winfo_reqwidth(), window.winfo_reqheight()
        screen_w, screen_h = widget.winfo_screenwidth(), widget.winfo_screenheight()
        if x + width > screen_w:
            x = max(0, screen_w - width - 4)
        if y + height > screen_h:
            y = max(0, widget.winfo_pointery() - height - 8)
        window.geometry(f"+{x}+{y}")
        self.window = window


_TOOLTIP = _Tooltip()


def hide_tooltip() -> None:
    _TOOLTIP.hide()


# ---- 検索・右クリック ----

def open_search(key: str) -> None:
    """Google の検索をブラウザで開く(送るのは用語の言葉だけ)。"""
    webbrowser.open(SEARCH_URL + quote(search_query(key)))


def search_label(key: str) -> str:
    return f"「{GLOSSARY[key].title}」を Google で調べる"


def add_term_items(menu: tk.Menu, key: str | None) -> None:
    """メニューに用語の段(区切り線と「Google で調べる」)を足す。用語がなければ何もしない。"""
    if key not in GLOSSARY:
        return
    if menu.index("end") is not None:
        menu.add_separator()
    menu.add_command(label=search_label(key), command=lambda: open_search(key))


def popup_term_menu(widget: tk.Misc, event, key: str | None) -> None:
    """用語だけの右クリックのメニューを出す(用語がなければ出さない)。"""
    if key not in GLOSSARY:
        return
    hide_tooltip()
    menu = tk.Menu(widget, tearoff=False)
    add_term_items(menu, key)
    try:
        menu.tk_popup(widget.winfo_rootx() + event.x, widget.winfo_rooty() + event.y)
    finally:
        menu.grab_release()


# ---- 部品に付ける ----

def attach(widget: tk.Misc, term: TermSource, menu: bool = True) -> None:
    """部品にマウスを乗せたら吹き出しを出す。menu が True なら、右クリックで用語のメニューを出す。"""
    if isinstance(term, str):
        _check(term)
    def enter(_event):
        key = _resolve(term)
        if key is None:
            return
        if tooltips_enabled():
            _set_cursor(widget, HELP_CURSOR)
        _TOOLTIP.schedule(widget, key)

    def leave(_event):
        _set_cursor(widget, "")
        _TOOLTIP.hide()

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")
    for sequence in ("<ButtonPress-1>", "<MouseWheel>", "<KeyPress>"):
        widget.bind(sequence, _TOOLTIP.hide, add="+")
    if menu:
        widget.bind("<Button-3>", lambda e: popup_term_menu(widget, e, _resolve(term)), add="+")


def attach_headings(tree: ttk.Treeview, terms: dict[str, str]) -> None:
    """Treeview の列の見出しにマウスを乗せたら、その列の用語の吹き出しを出す。

    右クリックのメニューは gui_menu.bind_tree が出す(term_at でここの対応を調べる)。
    """
    for key in terms.values():
        _check(key)
    _heading_terms[str(tree)] = dict(terms)

    def motion(event):
        key = heading_term(tree, event.x, event.y)
        if key is None:
            _set_cursor(tree, "")
            _TOOLTIP.hide()
            return
        if tooltips_enabled():
            _set_cursor(tree, HELP_CURSOR)
        _TOOLTIP.schedule(tree, key)

    def leave(_event):
        _set_cursor(tree, "")
        _TOOLTIP.hide()

    tree.bind("<Motion>", motion, add="+")
    tree.bind("<Leave>", leave, add="+")
    for sequence in ("<ButtonPress-1>", "<MouseWheel>"):
        tree.bind(sequence, _TOOLTIP.hide, add="+")


def _check(key: str) -> None:
    """辞書にない用語のキー(書き間違い)は、画面を作るときに止める。"""
    if key not in GLOSSARY:
        raise KeyError(f"core_glossary.py にない用語です: {key}")


def _column_at(tree: ttk.Treeview, x: int) -> str | None:
    column = tree.identify_column(x)  # "#1" など(表示している列の番号)
    if not column or column == "#0":
        return None
    try:
        return tree.column(column, "id")
    except tk.TclError:
        return None


def heading_term(tree: ttk.Treeview, x: int, y: int) -> str | None:
    """見出しの上なら、その列の用語のキー。見出しの外・用語のない列なら None。"""
    if tree.identify_region(x, y) != "heading":
        return None
    return _heading_terms.get(str(tree), {}).get(_column_at(tree, x) or "")


def term_at(tree: ttk.Treeview, x: int) -> str | None:
    """その横の位置の列の用語のキー(見出しでもセルでも)。なければ None。"""
    return _heading_terms.get(str(tree), {}).get(_column_at(tree, x) or "")


def _set_cursor(widget: tk.Misc, cursor: str) -> None:
    try:
        widget.configure(cursor=cursor)
    except tk.TclError:
        pass
