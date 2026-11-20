"""右クリックのメニュー(全プログラム共通)。仕様は context-menu-spec.md。

メニューの形:
    チャートを開く               A. 銘柄の共通の項目
    ─────────
    (その画面だけの操作)         B. 画面ごとの項目(ない画面では、この段ごと出さない)
    ─────────
    グループに追加…               A. 銘柄の共通の項目
    コードをコピー
    ─────────
    「PER」を Google で調べる    C. 用語の段(右クリックした列に用語の説明があるときだけ。gui_help.py)

列の見出しの右クリックでは、C の段だけを出す。
使えない項目は消さずにグレーで出す(その画面で何ができないかが分かるように)。

使い方(他のプログラムから):
    def on_menu(event, iid):
        popup_stock_menu(tree, event, codes=[iid], app_name="fina_xxx",
                         open_chart=lambda: open_chart(iid),
                         actions=[MenuAction("削除", delete)], on_message=status_var.set)
    bind_tree(tree, on_menu)
"""

import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from tkinter import messagebox, ttk

from gui_help import add_term_items, heading_term, hide_tooltip, popup_term_menu, term_at
from gui_widgets import add_to_watchlist

NOT_SUPPORTED = "(この画面では未対応)"  # 「チャートを開く」の機能がない画面で、項目の名前に添える
SHOWING = "(表示中)"                     # チャートをもう出している画面で、項目の名前に添える

_clicked_terms: dict[str, str | None] = {}  # bind_tree が右クリックした列の用語を、popup_stock_menu に渡す


@dataclass(frozen=True)
class MenuAction:
    """メニューの B の段の項目 1 つ。enabled が False ならグレーで出す。"""
    label: str
    command: Callable[[], None]
    enabled: bool = True


def popup_stock_menu(widget: tk.Misc, event, *, codes: Sequence[str], app_name: str,
                     open_chart: Callable[[], None] | None = None, chart_note: str = NOT_SUPPORTED,
                     actions: Sequence[MenuAction] = (),
                     on_message: Callable[[str], None] | None = None, term: str | None = None) -> None:
    """銘柄の右クリックのメニューを、マウスの位置に出す。

    codes: 対象の銘柄コード(空なら「グループに追加…」「コードをコピー」はグレー)。
    open_chart: 「チャートを開く」で呼ぶ関数。None ならグレーにして、名前に chart_note を添える。
    actions: B の段の項目。on_message: 「グループに追加…」「コードをコピー」の結果を知らせる先
    (None なら、グループに追加した結果だけを小さな画面で知らせる)。
    term: C の段の用語のキー。省くと、bind_tree で右クリックした列の用語(gui_help.attach_headings で付けたもの)。
    """
    hide_tooltip()
    codes = list(dict.fromkeys(codes))
    term = term or _clicked_terms.get(str(widget))
    menu = tk.Menu(widget, tearoff=False)
    _add(menu, "チャートを開く" + ("" if open_chart else chart_note), open_chart)
    if actions:
        menu.add_separator()
        for action in actions:
            _add(menu, action.label, action.command if action.enabled else None)
    menu.add_separator()

    def add_group():
        message = add_to_watchlist(widget, codes, app_name)
        if message and on_message:
            on_message(message)
        elif message:  # 知らせる欄のない画面では、小さな画面で知らせる
            messagebox.showinfo(app_name, message, parent=widget)

    def copy_codes():
        widget.clipboard_clear()
        widget.clipboard_append("\n".join(codes))
        if on_message:
            on_message(f"コード {len(codes)} 件をコピーしました: {'、'.join(codes[:5])}{' ほか' if len(codes) > 5 else ''}")

    _add(menu, "グループに追加…", add_group if codes else None)
    _add(menu, "コードをコピー", copy_codes if codes else None)
    add_term_items(menu, term)
    # event.x_root は、ほかの部品から送り直した右クリックでは正しくないことがあるので、部品の位置から求める
    try:
        menu.tk_popup(widget.winfo_rootx() + event.x, widget.winfo_rooty() + event.y)
    finally:
        menu.grab_release()


def _add(menu: tk.Menu, label: str, command: Callable[[], None] | None) -> None:
    menu.add_command(label=label, command=command or (lambda: None), state="normal" if command else "disabled")


def bind_tree(tree: ttk.Treeview, on_menu: Callable[[object, str], None] | None) -> None:
    """Treeview の右クリックで on_menu(event, 右クリックした行の iid) を呼ぶ。

    - 選んでいない行なら、その行だけを選び直す。選んでいる行(複数選べる表では複数)の上なら、選んだままにする。
    - 列の見出しでは、その列に用語の説明があれば用語の段だけを出す(gui_help.attach_headings)。
    - 行のない所では、何も出さない。on_menu が None(銘柄の行でない表)なら、見出しの用語だけを扱う。
    """
    def handler(event):
        region = tree.identify_region(event.x, event.y)
        if region == "heading":
            popup_term_menu(tree, event, heading_term(tree, event.x, event.y))
            return "break"
        if region not in ("cell", "tree") or on_menu is None:
            return "break"
        iid = tree.identify_row(event.y)
        if not iid:
            return "break"
        if iid not in tree.selection():
            tree.selection_set(iid)
        tree.focus(iid)
        _clicked_terms[str(tree)] = term_at(tree, event.x)
        try:
            on_menu(event, iid)
        finally:
            _clicked_terms.pop(str(tree), None)
        return "break"
    tree.bind("<Button-3>", handler)
