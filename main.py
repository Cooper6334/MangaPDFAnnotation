import sys
from pathlib import Path
from ocr import process_manga_folder
from translate import translate_result


def main():
    skip_translate = "--no-translate" in sys.argv

    print("=== 漫畫 OCR 翻譯工具 ===\n")

    # 輸入目標資料夾
    while True:
        folder = input("請輸入漫畫圖片資料夾路徑：").strip().strip('"')
        if Path(folder).is_dir():
            break
        print(f"  找不到資料夾：{folder}，請重新輸入。\n")

    # 執行 OCR
    print()
    output_dir = process_manga_folder(folder)

    if output_dir is None:
        return

    # 執行翻譯（除非傳入 --no-translate）
    if not skip_translate:
        print()
        translate_result(str(output_dir / "result.txv"))


if __name__ == "__main__":
    main()
