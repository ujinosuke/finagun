"""チャートの状態(表示の設定と図形)を、銘柄ごとのファイルに保存する(GUI を含まない部分)。

仕様は chart-state-spec.md を参照。
保存先はルートの charts フォルダで、1 銘柄 1 ファイル(charts/7203.json)。git 管理外。
どのプログラムからチャートを開いても、同じ銘柄なら同じ図形・表示になるよう、gui_chart.py の ChartFrame が使う。
何も覚えることがない銘柄(図形がなく、表示も既定のまま)はファイルを作らない。

ファイルの形:
    {"version": 1, "code": "7203",
     "display": {"timeframe": "週足", "bollinger": false, "volume": true, "oscillator_on": true, "oscillator": "RSI",
                 "condition_on": false, "condition": "...", "crosshair": false},
     "drawings": [{"type": "trend", "color": "#2962ff", "points": [["2026-03-02", 3120.0], ...]}],
     "updated": "2026-11-18T22:10:00"}
display がない・欠けている項目は、ToreCan の設定のチャートの既定を使う(確かめるのは gui_chart の側)。
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from core_settings import APP_DIR

CHART_DIR = APP_DIR / "charts"
FORMAT_VERSION = 1
CODE_PATTERN = re.compile(r"[0-9A-Z]{1,10}")  # ファイル名に使えるコード(銘柄コード・ETF など)


def chart_path(code: str) -> Path | None:
    """銘柄のファイルの場所。ファイル名に使えないコードは None(保存しない)。"""
    return CHART_DIR / f"{code}.json" if CODE_PATTERN.fullmatch(code or "") else None


def load_chart(code: str) -> dict | None:
    """銘柄のファイルを読む。ない・壊れているときは None(既定の状態で表示する)。"""
    path = chart_path(code)
    if path is None or not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def chart_mtime(code: str) -> float | None:
    """銘柄のファイルの更新時刻(ほかのウィンドウが書き換えたかを確かめるのに使う)。ファイルがなければ None。"""
    path = chart_path(code)
    try:
        return path.stat().st_mtime if path is not None else None
    except OSError:
        return None


def save_chart(code: str, display: dict | None, drawings: list[dict]) -> None:
    """銘柄のファイルに保存する。.tmp に書いて置き換える(途中で止まっても元のファイルが壊れない)。"""
    path = chart_path(code)
    if path is None:
        return
    CHART_DIR.mkdir(exist_ok=True)
    data = {"version": FORMAT_VERSION, "code": code}
    if display is not None:
        data["display"] = display
    data["drawings"] = drawings
    data["updated"] = datetime.now().isoformat(timespec="seconds")
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(temporary, path)


def delete_chart(code: str) -> None:
    """銘柄のファイルを消す(図形がなく、表示も既定に戻ったとき)。"""
    path = chart_path(code)
    if path is not None and path.exists():
        path.unlink()


def migrate_drawings(drawings_by_code: dict) -> int:
    """前の版の watchlist.json にまとめて入っていた図形を、銘柄ごとのファイルに書き出す。

    ファイルがまだない銘柄だけ書く(表示は書かない = 既定)。書いた銘柄の数を返す。
    """
    count = 0
    for code, drawings in drawings_by_code.items() if isinstance(drawings_by_code, dict) else []:
        if not isinstance(code, str) or not isinstance(drawings, list) or not drawings:
            continue
        if chart_path(code) is None or load_chart(code) is not None:
            continue
        save_chart(code, None, drawings)
        count += 1
    return count
