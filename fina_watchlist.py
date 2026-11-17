"""株価チャートのウォッチリスト。銘柄をグループに分けて登録し、チャートを見て回る。

左側にグループと銘柄の一覧、右側にチャート(fina_chart.py の ChartFrame)を表示する。
グループはいくつでも作れ、1 つのグループに複数の銘柄を登録できる
(同じ銘柄を複数のグループに登録してもよい)。

登録した内容は、ルートディレクトリの watchlist.json に保存し、次回の起動時に自動で読み込む。
チャートの描画の状態(足の種類、下段の表示、図形の色、銘柄ごとの図形)も同じファイルに保存する。
保存は、登録内容を変えたとき・銘柄を選んだとき・ウィンドウを閉じるときに行う。

株価は取得しない。stock_database フォルダの保存済みデータだけを使うので、
先に fina_gather.py で株価を取得しておくこと。

使い方:
    python fina_watchlist.py

操作:
    グループ        上の欄で選ぶ。「新規」「名前変更」「削除」ボタンで編集する
    銘柄の登録      「銘柄の追加」欄に銘柄コードまたは銘柄名の一部を入力すると、候補が出る。
                    上下キーで選んで Enter、または候補をダブルクリックで、選択中のグループに登録する。
                    空白で区切ると、すべての語を含む銘柄に絞り込む。
                    全角・半角、大文字・小文字、ひらがな・カタカナは区別しない
    チャートの表示  一覧の銘柄をクリック、または上下キーで選ぶ。
                    チャートの操作は fina_chart.py と同じ
    並べ替え        銘柄を選んで「↑」「↓」ボタン
    銘柄の削除      銘柄を選んで「削除」ボタン、または Delete キー(図形は残る)
"""

import json
import math
import os
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from fina_chart import ChartFrame
from fina_gather import BASE_DIR, csv_path_for, load_saved
from fina_stocks import load_listed, stock_index
from fina_widgets import StockSearchBox

WATCHLIST_PATH = BASE_DIR / "watchlist.json"
FORMAT_VERSION = 1
DEFAULT_GROUP = "ウォッチリスト"  # グループが 1 つもないときに作るグループの名前
LIST_WIDTH = 400      # 一覧の最初の幅(ピクセル)

# 一覧の列(列の名前 -> (見出し、幅、文字の寄せ方))
LIST_COLUMNS = {
    "code": ("コード", 54, "center"),
    "name": ("銘柄名", 170, "w"),
    "close": ("終値", 74, "e"),
    "change": ("前日比", 62, "e"),
}


def read_watchlist() -> dict:
    """watchlist.json を読み込む。ファイルがなければ空の辞書を返す。"""
    if not WATCHLIST_PATH.exists():
        return {}
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("ファイルの形式が違います")
    return data


def write_watchlist(data: dict) -> None:
    """watchlist.json に保存する。

    書き込みの途中で止まっても元のファイルが壊れないよう、別のファイルに書いてから置き換える。
    """
    temporary = WATCHLIST_PATH.with_name(WATCHLIST_PATH.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temporary, WATCHLIST_PATH)


def parse_groups(saved) -> list[dict]:
    """保存されていたグループを、{"name": 名前, "codes": [コード, ...]} の一覧に直す。

    手で書き換えたファイルでも止まらないよう、形の違うものや重複は除く。
    グループが 1 つもなければ、空のグループを 1 つ作る。
    """
    groups = []
    for group in saved if isinstance(saved, list) else []:
        if not isinstance(group, dict):
            continue
        name, codes = group.get("name"), group.get("codes")
        if not isinstance(name, str) or not name.strip() or any(g["name"] == name.strip() for g in groups):
            continue
        codes = [c for c in codes if isinstance(c, str) and c] if isinstance(codes, list) else []
        groups.append({"name": name.strip(), "codes": list(dict.fromkeys(codes))})
    return groups or [{"name": DEFAULT_GROUP, "codes": []}]


