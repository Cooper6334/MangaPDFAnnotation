import sys
import csv
from datetime import datetime
from pathlib import Path
from PIL import Image
import cv2

# Fix console encoding for Japanese characters on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 將 comic-text-detector 加入 Python 路徑
DETECTOR_DIR = Path(__file__).parent / "comic-text-detector"
sys.path.insert(0, str(DETECTOR_DIR))

from inference import TextDetector
from utils.textmask import REFINEMASK_INPAINT
from manga_ocr import MangaOcr

MODEL_PATH = DETECTOR_DIR / "data" / "comictextdetector.pt.onnx"


def process_manga_folder(folder_path: str):
    folder = Path(folder_path)
    jpg_files = sorted(folder.glob("*.[jJ][pP][gG]"))

    if not jpg_files:
        print(f"No JPG files found in {folder_path}")
        return None

    if not MODEL_PATH.exists():
        print(f"Model not found: {MODEL_PATH}")
        print("Please download comictextdetector.pt.onnx from:")
        print("https://github.com/zyddnys/manga-image-translator/releases/tag/beta-0.2.1")
        print(f"and place it in: {MODEL_PATH.parent}")
        return None

    # 建立帶時間戳記的輸出資料夾
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent / f"result_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_txv = output_dir / "result.txv"

    print("Loading models...")
    detector = TextDetector(
        model_path=str(MODEL_PATH),
        input_size=1024,
        device="cpu",
    )
    mocr = MangaOcr()

    with open(output_txv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["圖檔檔名", "第幾筆", "文字內容"])

        for jpg_path in jpg_files:
            print(f"Processing {jpg_path.name}...")

            img_cv = cv2.imread(str(jpg_path))
            if img_cv is None:
                print(f"  Failed to load {jpg_path.name}, skipping.")
                continue

            _, _, blk_list = detector(img_cv, refine_mode=REFINEMASK_INPAINT)

            if not blk_list:
                print(f"  No text detected in {jpg_path.name}")
                continue

            img_pil = Image.open(jpg_path).convert("RGB")
            stem = jpg_path.stem  # 檔名去掉副檔名

            for i, blk in enumerate(blk_list, start=1):
                x1, y1, x2, y2 = (int(v) for v in blk.xyxy)
                x1 = max(0, x1)
                y1 = max(0, y1)
                x2 = min(img_pil.width, x2)
                y2 = min(img_pil.height, y2)

                if x2 <= x1 or y2 <= y1:
                    continue

                cropped = img_pil.crop((x1, y1, x2, y2))
                text = mocr(cropped).strip()

                if text:
                    writer.writerow([jpg_path.name, i, text])
                    print(f"  [{i}] {text}")

                    # 儲存文字框截圖
                    crop_filename = output_dir / f"{stem}_{i}.jpg"
                    cropped.save(crop_filename)

    print(f"\nDone! Results saved to {output_dir}")
    return output_dir


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py <folder_path>")
        print("  folder_path : 存放 JPG 的資料夾路徑")
        sys.exit(1)

    process_manga_folder(sys.argv[1])
