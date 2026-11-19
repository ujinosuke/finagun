"""株価チャートのウォッチリスト。銘柄をグループに分けて登録し、チャートを見て回る。

左側にグループと銘柄の一覧、右側にチャート(gui_chart.py の ChartFrame)を表示する。
グループはいくつでも作れ、1 つのグループに複数の銘柄を登録できる
(同じ銘柄を複数のグループに登録してもよい)。

グループは銘柄比較(fina_compare.py)と共有する。どちらで変えても、もう片方に出る
(同時に開いているときは、ウィンドウに戻ってきたときに読み直す)。
「保有資産」は銘柄比較の固定のグループの名前なので、ここでは作れない。

登録した内容は、ルートディレクトリの watchlist.json に保存し、次回の起動時に自動で読み込む。
チャートの表示と図形は、どのプログラムから開いても同じになるよう、銘柄ごとに charts フォルダに保存する
(gui_chart.py。前の版で watchlist.json に入っていた図形は、起動時に charts へ引き継ぐ)。
登録内容は変えたときにすぐ保存する(毎回 watchlist.json を読み直してから変える。core_watchlist.py)。
選んでいるグループと銘柄は、銘柄を選んだとき・ウィンドウを閉じるときに保存する。

株価は取得しない。stock_database フォルダの保存済みデータだけを使うので、
先に core_gather.py で株価を取得しておくこと。

使い方:
    python fina_watchlist.py         # 前回の状態で起動
    python fina_watchlist.py 7203    # 指定した銘柄を選んだ状態で起動(登録していなければチャートだけ)

操作:
    グループ        上の欄で選ぶ。「新規」「名前変更」「削除」ボタンで編集する
    銘柄の登録      「銘柄の追加」欄に銘柄コードまたは銘柄名の一部を入力すると、候補が出る。
                    上下キーで選んで Enter、または候補をダブルクリックで、選択中のグループに登録する。
                    空白で区切ると、すべての語を含む銘柄に絞り込む。
                    全角・半角、大文字・小文字、ひらがな・カタカナは区別しない
    チャートの表示  一覧の銘柄をクリック、または上下キーで選ぶ。
                    チャートの操作は gui_chart.py と同じ
    並べ替え        銘柄を選んで「↑」「↓」ボタン
    銘柄の削除      銘柄を選んで「削除」ボタン、または Delete キー(図形は残る)
"""

import math
import os
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from gui_theme import apply_theme
from core_chartstore import migrate_drawings
from gui_chart import DISPLAY_KEYS, ChartFrame
from core_settings import app_title, load_settings, update_settings
from core_gather import csv_path_for, load_saved
from core_stocks import load_listed, stock_index
from core_watchlist import (
    WATCHLIST_PATH, add_codes, create_group, delete_group, load_groups, move_code, name_error, parse_groups,
    read_watchlist, remove_codes, rename_group, watchlist_mtime, write_selected,
)
from gui_widgets import StockSearchBox

LIST_WIDTH = 400      # 一覧の最初の幅(ピクセル)

# 一覧の列(列の名前 -> (見出し、幅、文字の寄せ方))
LIST_COLUMNS = {
    "code": ("コード", 54, "center"),
    "name": ("銘柄名", 170, "w"),
    "close": ("終値", 74, "e"),
    "change": ("前日比", 62, "e"),
}


def migrate_chart_state(chart: dict) -> None:
    """前の版の watchlist.json の chart(表示の設定と全銘柄の図形)を引き継ぐ。

    図形は銘柄ごとのファイル(charts/)に書き出す(ファイルがまだない銘柄だけ)。
    表示の設定と描く色は、ToreCan の設定にまだなければ、チャートの既定・描く色にする。
    何度呼んでも同じ結果になる(次に保存した watchlist.json からは chart がなくなる)。
    """
    try:
        migrate_drawings(chart.get("drawings"))
        settings = load_settings()
        changes = {}
        if not settings.chart_defaults:
            changes["chart_defaults"] = {k: chart[k] for k in DISPLAY_KEYS if k in chart}
        if not settings.draw_color and isinstance(chart.get("color"), str):
            changes["draw_color"] = chart["color"]
        if changes:
            update_settings(**changes)
    except OSError:
        pass  # 引き継げなくても、ウォッチリストは使える(次の起動でもう一度試す)