class WatchlistFrame(ttk.Frame):
    """グループと銘柄の一覧(左)と、チャート(右)をまとめた部品。"""

    def __init__(self, master, data: dict):
        super().__init__(master)
        self.listed = load_listed()
        self.groups = parse_groups(data.get("groups"))
        self.group_index = 0
        self.quotes: dict[str, tuple[str, str]] = {}  # コード -> (終値、前日比) の表示

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes, width=LIST_WIDTH)
        left.pack_propagate(False)
        self._build_group_bar(left)
        self._build_list(left)
        self._build_search(left)
        self.chart = ChartFrame(panes)
        panes.add(left, weight=0)
        panes.add(self.chart, weight=1)

        # 前回の状態に戻す
        if isinstance(data.get("chart"), dict):
            self.chart.set_state(data["chart"])
        selected = data.get("selected") if isinstance(data.get("selected"), dict) else {}
        names = [group["name"] for group in self.groups]
        if selected.get("group") in names:
            self.group_index = names.index(selected["group"])
        self._refresh_groups()
        self._refresh_stocks(select=selected.get("code"))

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
        """グループ・選択中の銘柄・チャートの状態を watchlist.json に保存する。保存できたら True。"""
        selected = self.tree.selection()
        data = {
            "version": FORMAT_VERSION,
            "groups": self.groups,
            "selected": {"group": self.group["name"], "code": selected[0] if selected else None},
            "chart": self.chart.get_state(),
        }
        try:
            write_watchlist(data)
        except OSError as e:
            messagebox.showerror("fina_watchlist", f"{WATCHLIST_PATH.name} に保存できません。\n{e}", parent=self)
            return False
        return True

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
        if any(group["name"] == name for group in self.groups):
            messagebox.showerror("fina_watchlist", f"「{name}」という名前のグループは、すでにあります。", parent=self)
            return None
        return name

    def new_group(self) -> None:
        """グループを作り、そのグループを選ぶ。"""
        name = self._ask_group_name("グループの新規作成")
        if name is None:
            return
        self.groups.append({"name": name, "codes": []})
        self.group_index = len(self.groups) - 1
        self._refresh_groups()
        self._refresh_stocks()
        self.save()

    def rename_group(self) -> None:
        """選択中のグループの名前を変える。"""
        name = self._ask_group_name("グループの名前変更", self.group["name"])
        if name is None:
            return
        self.group["name"] = name
        self._refresh_groups()
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
        del self.groups[self.group_index]
        if not self.groups:
            self.groups.append({"name": DEFAULT_GROUP, "codes": []})
        self.group_index = min(self.group_index, len(self.groups) - 1)
        self._refresh_groups()
        self._refresh_stocks()
        self.save()

    # ---- 銘柄の登録 ----

    def add_stock(self, code: str) -> None:
        """銘柄を選択中のグループに登録して選ぶ。登録済みなら選ぶだけ。"""
        added = code not in self.group["codes"]
        if added:
            self.group["codes"].append(code)
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
        del codes[i]
        self._refresh_stocks(select=codes[min(i, len(codes) - 1)] if codes else None)
        self.save()

    def move_stock(self, step: int) -> None:
        """選択中の銘柄を、一覧の中で上(step=-1)または下(step=1)に動かす。"""
        selected = self.tree.selection()
        if not selected:
            return
        codes = self.group["codes"]
        i = codes.index(selected[0])
        j = i + step
        if not 0 <= j < len(codes):
            return
        codes[i], codes[j] = codes[j], codes[i]
        self._refresh_stocks(select=selected[0])
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
                f"先に python fina_gather.py {code} を実行してください。",
            )
        elif code != self.chart.code:
            self.chart.set_code(code)
        self.save()


def main() -> int:
    root = tk.Tk()
    root.title("ToreCan watchlist")
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

    def on_close():
        # 最後に描いた図形や表示の設定を保存する。保存できなければ、閉じるかどうかを尋ねる
        if frame.save() or messagebox.askyesno("fina_watchlist", "保存せずに終了しますか?", parent=root):
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
