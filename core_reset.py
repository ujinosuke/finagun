"""プログラムの保存ファイルの初期化(GUI を含まない部分)。ToreCan のホーム画面の設定から使う。

初期化は、保存ファイルを消すのではなく backup フォルダ(backup/<日時>_<対象>/)に移す
(間違えて初期化しても、ファイルを戻せば元に戻る)。
株価データ(stock_database)と銘柄一覧(listed_stocks)は初期化しない(取り直しに時間がかかるため)。

使い方(他のプログラムから):
    for target in RESET_TARGETS:
        print(target.label, existing_files(target))
    folder, moved = reset([target_by_key("watchlist")])
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core_settings import APP_DIR

BACKUP_DIR = APP_DIR / "backup"


@dataclass(frozen=True)
class ResetTarget:
    """初期化の対象 1 つ。"""
    key: str                  # ToreCan.py の PROGRAMS の key と同じ(起動中かを確かめるのに使う)。全体設定などは独自
    label: str
    description: str          # 何が消えるか
    patterns: tuple[str, ...]  # APP_DIR からの相対のファイル名(glob)。.tmp・.bak も含める


RESET_TARGETS = [
    ResetTarget("asset", "資産管理", "取引・保有・資産推移の記録・証券会社・画面の状態と、配当情報のキャッシュ",
                ("asset.json", "asset.json.tmp", "asset.json.bak", "dividend_cache.json", "dividend_cache.json.tmp")),
    ResetTarget("watchlist", "ウォッチリスト", "グループと登録した銘柄(チャートの図形は「チャート」で初期化)",
                ("watchlist.json", "watchlist.json.tmp", "watchlist.json.bak")),
    ResetTarget("compare", "銘柄比較", "グループと登録した銘柄・表示の設定と、業績のキャッシュ(保有資産は資産管理から作るので消えない)",
                ("compare.json", "compare.json.tmp", "financials_cache.json", "financials_cache.json.tmp")),
    ResetTarget("screening", "スクリーニング", "", ()),
    ResetTarget("backtest", "バックテスト", "", ()),
    ResetTarget("techtrainer", "テクニカル訓練", "", ()),
    ResetTarget("chart", "チャート", "全銘柄のチャートに描いた図形と、銘柄ごとの表示(charts フォルダ)",
                ("charts/*.json", "charts/*.json.tmp")),
    ResetTarget("settings", "全体設定", "色の決まり・ベンチマーク・相関の期間・データフォルダ・チャートの既定・描く色(既定に戻る)",
                ("settings.json", "settings.json.tmp", "settings.json.bak")),
    ResetTarget("logs", "ホームのログ", "ホームから起動したプログラムのエラー出力(logs フォルダ)", ("logs/*.log",)),
]
NOTHING_SAVED = "保存しているものはありません"


def target_by_key(key: str) -> ResetTarget:
    return next(t for t in RESET_TARGETS if t.key == key)


def existing_files(target: ResetTarget) -> list[Path]:
    """対象の保存ファイルのうち、今あるもの。"""
    files: list[Path] = []
    for pattern in target.patterns:
        files += [p for p in sorted(APP_DIR.glob(pattern)) if p.is_file()]
    return files


def reset(targets: list[ResetTarget], now: datetime | None = None) -> tuple[Path | None, list[Path]]:
    """対象の保存ファイルを backup フォルダに移す。(移した先のフォルダ, 移したファイル) を返す。

    移すファイルがなければ (None, [])。フォルダの中の構成(logs/ など)は保つ。
    """
    files = [path for target in targets for path in existing_files(target)]
    if not files:
        return None, []
    name = targets[0].key if len(targets) == 1 else "all"
    folder = BACKUP_DIR / f"{(now or datetime.now()):%Y%m%d-%H%M%S}_{name}"
    moved = []
    for path in files:
        destination = folder / path.relative_to(APP_DIR)
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.replace(destination)
        moved.append(destination)
    return folder, moved
