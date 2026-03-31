import sys
import csv
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv()


def translate_text(client: anthropic.Anthropic, text: str) -> str:
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": f"將以下日文翻譯成繁體中文，只輸出翻譯結果，不要任何說明：\n{text}",
            }
        ],
    )
    return response.content[0].text.strip()


def translate_result(input_txv: str):
    input_path = Path(input_txv)
    if not input_path.exists():
        print(f"File not found: {input_txv}")
        return

    output_path = input_path.parent / "result_translated.txv"

    client = anthropic.Anthropic()

    with (
        open(input_path, newline="", encoding="utf-8") as fin,
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

            filename, index, text = row[0], row[1], row[2]
            print(f"Translating [{filename}] #{index}: {text}")

            translated = translate_text(client, text)
            print(f"  → {translated}")

            writer.writerow(row + [translated])

    print(f"\nDone! Translated results saved to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python translate.py <result.txv>")
        print("  result.txv : OCR 輸出的 txv 檔案路徑")
        sys.exit(1)

    translate_result(sys.argv[1])
