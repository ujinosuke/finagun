"""銘柄一覧の Excel ファイルを CSV に変換する。

listed_stocks フォルダにある Excel ファイル(例: T2610.xlsx)を読み込み、
同じフォルダに同じ名前の CSV ファイル(例: T2610.csv)を作成する。
同じ名前の CSV ファイルが既にある場合は、その Excel ファイルの処理をスキップする。

使い方:
    python core_listup_stocks.py              # listed_stocks 内の全 Excel ファイルを変換
    python core_listup_stocks.py T2610.xlsx   # 指定したファイルだけを変換
"""

import sys
from pathlib import Path

import pandas as pd

LISTED_STOCKS_DIR = Path(__file__).resolve().parent / "listed_stocks"
EXCEL_SUFFIXES = (".xls", ".xlsx")


def convert_to_csv(excel_path: Path) -> Path:
    """Excel ファイルの先頭シートを、同じ名前の CSV ファイルに書き出す。"""
    # コード(例: "130A")や業種コードの "-" を崩さないよう、すべて文字列として読み込む
    df = pd.read_excel(excel_path, sheet_name=0, dtype=str)
    csv_path = excel_path.with_suffix(".csv")
    # utf-8-sig にすると、Excel で開いても日本語が文字化けしない
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def find_excel_files(names: list[str]) -> list[Path]:
    """変換対象の Excel ファイルを返す。names が空なら listed_stocks 内の全ファイル。"""
    if names:
        return [LISTED_STOCKS_DIR / name for name in names]
    return sorted(
        p for p in LISTED_STOCKS_DIR.iterdir()
        if p.suffix.lower() in EXCEL_SUFFIXES and not p.name.startswith("~$")
    )


def main() -> int:
    if not LISTED_STOCKS_DIR.is_dir():
        print(f"フォルダが見つかりません: {LISTED_STOCKS_DIR}")
        return 1

    excel_files = find_excel_files(sys.argv[1:])
    if not excel_files:
        print(f"Excel ファイルがありません: {LISTED_STOCKS_DIR}")
        return 1

    failed = 0
    for excel_path in excel_files:
        if excel_path.with_suffix(".csv").exists():
            print(f"[スキップ] CSV が既にあります: {excel_path.with_suffix('.csv').name}")
            continue
        try:
            csv_path = convert_to_csv(excel_path)
        except FileNotFoundError:
            print(f"[失敗] ファイルが見つかりません: {excel_path.name}")
            failed += 1
        except ImportError as e:
            # 旧形式の .xls には xlrd が必要(pip install xlrd)
            print(f"[失敗] {excel_path.name}: {e}")
            failed += 1
        else:
            print(f"[完了] {excel_path.name} -> {csv_path.name}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
