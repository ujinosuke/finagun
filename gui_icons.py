"""アイコン(Material Icons の Filled スタイル)の読み込みと、PNG の作成。

アイコンは icons フォルダの PNG(24px・48px、色は 濃いグレー・アクセント・白)を使う。
PNG は、Google の Material Icons のフォント(MaterialIcons-Regular.ttf。Apache License 2.0)から作る。
フォントはリポジトリに入れない。アイコンを足すときは、フォントを取得して次を実行する:
    python gui_icons.py build <MaterialIcons-Regular.ttf のパス> <MaterialIcons-Regular.codepoints のパス>
    (フォントと codepoints は https://github.com/google/material-design-icons の font フォルダにある)

使い方(他のプログラムから):
    image = icon("asset", 48)              # 濃いグレーの 48px
    image = icon("asset", 48, "accent")    # アクセント色
    ttk.Label(parent, image=image)         # PhotoImage はこのモジュールが持っておくので、消えない
    set_window_icon(root)                  # ウィンドウのアイコン(ローソク足)
"""

import sys
import tkinter as tk
from pathlib import Path

from core_settings import APP_DIR

ICON_DIR = APP_DIR / "icons"
SIZES = (24, 32, 48)
# 色の名前 -> 色(gui_theme の色と同じ)
COLORS = {"dark": "#374151", "accent": "#2563EB", "white": "#FFFFFF", "muted": "#9CA3AF"}

# 用途 -> Material Icons の名前(torecan-design要望.md のアイコン割り当て案の本命)
ICONS = {
    "asset": "account_balance_wallet",
    "backtest": "history",
    "techtrainer": "fitness_center",
    "screening": "filter_alt",
    "watchlist": "visibility",
    "compare": "compare",
    "settings": "settings",
    "refresh": "refresh",
    "listed": "list_alt",
    "app": "candlestick_chart",
    "chart": "show_chart",
}

_cache: dict[tuple[str, int, str], tk.PhotoImage] = {}


def icon_path(role: str, size: int, color: str = "dark") -> Path:
    return ICON_DIR / f"{ICONS[role]}_{color}_{size}.png"


def icon(role: str, size: int = 24, color: str = "dark") -> tk.PhotoImage | None:
    """アイコンの PhotoImage(作ったものは覚えておく)。PNG がなければ None(文字だけで表示する)。"""
    key = (role, size, color)
    image = _cache.get(key)
    if image is not None:
        try:
            image.width()  # 作ったときのウィンドウ(Tk)が閉じられていたら、作り直す
            return image
        except (tk.TclError, RuntimeError):
            pass
    path = icon_path(role, size, color)
    if not path.exists():
        return None
    _cache[key] = tk.PhotoImage(file=str(path))
    return _cache[key]


def set_window_icon(window: tk.Misc) -> None:
    """ウィンドウ(タイトルバー・タスクバー)のアイコンを、ToreCan のアイコンにする。"""
    images = [image for size in SIZES if (image := icon("app", size, "accent")) is not None]
    if images:
        window.iconphoto(True, *images)


# ---- PNG の作成(開発用) ----

def build(font_path: Path, codepoints_path: Path) -> list[Path]:
    """フォントから、ICONS の全アイコンを SIZES・COLORS の組み合わせで PNG にする。作ったファイルを返す。"""
    from PIL import Image, ImageDraw, ImageFont

    codepoints = {}
    for line in codepoints_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            codepoints[parts[0]] = int(parts[1], 16)
    missing = [name for name in ICONS.values() if name not in codepoints]
    if missing:
        raise ValueError(f"フォントにないアイコンがあります: {', '.join(missing)}")
    ICON_DIR.mkdir(exist_ok=True)
    made = []
    scale = 4  # 大きく描いてから縮めて、縁をなめらかにする
    for name in dict.fromkeys(ICONS.values()):
        for size in SIZES:
            font = ImageFont.truetype(str(font_path), size * scale)
            mask = Image.new("L", (size * scale, size * scale), 0)
            ImageDraw.Draw(mask).text((0, 0), chr(codepoints[name]), font=font, fill=255)
            mask = mask.resize((size, size), Image.LANCZOS)
            for color_name, color in COLORS.items():
                image = Image.new("RGBA", (size, size), color)
                image.putalpha(mask)
                path = ICON_DIR / f"{name}_{color_name}_{size}.png"
                image.save(path)
                made.append(path)
    return made


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] != "build":
        print(__doc__)
        return 1
    made = build(Path(sys.argv[2]), Path(sys.argv[3]))
    print(f"{len(made)} 個の PNG を {ICON_DIR} に作りました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
