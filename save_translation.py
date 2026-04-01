"""
由 translate-manga skill 呼叫，驗證並儲存翻譯結果 JSON。

使用方式：
    python save_translation.py <temp_json_path> <output_path>

    temp_json_path : skill 用 Write 工具寫入的暫存 JSON 檔
    output_path    : 驗證通過後複製到此路徑
"""
import sys
import json
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        print("Usage: python save_translation.py <temp_json_path> <output_path>",
              file=sys.stderr)
        sys.exit(1)

    temp_path   = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not temp_path.exists():
        print(f"Error: temp file not found: {temp_path}", file=sys.stderr)
        sys.exit(1)

    raw = temp_path.read_text(encoding="utf-8")

    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"JSON parse error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(items, list):
        print("Error: expected a JSON array", file=sys.stderr)
        sys.exit(1)

    required = {"filename", "index", "translation"}
    for i, item in enumerate(items):
        missing = required - item.keys()
        if missing:
            print(f"Error: item[{i}] missing fields: {missing}", file=sys.stderr)
            sys.exit(1)

    output_path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"TRANSLATION_SAVED: {len(items)} items → {output_path}")


if __name__ == "__main__":
    main()
