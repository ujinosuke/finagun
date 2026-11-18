"""ToreCan のホーム画面。各プログラムの起動(アイコンレール)と、データ・資産の状態(ダッシュボード)。

設計は ToreCan_design.md、仕様は torecan-home-spec.md、見た目は torecan-design要望.md を参照。

左端のアイコンレールにプログラムを並べ、右のダッシュボードに 資産・株価データ・銘柄一覧 のカードと、
保有銘柄のポートフォリオ・ヒートマップを出す。
    アイコンレール  各プログラムを別のプロセス・別のウィンドウで起動する(ホームを閉じても、起動したものは閉じない)。
                    起動中のプログラムは、アイコンをアクセント色にする。
                    保存ファイルを持つ 資産管理・ウォッチリスト・銘柄比較 は、ホームから 2 つ目を起動しない
                    (閉じるときの保存で、互いの変更を上書きしてしまうため)。
                    起動したプログラムのエラー出力は logs/<名前>.log に残す。一番下は設定
    資産            総資産・前日比・含み損益・今年の実現損益(asset.json から。資産管理と同じ計算)。
                    起動したとき、今日の資産の記録(スナップショット)がまだなければ 1 件足す
    株価データ      最終日・収録銘柄数と、最新かどうか。「データを更新」で core_gather.py と同じ取得・更新を、
                    このホームの中で行う(進み具合を出し、100 銘柄の切れ目で中止できる)
    銘柄一覧        最新の銘柄一覧(listed_stocks)の年月。新しい月の一覧が出ていれば、取得の手順を案内する
    ヒートマップ    四角の大きさ = 評価額、色 = 前日比(または含み損益率)。業種ごとにまとめる。
                    ダブルクリックでその銘柄のチャートを開く
    設定            全プログラム共通の設定(settings.json): 色の決まり(海外式・日本式)、ベンチマーク、
                    相関の期間、データフォルダ。開いているプログラムは、起動し直すと反映される。
                    「初期化」のタブで、プログラムごと・すべての保存ファイルを初期化できる
                    (消すのではなく backup フォルダに移す。株価データと銘柄一覧は消さない)

ホームは 1 つしか起動できない(torecan.lock)。

使い方:
    python ToreCan.py
"""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import core_gather
from core_asset import ASSET_PATH
from core_asset_value import AssetOverview, asset_overview
from core_compare import BENCHMARKS, CORRELATION_PERIODS
from core_reset import NOTHING_SAVED, RESET_TARGETS, existing_files, reset
from core_settings import (
    APP_DIR, COLOR_SCHEMES, Settings, __version__, acquire_lock, data_root, is_japan_style, load_settings,
    market_colors, profit_colors, release_lock, update_settings,
)
from gui_icons import icon
from gui_theme import (
    ACCENT, ACCENT_LIGHT, BACKGROUND, BORDER, CARD, PAD, PAD2, TEXT, TEXT_MUTED, WARNING, apply_theme, fonts,
)
from gui_treemap import TreemapCanvas, TreemapItem

APP_NAME = "ToreCan"
HOME_LOCK = APP_DIR / "torecan.lock"
LOG_DIR = APP_DIR / "logs"
TICK = 1000               # 見回りの間隔(ミリ秒)
ASSET_CHECK_SECONDS = 5   # asset.json の更新を確かめる間隔(秒)
EARLY_EXIT_SECONDS = 5    # 起動からこの秒数以内にエラーで終わったら、起動に失敗したとみなす
JPX_LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"  # 東証上場銘柄一覧
RAIL_WIDTH = 96           # アイコンレールの幅(ピクセル)
RAIL_ICON = 32            # アイコンレールのアイコンの大きさ(ピクセル)

# ヒートマップの色の指標(表示名 -> (種類、色が一番濃くなる値 %))
HEATMAP_METRICS = {"前日比": ("change", 3.0), "含み損益率": ("unrealized", 30.0)}


@dataclass(frozen=True)
class Program:
    """アイコンレールの項目 1 つ。"""
    key: str           # アイコン(gui_icons.ICONS)とログのファイル名にも使う
    label: str
    script: str
    description: str
    single: bool       # True ならホームから 2 つ目を起動しない(保存ファイルを持つプログラム)


PROGRAMS = [
    Program("asset", "資産管理", "fina_asset.py", "保有・損益・配当・ヒートマップ", True),
    Program("watchlist", "ウォッチリスト", "fina_watchlist.py", "銘柄をグループで見て回る", True),
    Program("compare", "銘柄比較", "fina_compare.py", "指標・相関・業績を並べて比べる", True),
    Program("screening", "スクリーニング", "fina_screening.py", "条件に合う銘柄を探す(先にデータ更新)", False),
    Program("backtest", "バックテスト", "fina_bktest.py", "ストラテジーの検証", False),
    Program("techtrainer", "テクニカル訓練", "fina_techtrainer.py", "チャートを進めて売買の練習", False),
]


def python_for_gui() -> tuple[str, int]:
    """画面のあるプログラムを起動する Python と、起動のフラグ(Windows で黒いコンソール窓を出さない)。"""
    executable = Path(sys.executable)
    if sys.platform == "win32":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw), 0
        return str(executable), subprocess.CREATE_NO_WINDOW
    return str(executable), 0


