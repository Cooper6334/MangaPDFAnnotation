import sys
import csv
import shutil
import warnings
warnings.filterwarnings("ignore", category=UserWarning, message="pkg_resources is deprecated")
from datetime import datetime
from pathlib import Path
from PIL import Image
import cv2
import numpy as np

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
PDF_ZOOM = 2.0  # 渲染倍率（72 DPI × 2 = 144 DPI）；座標換算：pdf_coord = pixel / PDF_ZOOM


def process_manga_pdf(pdf_path: str):
    """讀取 PDF，逐頁渲染後執行 OCR，並在 result.txv 中儲存 PDF 原生座標。"""
    try:
        import fitz
    except ImportError:
        print("錯誤：請先安裝 pymupdf：pip install pymupdf")
        return None

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        print(f"找不到 PDF 檔案：{pdf_path}")
        return None

    if not MODEL_PATH.exists():
        print(f"Model not found: {MODEL_PATH}")
        return None

    doc = fitz.open(str(pdf_path))
    mat = fitz.Matrix(PDF_ZOOM, PDF_ZOOM)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent / "results" / f"result_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 複製 PDF 到 result 資料夾，輸出時直接在此檔加上 annotation
    shutil.copy2(str(pdf_path), str(output_dir / "original.pdf"))

    output_txv = output_dir / "result.txv"

    print("Loading models...")
    detector = TextDetector(model_path=str(MODEL_PATH), input_size=1024, device="cpu")
    mocr = MangaOcr()

    pdf_stem = pdf_path.stem

    with open(output_txv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow([
            "圖檔檔名", "第幾筆", "文字內容", "翻譯結果", "AI訊息", "已確認",
            "page_num", "pdf_x1", "pdf_y1", "pdf_x2", "pdf_y2",
        ])

        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(matrix=mat)
            img_pil = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

            page_label = f"{pdf_stem}_p{page_num + 1:03d}"
            print(f"Processing page {page_num + 1}/{len(doc)}...")

            _, _, blk_list = detector(img_cv, refine_mode=REFINEMASK_INPAINT)

            if not blk_list:
                print(f"  No text detected")
                continue

            for i, blk in enumerate(blk_list, start=1):
                x1, y1, x2, y2 = (int(v) for v in blk.xyxy)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(img_pil.width, x2), min(img_pil.height, y2)

                if x2 <= x1 or y2 <= y1:
                    continue

                cropped = img_pil.crop((x1, y1, x2, y2))
                text = mocr(cropped).strip()

                if text:
                    # 像素座標 → PDF 原生座標（pt）
                    px1, py1 = x1 / PDF_ZOOM, y1 / PDF_ZOOM
                    px2, py2 = x2 / PDF_ZOOM, y2 / PDF_ZOOM

                    writer.writerow([
                        f"{page_label}.jpg", i, text, "", "", "0",
                        page_num,
                        f"{px1:.2f}", f"{py1:.2f}", f"{px2:.2f}", f"{py2:.2f}",
                    ])
                    print(f"  [{i}] {text}")

                    cropped.save(output_dir / f"{page_label}_{i}.jpg")

    print(f"\nDone! Results saved to {output_dir}")
    return output_dir


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

    # 建立帶時間戳記的輸出資料夾（統一放在 results/ 下）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(__file__).parent / "results" / f"result_{timestamp}"
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
        print("Usage: python ocr.py <folder_path>")
        print("       python ocr.py <file.pdf>")
        sys.exit(1)

    target = Path(sys.argv[1])
    if target.suffix.lower() == ".pdf":
        process_manga_pdf(sys.argv[1])
    else:
        process_manga_folder(sys.argv[1])
