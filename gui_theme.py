"""ToreCan の見た目(色・フォント・余白)をそろえる ttk のテーマ。

torecan-design要望.md に合わせ、標準の ttk のテーマ(clam)をもとに、色とフォントを決め打ちにする。
外部のテーマのライブラリ(ttkbootstrap など)は使わない(ライブラリを増やさず、全プログラムで同じに使えるように)。

    背景        #F4F6F8(薄いグレー)
    カード面    #FFFFFF
    アクセント  #2563EB(青。タイトル・主要なボタン・進み具合のバー・選ばれた項目)
    文字        #1F2937、補助の文字 #6B7280
    フォント    Yu Gothic UI(なければ Meiryo UI)。タイトル 20pt 太字、見出し 11pt 太字、本文 10pt、補助 9pt
    余白        8 の倍数(PAD・PAD2・PAD3)

値動き(上昇・下落、利益・損失)の色は、全体設定の日本式・海外式に従う(core_settings.profit_colors)。

使い方(各プログラムの main で、ルートのウィンドウを作った直後に 1 回呼ぶ):
    root = tk.Tk()
    apply_theme(root)
スタイルの名前(ttk の style=...):
    Card.TFrame / Card.TLabel / CardMuted.TLabel / CardHeading.TLabel   白いカードとその中の文字
    Title.TLabel / Heading.TLabel / Muted.TLabel / Accent.TLabel        背景の上の文字
    Accent.TButton(主要なボタン)、Icon.TButton(アイコンだけの平たいボタン)
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from gui_icons import set_window_icon

BACKGROUND = "#F4F6F8"
CARD = "#FFFFFF"
ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"    # 押したとき
ACCENT_LIGHT = "#DBEAFE"   # 選ばれた行・マウスを乗せた項目の背景
TEXT = "#1F2937"
TEXT_MUTED = "#6B7280"
BORDER = "#E5E7EB"
FIELD_BORDER = "#D1D5DB"
DISABLED = "#9CA3AF"
WARNING = "#B45309"
DANGER = "#B91C1C"         # 初期化など、取り返しのつきにくい操作

PAD = 8     # 余白は 8 の倍数でそろえる
PAD2 = 16
PAD3 = 24

FONT_FAMILIES = ("Yu Gothic UI", "Meiryo UI")
FONT_SIZES = {"title": 20, "heading": 11, "body": 10, "small": 9}


def font_family(root: tk.Misc) -> str:
    """使うフォント(Yu Gothic UI、なければ Meiryo UI、どちらもなければ Tk の既定)。"""
    available = set(tkfont.families(root))
    return next((f for f in FONT_FAMILIES if f in available), tkfont.nametofont("TkDefaultFont").actual("family"))


def fonts(root: tk.Misc) -> dict[str, tuple]:
    """用途 -> フォント(family, size[, weight])。"""
    family = font_family(root)
    return {
        "title": (family, FONT_SIZES["title"], "bold"),
        "heading": (family, FONT_SIZES["heading"], "bold"),
        "body": (family, FONT_SIZES["body"]),
        "body_bold": (family, FONT_SIZES["body"], "bold"),
        "small": (family, FONT_SIZES["small"]),
        "value": (family, 13, "bold"),
    }


def apply_theme(root: tk.Tk) -> None:
    """ttk のスタイルと、tk の部品の既定の色・フォントを、ToreCan の見た目にそろえる。"""
    font = fonts(root)
    family = font_family(root)
    # tk の既定のフォント(ttk の部品・ダイアログ・Listbox などが使う)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
        try:
            tkfont.nametofont(name).configure(family=family, size=FONT_SIZES["body"])
        except tk.TclError:
            pass
    tkfont.nametofont("TkSmallCaptionFont").configure(family=family, size=FONT_SIZES["small"])
    root.configure(background=BACKGROUND)
    # tk の部品(Listbox・Text・Canvas・Toplevel など)の既定の色
    root.option_add("*Background", BACKGROUND)
    root.option_add("*Foreground", TEXT)
    root.option_add("*Listbox.Background", CARD)
    root.option_add("*Listbox.selectBackground", ACCENT_LIGHT)
    root.option_add("*Listbox.selectForeground", TEXT)
    root.option_add("*Text.Background", CARD)
    root.option_add("*Canvas.Background", CARD)
    root.option_add("*Menu.Background", CARD)
    root.option_add("*Menu.activeBackground", ACCENT_LIGHT)
    root.option_add("*Menu.activeForeground", TEXT)
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_LIGHT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BACKGROUND, foreground=TEXT, font=font["body"], bordercolor=BORDER,
                    lightcolor=BACKGROUND, darkcolor=BACKGROUND, troughcolor=BORDER, focuscolor=ACCENT,
                    selectbackground=ACCENT_LIGHT, selectforeground=TEXT, fieldbackground=CARD,
                    insertcolor=TEXT)
    style.map(".", foreground=[("disabled", DISABLED)])

    # 文字
    style.configure("Title.TLabel", font=font["title"], foreground=ACCENT)
    style.configure("Heading.TLabel", font=font["heading"])
    style.configure("Muted.TLabel", font=font["small"], foreground=TEXT_MUTED)
    style.configure("Accent.TLabel", foreground=ACCENT)
    style.configure("Warning.TLabel", foreground=WARNING)

    # カード(白い面)
    style.configure("Card.TFrame", background=CARD, relief="solid", borderwidth=1, bordercolor=BORDER)
    style.configure("CardInner.TFrame", background=CARD)
    style.configure("Card.TLabel", background=CARD)
    style.configure("CardHeading.TLabel", background=CARD, font=font["heading"])
    style.configure("CardMuted.TLabel", background=CARD, foreground=TEXT_MUTED, font=font["small"])
    style.configure("CardValue.TLabel", background=CARD, font=font["value"])
    style.configure("CardWarning.TLabel", background=CARD, foreground=WARNING, font=font["small"])
    style.configure("TLabelframe", background=BACKGROUND, bordercolor=BORDER, relief="solid")
    style.configure("TLabelframe.Label", background=BACKGROUND, foreground=TEXT, font=font["body_bold"])

    # ボタン
    style.configure("TButton", background=CARD, foreground=TEXT, bordercolor=FIELD_BORDER, lightcolor=CARD,
                    darkcolor=CARD, padding=(PAD + 4, 4), relief="solid", borderwidth=1)
    style.map("TButton", background=[("pressed", ACCENT_LIGHT), ("active", "#F3F4F6")],
              bordercolor=[("focus", ACCENT)])
    style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF", bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, font=font["body_bold"])
    style.map("Accent.TButton", background=[("disabled", BORDER), ("pressed", ACCENT_DARK), ("active", ACCENT_DARK)],
              foreground=[("disabled", DISABLED)], bordercolor=[("disabled", BORDER)])
    # 取り返しのつきにくい操作(初期化など)のボタン。赤い文字と枠
    style.configure("Danger.TButton", background=CARD, foreground=DANGER, bordercolor=DANGER, lightcolor=CARD,
                    darkcolor=CARD, font=font["body_bold"])
    style.map("Danger.TButton", background=[("disabled", BACKGROUND), ("pressed", "#FEE2E2"), ("active", "#FEF2F2")],
              foreground=[("disabled", DISABLED)], bordercolor=[("disabled", BORDER)])
    style.configure("Icon.TButton", background=BACKGROUND, bordercolor=BACKGROUND, lightcolor=BACKGROUND,
                    darkcolor=BACKGROUND, padding=4)
    style.map("Icon.TButton", background=[("pressed", ACCENT_LIGHT), ("active", BORDER)],
              bordercolor=[("active", BORDER)])
    style.configure("CardIcon.TButton", background=CARD, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD,
                    padding=4)
    style.map("CardIcon.TButton", background=[("pressed", ACCENT_LIGHT), ("active", "#F3F4F6")],
              bordercolor=[("active", BORDER)])

    # 入力欄・選択
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=CARD, bordercolor=FIELD_BORDER, lightcolor=CARD, darkcolor=CARD,
                        padding=4, arrowcolor=TEXT_MUTED)
        style.map(name, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                  fieldbackground=[("readonly", CARD), ("disabled", BACKGROUND)])
    style.map("TCombobox", selectbackground=[("readonly", CARD)], selectforeground=[("readonly", TEXT)])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=BACKGROUND, indicatorbackground=CARD, indicatorforeground=ACCENT,
                        upperbordercolor=FIELD_BORDER, lowerbordercolor=FIELD_BORDER)
        style.map(name, background=[("active", BACKGROUND)],
                  indicatorbackground=[("selected", ACCENT), ("pressed", ACCENT_LIGHT)],
                  indicatorforeground=[("selected", "#FFFFFF")])
    for name in ("Card.TCheckbutton", "Card.TRadiobutton"):
        style.configure(name, background=CARD)
        style.map(name, background=[("active", CARD)])

    # 進み具合のバー(標準の緑は浮くので、アクセント色)
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BORDER, bordercolor=BORDER,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)

    # 一覧(Treeview)
    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT, bordercolor=BORDER,
                    rowheight=24)
    style.map("Treeview", background=[("selected", ACCENT_LIGHT)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=BACKGROUND, foreground=TEXT_MUTED, font=font["body_bold"],
                    bordercolor=BORDER, lightcolor=BACKGROUND, darkcolor=BACKGROUND, relief="flat", padding=4)
    style.map("Treeview.Heading", background=[("active", BORDER)])

    # タブ
    style.configure("TNotebook", background=BACKGROUND, bordercolor=BORDER, tabmargins=(PAD, PAD, PAD, 0))
    style.configure("TNotebook.Tab", background=BACKGROUND, foreground=TEXT_MUTED, padding=(PAD2, 6),
                    bordercolor=BORDER, lightcolor=BACKGROUND, font=font["body"])
    style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", ACCENT)],
              lightcolor=[("selected", CARD)], padding=[("selected", (PAD2, 6))], expand=[("selected", (0, 0, 0, 0))])

    # スクロールバー・区切り
    style.configure("TScrollbar", background=BORDER, troughcolor=BACKGROUND, bordercolor=BACKGROUND,
                    arrowcolor=TEXT_MUTED, lightcolor=BORDER, darkcolor=BORDER)
    style.map("TScrollbar", background=[("active", FIELD_BORDER)])
    style.configure("TSeparator", background=BORDER)
    style.configure("TPanedwindow", background=BACKGROUND)
    style.configure("Sash", sashthickness=6, gripcount=0, background=BACKGROUND)

    set_window_icon(root)