def fmt_yen(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "―"
    return f"{value:+,.0f} 円" if signed else f"{value:,.0f} 円"


def make_card(parent, title: str, role: str | None = None) -> tuple[ttk.Frame, ttk.Frame, ttk.Frame]:
    """白いカード。(カード、見出しの行、中身の枠) を返す。見出しの行の右には、ボタンなどを足せる。"""
    card = ttk.Frame(parent, style="Card.TFrame", padding=PAD2)
    header = ttk.Frame(card, style="CardInner.TFrame")
    header.pack(side="top", fill="x")
    image = icon(role, 24, "accent") if role else None
    ttk.Label(header, text=f" {title}", image=image or "", compound="left", style="CardHeading.TLabel").pack(
        side="left")
    body = ttk.Frame(card, style="CardInner.TFrame")
    body.pack(side="top", fill="both", expand=True, pady=(PAD, 0))
    return card, header, body


# ---- アイコンレール(ランチャー) ----

@dataclass
class Running:
    """起動中のプログラム。"""
    process: subprocess.Popen
    started: float
    log: object   # 開いているログのファイル


class RailItem(tk.Frame):
    """アイコンレールの項目 1 つ(アイコン + 短いラベル)。マウスを乗せると色が付く。"""

    def __init__(self, master, role: str, label: str, command, on_hover):
        super().__init__(master, background=CARD, cursor="hand2", padx=4, pady=PAD)
        self.role = role
        self.command = command
        self.on_hover = on_hover
        self.active = False   # 起動中
        self.hover = False
        family = fonts(self)["small"][0]
        self.image_label = tk.Label(self, background=CARD, borderwidth=0)
        self.image_label.pack(side="top")
        self.text_label = tk.Label(self, text=label, background=CARD, foreground=TEXT, font=(family, 9),
                                   wraplength=RAIL_WIDTH - 12, justify="center")
        self.text_label.pack(side="top", pady=(2, 0))
        self.dot = tk.Label(self, text="", background=CARD, foreground=ACCENT, font=(family, 8))
        self.dot.pack(side="top")
        for widget in (self, self.image_label, self.text_label, self.dot):
            widget.bind("<Enter>", lambda e: self._set_hover(True))
            widget.bind("<Leave>", lambda e: self._set_hover(False))
            widget.bind("<Button-1>", lambda e: self.command())
        self._paint()

    def set_active(self, count: int) -> None:
        self.active = count > 0
        self.dot.configure(text="" if not count else ("● 起動中" if count == 1 else f"● {count} つ起動中"))
        self._paint()

    def _set_hover(self, hover: bool) -> None:
        self.hover = hover
        self.on_hover(hover)
        self._paint()

    def _paint(self):
        """起動中・マウスを乗せている項目は、アクセント色にする。"""
        background = ACCENT_LIGHT if self.hover else CARD
        color = "accent" if self.active or self.hover else "dark"
        image = icon(self.role, RAIL_ICON, color)
        self.image_label.configure(image=image or "", background=background)
        for widget in (self, self.text_label, self.dot):
            widget.configure(background=background)
        self.text_label.configure(foreground=ACCENT if self.active or self.hover else TEXT)


class IconRail(tk.Frame):
    """左端のアイコンレール。プログラムを起動し、起動したプロセスを覚えておく。"""

    def __init__(self, master, home: "HomeApp"):
        super().__init__(master, background=CARD, width=RAIL_WIDTH, highlightthickness=1,
                         highlightbackground=BORDER)
        self.pack_propagate(False)
        self.home = home
        self.running: dict[str, list[Running]] = defaultdict(list)
        self.items: dict[str, RailItem] = {}
        for program in PROGRAMS:
            item = RailItem(self, program.key, program.label, lambda p=program: self.launch(p),
                            lambda hover, p=program: home.show_hint(f"{p.label}: {p.description}" if hover else ""))
            item.pack(side="top", fill="x", pady=(PAD2 if not self.items else 0, 2))  # 一番上は余白をあける
            self.items[program.key] = item
        settings = RailItem(self, "settings", "設定", lambda: SettingsDialog(home),
                            lambda hover: home.show_hint("設定: 色の決まり・ベンチマーク・データフォルダ" if hover else ""))
        settings.pack(side="bottom", fill="x", pady=(0, PAD))

    def launch(self, program: Program) -> None:
        """プログラムを別のプロセスで起動する。"""
        if program.single and self.running[program.key]:
            self.home.set_message(f"{program.label}はすでに起動しています(2 つ開くと、保存で互いの変更を上書きするため)。")
            return
        LOG_DIR.mkdir(exist_ok=True)
        log = open(LOG_DIR / f"{program.key}.log", "w", encoding="utf-8")
        log.write(f"# {datetime.now():%Y-%m-%d %H:%M:%S} ToreCan v{__version__} から {program.script} を起動\n")
        log.flush()
        python, flags = python_for_gui()
        try:
            process = subprocess.Popen(
                [python, str(APP_DIR / program.script)], cwd=APP_DIR, stdout=log, stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        except OSError as e:
            log.close()
            messagebox.showerror(APP_NAME, f"{program.label}を起動できません。\n{e}", parent=self)
            return
        self.running[program.key].append(Running(process, time.time(), log))
        message = f"{program.label}を起動しました。"
        if program.key == "screening" and self.home.data_card.gathering:
            message += "データ更新の途中なので、一部の銘柄は前の日までのデータで判定されます。"
        self.home.set_message(message)
        self.tick()

    def tick(self) -> None:
        """起動中のプロセスが終わったかを確かめる。すぐにエラーで終わったものは、ログを添えて知らせる。"""
        for program in PROGRAMS:
            alive = []
            for item in self.running[program.key]:
                code = item.process.poll()
                if code is None:
                    alive.append(item)
                    continue
                item.log.close()
                if code != 0 and time.time() - item.started < EARLY_EXIT_SECONDS:
                    self._report_failure(program)
            self.running[program.key] = alive
            self.items[program.key].set_active(len(alive))

    def _report_failure(self, program: Program) -> None:
        path = LOG_DIR / f"{program.key}.log"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        except OSError:
            lines = []
        messagebox.showerror(
            APP_NAME, f"{program.label}の起動に失敗しました。\n\n" + "\n".join(lines) + f"\n\n(全体は {path} にあります)",
            parent=self,
        )


# ---- 株価データ ----

class DataCard:
    """株価データの状態とデータ更新のカード。"""

    def __init__(self, parent, home: "HomeApp"):
        self.home = home
        self.queue: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.gathering = False
        self.checking = False
        self.status: core_gather.DataStatus | None = None

        self.card, header, body = make_card(parent, "株価データ", "chart")
        self.latest_var, self.count_var, self.result_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.latest_label = ttk.Label(body, textvariable=self.latest_var, style="Card.TLabel",
                                      font=fonts(parent)["body_bold"])
        self.latest_label.pack(side="top", anchor="w")
        ttk.Label(body, textvariable=self.count_var, style="CardMuted.TLabel", justify="left").pack(
            side="top", anchor="w", pady=(2, 0))
        line = ttk.Frame(body, style="CardInner.TFrame")
        line.pack(side="top", fill="x", pady=(PAD2, 0))
        self.update_button = ttk.Button(line, text=" データを更新", image=icon("refresh", 24, "white") or "",
                                        compound="left", style="Accent.TButton", command=self.start_gather)
        self.update_button.pack(side="left")
        self.cancel_button = ttk.Button(line, text="中止", command=self.cancel_gather, state="disabled")
        self.cancel_button.pack(side="left", padx=(PAD, 0))
        self.progress = ttk.Progressbar(body, mode="determinate")
        self.progress.pack(side="top", fill="x", pady=(PAD, 0))
        ttk.Label(body, textvariable=self.result_var, style="CardMuted.TLabel", wraplength=300,
                  justify="left").pack(side="top", anchor="w", pady=(4, 0))

    # ---- 状態 ----

    def check_status(self) -> None:
        """株価データの状態を別のスレッドで調べる(各 CSV の末尾を読むので、数秒かかることがある)。"""
        if self.checking:
            return
        self.checking = True
        self.latest_var.set("株価データを調べています…")

        def work():
            try:
                self.queue.put(("status", core_gather.data_status()))
            except Exception as e:  # noqa: BLE001  調べられなくても、画面は動かし続ける
                self.queue.put(("status_error", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _show_status(self, status: core_gather.DataStatus) -> None:
        self.status = status
        if status.files == 0:
            self.latest_var.set("株価データがありません")
            self.latest_label.configure(foreground=WARNING)
            self.count_var.set("「データを更新」で全銘柄を取得してください(初回は時間がかかります)。\n"
                               f"フォルダ: {core_gather.STOCK_DATABASE_DIR}")
        else:
            state = "✓ 最新です" if status.up_to_date else f"更新が必要です({status.expected} まで)"
            self.latest_var.set(f"最終日  {status.latest}   {state}")
            self.latest_label.configure(foreground=TEXT if status.up_to_date else WARNING)
            notes = [f"収録 {status.files:,} 銘柄"]
            if status.behind:
                notes.append(f"うち {status.behind:,} 銘柄は最終日が古い(売買停止・上場廃止など)")
            if status.listed_missing:
                notes.append(f"銘柄一覧にあってデータのない銘柄 {status.listed_missing:,}")
            self.count_var.set("\n".join(notes))
        self.home.listed_card.show(status.listed_name)

    # ---- データ更新 ----

    def start_gather(self) -> None:
        """銘柄一覧の全銘柄を取得・更新する(別のスレッドで)。"""
        if self.gathering:
            return
        try:
            listed_csv = core_gather.latest_listed_stocks_csv()
        except FileNotFoundError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.card)
            return
        codes = core_gather.listed_codes(listed_csv)
        if not messagebox.askyesno(
            APP_NAME, f"{listed_csv.name} の {len(codes):,} 銘柄の株価を取得・更新します。\n"
                      "数分かかります(初回は数十分)。始めますか?", parent=self.card,
        ):
            return
        self.gathering = True
        self.cancel.clear()
        self.update_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.configure(maximum=len(codes), value=0)
        self.result_var.set(f"取得中… 0 / {len(codes):,} 銘柄")
        started = time.time()

        def progress(done: int, total: int) -> None:
            self.queue.put(("progress", done, total))

        def work():
            try:
                core_gather.STOCK_DATABASE_DIR.mkdir(parents=True, exist_ok=True)
                gatherer = core_gather.gather(codes, on_progress=progress, cancel=self.cancel)
                self.queue.put(("done", gatherer, time.time() - started))
            except core_gather.GatherCancelled:
                self.queue.put(("cancelled",))
            except core_gather.GatherBusy as e:
                self.queue.put(("busy", str(e)))
            except Exception as e:  # noqa: BLE001  通信の失敗など。画面に出す
                self.queue.put(("error", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def cancel_gather(self) -> None:
        """データ更新を中止する(100 銘柄の切れ目で止まる)。"""
        if self.gathering:
            self.cancel.set()
            self.cancel_button.configure(state="disabled")
            self.result_var.set(self.result_var.get() + "  中止しています(今の 100 銘柄が終わったら止まります)…")

    def poll(self) -> None:
        """別のスレッドから届いた結果を反映する。"""
        while True:
            try:
                message = self.queue.get_nowait()
            except queue.Empty:
                return
            kind = message[0]
            if kind == "status":
                self.checking = False
                self._show_status(message[1])
            elif kind == "status_error":
                self.checking = False
                self.latest_var.set(f"株価データを調べられません: {message[1]}")
            elif kind == "progress":
                _, done, total = message
                self.progress.configure(maximum=max(total, 1), value=done)
                if not self.cancel.is_set():
                    self.result_var.set(f"取得中… {done:,} / {total:,} 銘柄")
            else:
                self._finish(kind, message)

    def _finish(self, kind: str, message: tuple) -> None:
        self.gathering = False
        self.update_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        if kind == "done":
            gatherer, elapsed = message[1], message[2]
            counts = defaultdict(int)
            for text in gatherer.results.values():
                counts[text.split("]")[0].lstrip("[")] += 1
            text = "、".join(f"{k} {n:,}" for k, n in counts.items()) + f"(所要 {elapsed / 60:.1f} 分)"
            failed = gatherer.failed()
            if failed:
                text += f"\n失敗した銘柄: {' '.join(failed[:10])}{' ほか' if len(failed) > 10 else ''}"
            self.result_var.set("完了: " + text)
        elif kind == "cancelled":
            self.result_var.set("中止しました(取得済みの銘柄は保存されています)。")
        elif kind == "busy":
            self.result_var.set(message[1])
        else:
            self.result_var.set(f"データ更新でエラーが起きました: {message[1]}")
        self.check_status()
        self.home.asset_card.refresh()


# ---- 銘柄一覧 ----

class ListedCard:
    """銘柄一覧の版と、月次更新の案内のカード。"""

    def __init__(self, parent, home: "HomeApp"):
        self.home = home
        self.card, header, body = make_card(parent, "銘柄一覧", "listed")
        self.listed_var, self.note_var = tk.StringVar(value="―"), tk.StringVar()
        ttk.Label(body, textvariable=self.listed_var, style="Card.TLabel", font=fonts(parent)["body_bold"]).pack(
            side="top", anchor="w")
        ttk.Label(body, textvariable=self.note_var, style="CardWarning.TLabel", wraplength=260,
                  justify="left").pack(side="top", anchor="w", pady=(2, 0))
        ttk.Label(body, text="JPX が毎月、前月末時点の\n一覧を公開します。", style="CardMuted.TLabel",
                  justify="left").pack(side="top", anchor="w", pady=(2, 0))
        ttk.Button(body, text="更新の手順", command=lambda: ListedGuideDialog(home)).pack(
            side="top", anchor="w", pady=(PAD2, 0))

    def show(self, name: str | None) -> None:
        if name is None:
            self.listed_var.set("銘柄一覧がありません")
            self.note_var.set("「更新の手順」を見て、銘柄一覧を用意してください。")
            return
        month = core_gather.listed_month(name)
        self.listed_var.set(f"{month[0]}年{month[1]}月版({name})" if month else name)
        today = date.today()
        previous = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        self.note_var.set(f"{previous[0]}年{previous[1]}月版が出ています。" if month and month < previous else "")


class ListedGuideDialog(tk.Toplevel):
    """銘柄一覧(東証上場銘柄一覧)の月次更新の手順。"""

    def __init__(self, home: "HomeApp"):
        super().__init__(home)
        self.home = home
        self.title("銘柄一覧の更新")
        self.transient(home.winfo_toplevel())
        self.resizable(False, False)
        today = date.today()
        year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        name = f"T{year % 100:02d}{month:02d}"
        folder = core_gather.LISTED_STOCKS_DIR
        frame = ttk.Frame(self, padding=PAD2)
        frame.pack(fill="both", expand=True)
        steps = [
            "銘柄一覧は毎月、JPX が前月末時点の「東証上場銘柄一覧」を公開します。",
            "",
            "1. JPX の「東証上場銘柄一覧」のページを開き、Excel ファイル(data_j.xls)を保存します。",
            f"2. 保存したファイルを、次の名前で銘柄一覧のフォルダに置きます: {name}.xls",
            f"     フォルダ: {folder}",
            "3. 「CSV に変換」を押します(core_listup_stocks.py と同じ処理)。",
            "4. 銘柄一覧が変わると取得する銘柄が増減するので、続けて「データを更新」を押します。",
        ]
        ttk.Label(frame, text="\n".join(steps), justify="left").pack(side="top", anchor="w")
        buttons = ttk.Frame(frame)
        buttons.pack(side="top", fill="x", pady=(PAD2, 0))
        ttk.Button(buttons, text="JPX のページを開く", command=lambda: webbrowser.open(JPX_LIST_URL)).pack(side="left")
        ttk.Button(buttons, text="フォルダを開く", command=self._open_folder).pack(side="left", padx=(PAD, 0))
        ttk.Button(buttons, text="CSV に変換", style="Accent.TButton", command=self._convert).pack(
            side="left", padx=(PAD, 0))
        ttk.Button(buttons, text="閉じる", command=self.destroy).pack(side="right")
        self.result_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.result_var, style="Muted.TLabel", wraplength=560,
                  justify="left").pack(side="top", anchor="w", pady=(PAD, 0))
        self.bind("<Escape>", lambda e: self.destroy())

    def _open_folder(self):
        folder = core_gather.LISTED_STOCKS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606  エクスプローラーで開く
        else:
            webbrowser.open(folder.as_uri())

    def _convert(self):
        """銘柄一覧の Excel ファイルのうち、CSV がまだないものを変換する。"""
        import core_listup_stocks

        folder = core_listup_stocks.LISTED_STOCKS_DIR
        if not folder.is_dir():
            self.result_var.set(f"フォルダがありません: {folder}")
            return
        targets = [p for p in core_listup_stocks.find_excel_files([]) if not p.with_suffix(".csv").exists()]
        if not targets:
            self.result_var.set("変換する Excel ファイルがありません(CSV が既にあるものは変換しません)。")
            return
        results = []
        for path in targets:
            try:
                results.append(f"[完了] {path.name} -> {core_listup_stocks.convert_to_csv(path).name}")
            except ImportError as e:  # 旧形式の .xls には xlrd が必要
                results.append(f"[失敗] {path.name}: {e}(旧形式の .xls には pip install xlrd が必要です)")
            except (OSError, ValueError) as e:
                results.append(f"[失敗] {path.name}: {e}")
        self.result_var.set("\n".join(results))
        self.home.data_card.check_status()


# ---- 資産とヒートマップ ----

class AssetCard:
    """資産のまとめ(asset.json から)のカード。読み込んだ結果は、ヒートマップのカードにも渡す。"""

    ROWS = (("change", "前日比"), ("unrealized", "含み損益"), ("realized", "実現損益(今年)"))

    def __init__(self, parent, home: "HomeApp"):
        self.home = home
        self.queue: queue.Queue = queue.Queue()
        self.loading = False
        self.first = True          # 最初の読み込みで、今日の記録がなければ足す
        self.mtime: float | None = None
        self.checked = 0.0
        font = fonts(parent)
        self.card, header, body = make_card(parent, "資産", "asset")
        ttk.Label(body, text="総資産", style="CardMuted.TLabel").pack(side="top", anchor="w")
        self.total_label = ttk.Label(body, text="―", style="Card.TLabel", font=(font["title"][0], 18, "bold"))
        self.total_label.pack(side="top", anchor="w")
        grid = ttk.Frame(body, style="CardInner.TFrame")
        grid.pack(side="top", fill="x", pady=(PAD, 0))
        self.labels: dict[str, ttk.Label] = {}
        for n, (key, text) in enumerate(self.ROWS):
            ttk.Label(grid, text=text, style="Card.TLabel").grid(row=n, column=0, sticky="w", padx=(0, PAD2), pady=1)
            label = ttk.Label(grid, text="―", style="Card.TLabel", font=font["body_bold"])
            label.grid(row=n, column=1, sticky="e", pady=1)
            self.labels[key] = label
        grid.columnconfigure(1, weight=1)
        self.note_var = tk.StringVar()
        ttk.Label(body, textvariable=self.note_var, style="CardMuted.TLabel", wraplength=260,
                  justify="left").pack(side="top", anchor="w", pady=(PAD, 0))

    def refresh(self) -> None:
        """asset.json を読み直して表示する(別のスレッドで。株価の CSV も読むため)。"""
        if self.loading:
            return
        self.loading = True
        add_snapshot, self.first = self.first, False
        self.mtime = ASSET_PATH.stat().st_mtime if ASSET_PATH.exists() else None

        def work():
            try:
                overview = asset_overview(ASSET_PATH, date.today().isoformat(), add_snapshot=add_snapshot)
                self.queue.put(("ok", overview, add_snapshot))
            except Exception as e:  # noqa: BLE001  壊れた asset.json など。画面に出す
                self.queue.put(("error", f"{type(e).__name__}: {e}", False))

        threading.Thread(target=work, daemon=True).start()

    def tick(self) -> None:
        """結果を受け取る。asset.json が変わっていたら読み直す(資産管理で取引を入れたときなど)。"""
        while True:
            try:
                kind, result, added = self.queue.get_nowait()
            except queue.Empty:
                break
            self.loading = False
            if added and ASSET_PATH.exists():
                self.mtime = ASSET_PATH.stat().st_mtime  # 自分で足した記録では、読み直さない
            if kind == "ok":
                self._show(result)
                self.home.heatmap_card.show(result)
            else:
                self.note_var.set(f"asset.json を読めません: {result}")
        if time.time() - self.checked >= ASSET_CHECK_SECONDS:
            self.checked = time.time()
            mtime = ASSET_PATH.stat().st_mtime if ASSET_PATH.exists() else None
            if mtime != self.mtime:
                self.refresh()

    def _show(self, overview: AssetOverview | None) -> None:
        if overview is None:
            self.total_label.configure(text="―")
            for label in self.labels.values():
                label.configure(text="―", foreground=TEXT)
            self.note_var.set("資産管理で保有を登録すると、ここに出ます。")
            return
        plus, minus = profit_colors(load_settings())  # 設定画面で変えた色を、ホームにはすぐ反映する
        summary = overview.summary

        def color(value: float | None) -> str:
            return TEXT if value is None or abs(value) < 0.5 else (plus if value > 0 else minus)

        pct = f"({overview.change_pct:+.2f}%)" if overview.change_pct is not None else ""
        unrealized_pct = f"({summary.unrealized_pct:+.1f}%)" if summary.unrealized_pct is not None else ""
        self.total_label.configure(text=fmt_yen(summary.value))
        values = {
            "change": (fmt_yen(overview.change, signed=True) + pct, color(overview.change)),
            "unrealized": (fmt_yen(summary.unrealized, signed=True) + unrealized_pct, color(summary.unrealized)),
            "realized": (fmt_yen(summary.realized_ytd, signed=True), color(summary.realized_ytd)),
        }
        for key, (text, foreground) in values.items():
            self.labels[key].configure(text=text, foreground=foreground)
        notes = [f"{summary.price_date} の終値で評価({overview.holdings} 件の保有)" if summary.price_date else ""]
        if summary.missing:
            notes.append(f"※ 株価データのない {len(summary.missing)} 銘柄は合計に入れていません")
        self.note_var.set("\n".join(n for n in notes if n))


class HeatmapCard:
    """保有銘柄のポートフォリオ・ヒートマップのカード。"""

    def __init__(self, parent, home: "HomeApp"):
        self.home = home
        self.overview: AssetOverview | None = None
        self.card, header, body = make_card(parent, "ポートフォリオ・ヒートマップ", "chart")
        self.metric_var = tk.StringVar(value="前日比")
        box = ttk.Combobox(header, textvariable=self.metric_var, values=list(HEATMAP_METRICS), state="readonly",
                           width=10)
        box.pack(side="right")
        box.bind("<<ComboboxSelected>>", lambda e: self.show(self.overview))
        ttk.Label(header, text="色 ", style="CardMuted.TLabel").pack(side="right")
        self.note_var = tk.StringVar()
        ttk.Label(header, textvariable=self.note_var, style="CardMuted.TLabel").pack(side="left", padx=(PAD2, 0))
        self.canvas = TreemapCanvas(body, on_open=self._open_chart)
        self.canvas.pack(fill="both", expand=True)

    def show(self, overview: AssetOverview | None) -> None:
        self.overview = overview
        metric, scale = HEATMAP_METRICS[self.metric_var.get()]
        items = []
        for item in overview.items if overview else []:
            instrument = item.instrument
            value = item.change_pct if metric == "change" else item.unrealized_pct
            detail = (f"{instrument.code} {instrument.name}\n評価額 {fmt_yen(item.value)}\n"
                      f"{self.metric_var.get()} {'―' if value is None else f'{value:+.2f}%'}")
            items.append(TreemapItem(instrument.code, instrument.code, item.value, value,
                                     instrument.sector or "(業種なし)", instrument.name, detail))
        self.note_var.set(f"大きさ = 評価額、色の一番濃い所 = ±{scale:g}%、業種ごと。ダブルクリックでチャート"
                          if items else "")
        self.canvas.set_items(items, scale, up_is_red=is_japan_style(load_settings()))

    def _open_chart(self, code: str) -> None:
        """銘柄のチャート(gui_chart.py)を別のウィンドウで開く。"""
        python, flags = python_for_gui()
        try:
            subprocess.Popen([python, str(APP_DIR / "gui_chart.py"), code], cwd=APP_DIR, creationflags=flags)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"チャートを開けません。\n{e}", parent=self.card)


# ---- 設定 ----

class SettingsDialog(tk.Toplevel):
    """全プログラム共通の設定(settings.json)。"""

    def __init__(self, home: "HomeApp"):
        super().__init__(home)
        self.home = home
        self.title("設定")
        self.transient(home.winfo_toplevel())
        self.resizable(False, False)
        self.original = load_settings()
        settings = self.original
        self.color_var = tk.StringVar(value=settings.color_scheme)
        self.bench_names = {f"{code} {name}": code for code, name in BENCHMARKS.items()}
        self.bench_var = tk.StringVar(value=next(
            (k for k, v in self.bench_names.items() if v == settings.benchmark), next(iter(self.bench_names))))
        self.period_var = tk.StringVar(value=settings.corr_period)
        self.dir_var = tk.StringVar(value=settings.data_dir)
        self.check_var = tk.StringVar()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        form = ttk.Frame(self.notebook, padding=PAD2 + PAD)
        self.notebook.add(form, text="設定")
        heading = ttk.Label(form, text=" 設定", image=icon("settings", 24, "accent") or "", compound="left",
                            style="Heading.TLabel")
        heading.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, PAD2))
        row = 1
        ttk.Label(form, text="色の決まり").grid(row=row, column=0, sticky="nw", padx=(0, PAD2), pady=4)
        line = ttk.Frame(form)
        line.grid(row=row, column=1, sticky="w")
        for key, text in COLOR_SCHEMES.items():
            ttk.Radiobutton(line, text=text, value=key, variable=self.color_var, command=self._draw_sample).pack(
                side="left", padx=(0, PAD2))
        self.sample = tk.Canvas(line, width=70, height=36, background=CARD, highlightthickness=1,
                                highlightbackground=BORDER)
        self.sample.pack(side="left")
        row += 1
        ttk.Label(form, text="チャートのローソク足・売買の印と、資産管理・ホームの ± の数値に使います(相関の色は変わりません)。",
                  style="Muted.TLabel").grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(form, text="ベンチマーク").grid(row=row, column=0, sticky="w", padx=(0, PAD2), pady=(PAD2, 4))
        line = ttk.Frame(form)
        line.grid(row=row, column=1, sticky="w", pady=(PAD2, 4))
        ttk.Combobox(line, textvariable=self.bench_var, values=list(self.bench_names), state="readonly",
                     width=26).pack(side="left")
        ttk.Label(line, text="  銘柄比較の相関・ベータの相手", style="Muted.TLabel").pack(side="left")
        row += 1
        ttk.Label(form, text="相関の期間").grid(row=row, column=0, sticky="w", padx=(0, PAD2), pady=4)
        line = ttk.Frame(form)
        line.grid(row=row, column=1, sticky="w")
        for name in CORRELATION_PERIODS:
            ttk.Radiobutton(line, text=name, value=name, variable=self.period_var).pack(side="left", padx=(0, PAD))
        row += 1
        ttk.Label(form, text="データフォルダ").grid(row=row, column=0, sticky="w", padx=(0, PAD2), pady=(PAD2, 4))
        line = ttk.Frame(form)
        line.grid(row=row, column=1, sticky="w", pady=(PAD2, 4))
        ttk.Entry(line, textvariable=self.dir_var, width=48).pack(side="left")
        ttk.Button(line, text="参照…", command=self._browse).pack(side="left", padx=(4, 0))
        ttk.Button(line, text="既定に戻す", command=lambda: self.dir_var.set("")).pack(side="left", padx=(4, 0))
        row += 1
        ttk.Label(form, textvariable=self.check_var, style="Muted.TLabel", wraplength=560, justify="left").grid(
            row=row, column=1, sticky="w")
        self.dir_var.trace_add("write", lambda *_: self._check_dir())
        row += 1
        ttk.Label(form, text="※ 開いているプログラムは、起動し直すと反映されます。", style="Warning.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(PAD2, 0))
        row += 1
        buttons = ttk.Frame(form)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(PAD2, 0))
        ttk.Button(buttons, text="保存", style="Accent.TButton", command=self._save).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=(PAD, 0))
        self._build_reset()
        self.bind("<Escape>", lambda e: self.destroy())
        self._draw_sample()
        self._check_dir()
        self.grab_set()

    # ---- 初期化 ----

    def _build_reset(self):
        """「初期化」のタブ。プログラムごとの初期化と、すべての初期化。"""
        frame = ttk.Frame(self.notebook, padding=PAD2 + PAD)
        self.notebook.add(frame, text="初期化")
        ttk.Label(frame, text="保存している記録を消して、最初の状態に戻します。", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="消したファイルは backup フォルダに移すので、戻すこともできます。"
                              "株価データと銘柄一覧は消しません。", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(2, PAD2))
        self.reset_rows: dict[str, tuple[tk.StringVar, ttk.Button]] = {}
        for n, target in enumerate(RESET_TARGETS, start=2):
            ttk.Label(frame, text=target.label, font=fonts(self)["body_bold"]).grid(
                row=n, column=0, sticky="w", padx=(0, PAD2), pady=3)
            var = tk.StringVar()
            ttk.Label(frame, textvariable=var, style="Muted.TLabel", wraplength=470, justify="left").grid(
                row=n, column=1, sticky="w")
            button = ttk.Button(frame, text="初期化", command=lambda t=target: self._reset([t]))
            button.grid(row=n, column=2, sticky="e", padx=(PAD2, 0))
            self.reset_rows[target.key] = (var, button)
        last = len(RESET_TARGETS) + 2
        ttk.Separator(frame).grid(row=last, column=0, columnspan=3, sticky="ew", pady=PAD2)
        line = ttk.Frame(frame)
        line.grid(row=last + 1, column=0, columnspan=3, sticky="ew")
        ttk.Label(line, text="上のすべてをまとめて初期化します。", style="Muted.TLabel").pack(side="left")
        self.reset_all_button = ttk.Button(line, text="すべて初期化", style="Danger.TButton",
                                           command=lambda: self._reset(list(RESET_TARGETS)))
        self.reset_all_button.pack(side="right")
        self.reset_result = tk.StringVar()
        ttk.Label(frame, textvariable=self.reset_result, style="Muted.TLabel", wraplength=640, justify="left").grid(
            row=last + 2, column=0, columnspan=3, sticky="w", pady=(PAD, 0))
        frame.columnconfigure(1, weight=1)
        self._refresh_reset()

    def _refresh_reset(self):
        """対象ごとに、何が消えるかと、今あるファイルを表示する。"""
        any_files = False
        for target in RESET_TARGETS:
            var, button = self.reset_rows[target.key]
            files = existing_files(target)
            any_files = any_files or bool(files)
            if not target.patterns:
                var.set(NOTHING_SAVED)
                button.configure(state="disabled")
                continue
            names = "、".join(dict.fromkeys(p.name for p in files)) if files else "記録はありません"
            var.set(f"{target.description}\n今あるもの: {names}")
            button.configure(state="normal" if files else "disabled")
        self.reset_all_button.configure(state="normal" if any_files else "disabled")

    def _reset(self, targets: list) -> None:
        """確かめてから初期化する。ホームから起動して開いているプログラムがあれば、先に閉じてもらう。"""
        targets = [t for t in targets if existing_files(t)]
        if not targets:
            return
        running = [p.label for p in PROGRAMS
                   if self.home.rail.running[p.key] and any(t.key == p.key for t in targets)]
        if running:
            messagebox.showwarning(APP_NAME, f"{'・'.join(running)}が開いています。閉じてから初期化してください"
                                             "(開いたままだと、閉じるときの保存で元に戻ってしまいます)。", parent=self)
            return
        everything = len(targets) > 1
        lines = [f"・{t.label}: {t.description}" for t in targets]
        if not messagebox.askyesno(
            APP_NAME,
            ("すべて初期化します。" if everything else f"{targets[0].label}を初期化します。") + "\n\n"
            + "\n".join(lines) + "\n\n消したファイルは backup フォルダに移します。"
            "ホームを通さずに開いているプログラムがあれば、先に閉じてください。\n\n初期化しますか?",
            icon="warning", default="no", parent=self,
        ):
            return
        try:
            folder, moved = reset(targets)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"初期化できません(ファイルを開いているプログラムがないか確かめてください)。\n{e}",
                                 parent=self)
            self._refresh_reset()
            return
        self._refresh_reset()
        if any(t.key == "settings" for t in targets):
            # 全体設定を既定に戻したので、設定のタブの表示も既定にする(このあと「保存」しても元に戻らないように)
            defaults = load_settings()
            self.original = defaults
            self.color_var.set(defaults.color_scheme)
            self.bench_var.set(next(k for k, v in self.bench_names.items() if v == defaults.benchmark))
            self.period_var.set(defaults.corr_period)
            self.dir_var.set(defaults.data_dir)
            self._draw_sample()
        text = (f"{'・'.join(t.label for t in targets)}を初期化しました。{len(moved)} 個のファイルを "
                f"{folder} に移しました(戻すときは、このフォルダから元の場所に移してください)。")
        self.reset_result.set(text)
        self.home.set_message(text)
        self.home.asset_card.refresh()

    def _draw_sample(self):
        """色の見本(陽線・陰線)。"""
        up, down = market_colors(Settings(color_scheme=self.color_var.get()))
        canvas = self.sample
        canvas.delete("all")
        for x, color, (top, bottom), (high, low) in ((20, up, (10, 28), (4, 32)), (48, down, (8, 26), (3, 33))):
            canvas.create_line(x, high, x, low, fill=color, width=1)
            canvas.create_rectangle(x - 7, top, x + 7, bottom, fill=color, outline=color)

    def _browse(self):
        folder = filedialog.askdirectory(parent=self, title="データフォルダ(listed_stocks と stock_database のあるフォルダ)",
                                         initialdir=self.dir_var.get() or str(APP_DIR))
        if folder:
            self.dir_var.set(folder)

    def _check_dir(self) -> bool:
        """データフォルダの中に listed_stocks・stock_database があるかを表示する。両方あれば True。"""
        root = data_root(Settings(data_dir=self.dir_var.get().strip()))
        listed, stocks = root / "listed_stocks", root / "stock_database"
        if listed.is_dir() and stocks.is_dir():
            count = sum(1 for _ in stocks.glob("T*.csv"))
            self.check_var.set(f"✓ {root} に listed_stocks と stock_database があります({count:,} 銘柄)")
            return True
        missing = [p.name for p in (listed, stocks) if not p.is_dir()]
        self.check_var.set(f"⚠ {root} に {'・'.join(missing)} がありません")
        return False

    def _save(self):
        data_dir = self.dir_var.get().strip()
        if data_dir != self.original.data_dir and not self._check_dir() and not messagebox.askyesno(
            APP_NAME, "データフォルダに listed_stocks・stock_database がありません。このまま保存しますか?", parent=self,
        ):
            return
        try:
            update_settings(color_scheme=self.color_var.get(), benchmark=self.bench_names[self.bench_var.get()],
                            corr_period=self.period_var.get(), data_dir=data_dir)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"settings.json に保存できません。\n{e}", parent=self)
            return
        message = "設定を保存しました。開いているプログラムは、起動し直すと反映されます。"
        if data_dir != self.original.data_dir:
            message += "データフォルダの変更は、ホームも起動し直すと反映されます。"
        self.home.set_message(message)
        self.home.asset_card.refresh()
        self.destroy()