class WatchlistFrame(ttk.Frame):
    """グループと銘柄の一覧(左)と、チャート(右)をまとめた部品。"""

    def __init__(self, master, data: dict):
        super().__init__(master)
        self.listed = load_listed()
        self.groups = parse_groups(data.get("groups"))
        self.group_index = 0
        self.quotes: dict[str, tuple[str, str]] = {}  # コード -> (終値、前日比) の表示
        # 他のプログラム(銘柄比較・fina_asset.py など)が変えたかを見るための、読んだときの更新時刻
        self.saved_mtime = watchlist_mtime()

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, width=LIST_WIDTH)
        left.pack_propagate(False)
        self._build_group_bar(left)
        self._build_list(left)
        self._build_search(left)
        if isinstance(data.get("chart"), dict):
            migrate_chart_state(data["chart"])
        self.chart = ChartFrame(panes)
        panes.add(left, weight=0)
        panes.add(self.chart, weight=1)

        # 前回の状態に戻す(チャートの表示と図形は、銘柄を選んだときに charts/ から読まれる)
        selected = data.get("selected") if isinstance(data.get("selected"), dict) else {}
        names = [group["name"] for group in self.groups]
        if selected.get("group") in names:
            self.group_index = names.index(selected["group"])
        self._refresh_groups()
        self._refresh_stocks(select=selected.get("code"))
        # 他のプログラムでグループや銘柄を変えたら、戻ってきたときに読み直す
        master.winfo_toplevel().bind("<FocusIn>", lambda e: self._reload_if_changed(), add="+")

    @property
    def group(self) -> dict:
        """選択中のグループ。"""
        return self.groups[self.group_index]

    # ---- 画面の組み立て ----

    def _build_group_bar(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(side="top", fill="x", padx=4, pady=(4, 0))
        ttk.Label(bar, text="グループ").pack(side="left")
        self.group_box = ttk.Combobox(bar, state="readonly", width=14)
        self.group_box.pack(side="left", fill="x", expand=True, padx=(4, 4))
        self.group_box.bind("<<ComboboxSelected>>", lambda e: self._on_group_selected())
        for text, command in (("新規", self.new_group), ("名前変更", self.rename_group), ("削除", self.delete_group)):
            ttk.Button(bar, text=text, width=len(text) * 2 + 1, command=command).pack(side="left", padx=(0, 2))

    def _build_search(self, parent):
        self.search = StockSearchBox(
            parent, stock_index(self.listed),
            on_pick=lambda stock: self.add_stock(stock.code),
            on_message=self.status_var.set,
            label="銘柄の追加", button="追加",
        )
        # 銘柄の一覧(_build_list で先に配置済み)より上に置く
        self.search.pack(side="top", fill="x", padx=(4, 0), pady=(6, 0), before=self.list_frame)

    def _build_list(self, parent):
        self.status_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.status_var, wraplength=LIST_WIDTH - 12, justify="left").pack(
            side="bottom", fill="x", padx=6, pady=4,
        )
        bar = ttk.Frame(parent)
        bar.pack(side="bottom", fill="x", padx=4, pady=(4, 0))
        ttk.Button(bar, text="↑", width=3, command=lambda: self.move_stock(-1)).pack(side="left")
        ttk.Button(bar, text="↓", width=3, command=lambda: self.move_stock(1)).pack(side="left", padx=(2, 0))
        ttk.Button(bar, text="削除", width=5, command=self.remove_stock).pack(side="right")

        self.list_frame = frame = ttk.Frame(parent)
        frame.pack(side="top", fill="both", expand=True, padx=(4, 0), pady=(6, 0))
        self.tree = ttk.Treeview(frame, columns=list(LIST_COLUMNS), show="headings", selectmode="browse")
        for column, (heading, width, anchor) in LIST_COLUMNS.items():
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor=anchor, stretch=column == "name")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Delete>", lambda e: self.remove_stock())

    # ---- 保存 ----

    def save(self) -> bool:
        """選んでいるグループと銘柄を watchlist.json に保存する。保存できたら True。

        グループと銘柄は、変えたときに保存済み(_apply)。
        チャートの表示と図形は、ChartFrame が銘柄ごとに charts/ へ保存する。
        """
        selected = self.tree.selection()
        try:
            write_selected(self.group["name"], selected[0] if selected else None)
        except (OSError, ValueError) as e:
            messagebox.showerror("fina_watchlist", f"{WATCHLIST_PATH.name} に保存できません。\n{e}", parent=self)
            return False
        self.saved_mtime = watchlist_mtime()
        return True

    def _apply(self, edit, *args, group: str | None = None, select: str | None = None) -> bool:
        """グループや銘柄を core_watchlist の関数 edit で変えて保存し、表示し直す。できたら True。

        edit はファイルを読み直してから変えるので、他のプログラムでの変更は消えない。
        group: 変えたあとに選ぶグループ(なければ今のグループ)。select: 選ぶ銘柄。
        """
        try:
            result = edit(*args)
            groups = result if isinstance(result, list) else load_groups()  # add_codes は足した数を返す
        except (OSError, ValueError) as e:
            messagebox.showerror("fina_watchlist", f"{WATCHLIST_PATH.name} に保存できません。\n{e}", parent=self)
            return False
        self.saved_mtime = watchlist_mtime()
        self._set_groups(groups, group or self.group["name"], select)
        return True

    def _set_groups(self, groups: list[dict], name: str, select: str | None) -> None:
        """グループの一覧を入れ替え、name のグループ(なければ同じ位置のグループ)と select の銘柄を選ぶ。"""
        index = self.group_index
        self.groups = groups
        names = [g["name"] for g in groups]
        self.group_index = names.index(name) if name in names else min(index, len(groups) - 1)
        self._refresh_groups()
        self._refresh_stocks(select=select)

    def _reload_if_changed(self) -> None:
        """他のプログラムが watchlist.json を変えていたら、読み直して表示し直す。"""
        mtime = watchlist_mtime()
        if mtime == self.saved_mtime:
            return
        try:
            groups = load_groups()
        except (OSError, ValueError):
            return  # 読めないときは、今の表示のまま(次に戻ってきたときにもう一度試す)
        self.saved_mtime = mtime
        if groups == self.groups:
            return
        selected = self.tree.selection()
        self._set_groups(groups, self.group["name"], selected[0] if selected else None)

    # ---- グループ ----

    def _refresh_groups(self):
        self.group_box.configure(values=[group["name"] for group in self.groups])
        self.group_box.current(self.group_index)

    def _on_group_selected(self):
        self.group_index = self.group_box.current()
        self.group_box.selection_clear()
        self._refresh_stocks()
        self.save()

    def _ask_group_name(self, title: str, initial: str = "") -> str | None:
        """グループの名前を尋ねる。取り消しや、使えない名前のときは None。"""
        name = simpledialog.askstring(title, "グループの名前", initialvalue=initial, parent=self)
        name = (name or "").strip()
        if not name or name == initial:
            return None
        error = name_error(name, self.groups)
        if error:
            messagebox.showerror("fina_watchlist", error, parent=self)
            return None
        return name

    def new_group(self) -> None:
        """グループを作り、そのグループを選ぶ。"""
        name = self._ask_group_name("グループの新規作成")
        if name is not None and self._apply(create_group, name, group=name):
            self.save()

    def rename_group(self) -> None:
        """選択中のグループの名前を変える。"""
        name = self._ask_group_name("グループの名前変更", self.group["name"])
        selected = self.tree.selection()
        if name is not None and self._apply(rename_group, self.group["name"], name, group=name,
                                            select=selected[0] if selected else None):
            self.save()

    def delete_group(self) -> None:
        """選択中のグループを消す。最後の 1 つを消したときは、空のグループを作り直す。"""
        group = self.group
        if group["codes"] and not messagebox.askyesno(
            "fina_watchlist",
            f"グループ「{group['name']}」と、登録されている {len(group['codes'])} 銘柄を削除しますか?",
            parent=self,
        ):
            return
        if self._apply(delete_group, group["name"]):
            self.save()

    # ---- 銘柄の登録 ----

    def show_code(self, code: str) -> None:
        """銘柄を選ぶ。登録済みのグループ(選択中のグループを優先)を探し、どこにもなければチャートだけ出す。"""
        order = [self.group_index, *(i for i in range(len(self.groups)) if i != self.group_index)]
        for i in order:
            if code in self.groups[i]["codes"]:
                self.group_index = i
                self._refresh_groups()
                self._refresh_stocks(select=code)
                return
        if csv_path_for(code).exists():
            self.chart.set_code(code)
            self.status_var.set(f"{code} はウォッチリストに登録されていません(チャートだけ表示しています)。")

    def add_stock(self, code: str) -> None:
        """銘柄を選択中のグループに登録して選ぶ。登録済みなら選ぶだけ。"""
        added = code not in self.group["codes"]
        if added and not self._apply(add_codes, self.group["name"], [code], select=code):
            return
        self._refresh_stocks(select=code)
        if not added:
            self.status_var.set(f"{code} {self._stock_name(code)} は、このグループに登録済みです。")
        self.save()

    def remove_stock(self) -> None:
        """選択中の銘柄を、選択中のグループから外す。次の銘柄(なければ前の銘柄)を選ぶ。"""
        selected = self.tree.selection()
        if not selected:
            return
        codes = self.group["codes"]
        i = codes.index(selected[0])
        rest = [c for c in codes if c != selected[0]]
        neighbor = rest[min(i, len(rest) - 1)] if rest else None
        if self._apply(remove_codes, self.group["name"], [selected[0]], select=neighbor):
            self.save()

    def move_stock(self, step: int) -> None:
        """選択中の銘柄を、一覧の中で上(step=-1)または下(step=1)に動かす。"""
        selected = self.tree.selection()
        if not selected:
            return
        codes = self.group["codes"]
        if not 0 <= codes.index(selected[0]) + step < len(codes):
            return
        if self._apply(move_code, self.group["name"], selected[0], step, select=selected[0]):
            self.save()

    # ---- 銘柄の一覧 ----

    def _stock_name(self, code: str) -> str:
        """銘柄名。銘柄一覧にない銘柄(上場廃止など)は空文字。"""
        name = self.listed["銘柄名"].get(code, "")
        return name if isinstance(name, str) else ""

    def _quote(self, code: str) -> tuple[str, str]:
        """保存済みの株価から、最新の終値と前日比の表示を作る。"""
        if code not in self.quotes:
            try:
                daily = load_saved(csv_path_for(code))
                close = daily["Close"].dropna() if daily is not None else []
            except (OSError, ValueError, KeyError):  # 壊れた CSV は、データなしとして扱う
                close = []
            if len(close) == 0:
                self.quotes[code] = ("データなし", "")
            else:
                change = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) > 1 else math.nan
                self.quotes[code] = (f"{close.iloc[-1]:,.1f}", "" if math.isnan(change) else f"{change:+.2f}%")
        return self.quotes[code]

    def _refresh_stocks(self, select: str | None = None):
        """選択中のグループの銘柄で一覧を作り直す。select の銘柄(なければ先頭の銘柄)を選ぶ。"""
        self.tree.delete(*self.tree.get_children())
        codes = self.group["codes"]
        for code in codes:
            self.tree.insert("", "end", iid=code, values=(code, self._stock_name(code), *self._quote(code)))
        self.status_var.set(
            "" if codes else "「銘柄の追加」欄に銘柄コードまたは銘柄名を入力して、銘柄を登録してください。",
        )
        if codes:
            code = select if select in codes else codes[0]
            self.tree.selection_set(code)
            self.tree.see(code)

    def _on_select(self):
        selected = self.tree.selection()
        if not selected:
            return
        code = selected[0]
        if not csv_path_for(code).exists():
            # チャートを表示できないので、前の銘柄のままにして理由を出す
            self.status_var.set(
                f"{code} {self._stock_name(code)} の株価データがありません。\n"
                f"先に python core_gather.py {code} を実行してください。",
            )
        elif code != self.chart.code:
            self.chart.set_code(code)
        self.save()


