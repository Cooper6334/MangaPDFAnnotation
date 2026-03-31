"""
insert.py — 將翻譯結果寫入 txv 檔案

用法：
    python insert.py <txv_file> <translations_json_file>

translations_json_file 格式：
    [
        {"filename": "page01.jpg", "index": "1", "translation": "翻譯結果"},
        ...
    ]

輸出：在同一資料夾產生 result_translated.txv
"""

import sys
import csv
import json
from pathlib import Path


def insert_translations(txv_path: str, translations_json_path: str):
    txv = Path(txv_path)
    if not txv.exists():
        print(f"File not found: {txv_path}")
        return

    with open(translations_json_path, encoding="utf-8") as f:
        translations_list = json.load(f)

    # 建立 (filename, index) -> translation 的查找表
    lookup = {
        (item["filename"], str(item["index"])): item["translation"]
        for item in translations_list
    }

    output_path = txv.parent / "result_translated.txv"

    with (
        open(txv, newline="", encoding="utf-8") as fin,
        open(output_path, "w", newline="", encoding="utf-8") as fout,
    ):
        reader = csv.reader(fin, delimiter="\t")
        writer = csv.writer(fout, delimiter="\t")

        header = next(reader)
        writer.writerow(header + ["翻譯結果"])

        for row in reader:
            if len(row) < 3:
                writer.writerow(row + [""])
                continue

            filename, index = row[0], row[1]
            translation = lookup.get((filename, index), "")
            writer.writerow(row + [translation])

    print(f"Done! Saved to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python insert.py <txv_file> <translations_json_file>")
        sys.exit(1)

    insert_translations(sys.argv[1], sys.argv[2])