# ---- 全体 ----

class HomeApp(tk.Frame):
    """ホーム画面。左端にアイコンレール、右にダッシュボード(上に 3 枚のカード、下にヒートマップ)。"""

    def __init__(self, master):
        super().__init__(master, background=BACKGROUND)
        self.message = ""
        self.rail = IconRail(self, self)
        self.rail.pack(side="left", fill="y")

        main = ttk.Frame(self, padding=(PAD2 + PAD, PAD2, PAD2 + PAD, PAD))
        main.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(main)
        header.pack(side="top", fill="x")
        ttk.Label(header, image=icon("app", 48, "accent") or "").pack(side="left")
        ttk.Label(header, text="ToreCan", style="Title.TLabel").pack(side="left", padx=(PAD, 0))
        ttk.Label(header, text=f"v{__version__}", style="Muted.TLabel").pack(side="left", padx=(PAD, 0), pady=(PAD, 0))
        ttk.Label(header, text="じぶんでできる・えがける・学べる", style="Muted.TLabel").pack(
            side="left", padx=(PAD2, 0), pady=(PAD, 0))

        self.status_var = tk.StringVar()
        ttk.Label(main, textvariable=self.status_var, style="Muted.TLabel", wraplength=1000, justify="left").pack(
            side="bottom", fill="x", pady=(PAD, 0))

        cards = ttk.Frame(main)
        cards.pack(side="top", fill="x", pady=(PAD2, 0))
        self.asset_card = AssetCard(cards, self)
        self.data_card = DataCard(cards, self)
        self.listed_card = ListedCard(cards, self)
        for n, card in enumerate((self.asset_card.card, self.data_card.card, self.listed_card.card)):
            card.grid(row=0, column=n, sticky="nsew", padx=(0 if n == 0 else 12, 0))
        cards.columnconfigure(0, weight=3)
        cards.columnconfigure(1, weight=4)
        cards.columnconfigure(2, weight=2)

        self.heatmap_card = HeatmapCard(main, self)
        self.heatmap_card.card.pack(side="top", fill="both", expand=True, pady=(12, 0))

        self.data_card.check_status()
        self.asset_card.refresh()
        self.after(TICK, self._tick)

    def set_message(self, text: str) -> None:
        """下のお知らせ欄。"""
        self.message = text
        self.status_var.set(text)

    def show_hint(self, text: str) -> None:
        """アイコンレールにマウスを乗せている間だけ、説明をお知らせ欄に出す。"""
        self.status_var.set(text or self.message)

    def _tick(self):
        self.rail.tick()
        self.data_card.poll()
        self.asset_card.tick()
        self.after(TICK, self._tick)

    def close(self, root: tk.Tk) -> None:
        """閉じる。データ更新中なら中止してから(100 銘柄の切れ目まで待つ)。"""
        card = self.data_card
        if card.gathering:
            if not messagebox.askyesno(APP_NAME, "データ更新を中止して閉じますか?", parent=self):
                return
            card.cancel_gather()
            self.set_message("データ更新を中止しています。止まったら閉じます…")

            def wait():
                card.poll()
                if card.gathering:
                    self.after(300, wait)
                else:
                    root.destroy()

            wait()
            return
        root.destroy()


def main() -> int:
    if not acquire_lock(HOME_LOCK):
        # ホームは 1 つだけ(データ更新を 2 か所から同時に行わないため)
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_NAME, "ToreCan はすでに起動しています。", parent=root)
        root.destroy()
        return 1
    try:
        root = tk.Tk()
        apply_theme(root)
        root.title(f"ToreCan v{__version__}")
        root.geometry("1200x760")
        root.minsize(1000, 640)
        home = HomeApp(root)
        home.pack(fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", lambda: home.close(root))
        root.mainloop()
    finally:
        release_lock(HOME_LOCK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