def main() -> int:
    root = tk.Tk()
    apply_theme(root)  # ToreCan の色・フォント(gui_theme.py)
    root.title(app_title("watchlist"))
    root.geometry("1600x880")
    try:
        data = read_watchlist()
    except OSError as e:
        messagebox.showerror("fina_watchlist", f"{WATCHLIST_PATH.name} を読み込めません。\n{e}", parent=root)
        return 1
    except ValueError as e:
        # 壊れたファイルを上書きして失わないよう、名前を変えて残しておく
        backup = WATCHLIST_PATH.with_name(WATCHLIST_PATH.name + ".bak")
        os.replace(WATCHLIST_PATH, backup)
        messagebox.showwarning(
            "fina_watchlist",
            f"{WATCHLIST_PATH.name} が壊れているため、読み込めません。\n{e}\n\n"
            f"壊れたファイルを {backup.name} に残し、空のウォッチリストで起動します。",
            parent=root,
        )
        data = {}
    try:
        frame = WatchlistFrame(root, data)
    except FileNotFoundError as e:
        messagebox.showerror("fina_watchlist", str(e), parent=root)
        return 1
    frame.pack(fill="both", expand=True)
    if len(sys.argv) > 1:  # 引数の銘柄を選んだ状態で起動する(python fina_watchlist.py 7203)
        frame.show_code(sys.argv[1].upper().removesuffix(".T"))

    def on_close():
        # 最後に描いた図形や表示の設定を保存する。保存できなければ、閉じるかどうかを尋ねる
        if frame.save() or messagebox.askyesno("fina_watchlist", "保存せずに終了しますか?", parent=root):
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
