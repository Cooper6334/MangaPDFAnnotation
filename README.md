# JPocr — 漫畫 OCR 翻譯工具

自動偵測漫畫中的日文文字框、執行 OCR，並透過 AI（Claude 或 Gemini）批次翻譯為繁體中文。提供圖形化編輯介面，支援 PDF 輸入與標注輸出。

---

## 功能

- **文字偵測**：以 YOLOv5 模型偵測漫畫文字框
- **日文 OCR**：使用 Manga-OCR 辨識日文
- **AI 翻譯**：支援 Claude 或 Gemini CLI，20 筆一批翻譯
- **術語表**：自訂日文→繁體中文術語，確保譯名一致
- **GUI 編輯**：逐筆檢視截圖、編輯原文與譯文
- **PDF 支援**：讀取 PDF 漫畫，翻譯後以便利貼 annotation 寫回原 PDF

---

## 安裝

### 1. 安裝 Python 套件

```bash
pip install -r requirements.txt
```

Windows 可直接雙擊 `init.bat`。

### 2. Clone comic-text-detector

```bash
git clone https://github.com/dmMaze/comic-text-detector.git
```

### 3. 下載偵測模型

從 [manga-image-translator releases](https://github.com/zyddnys/manga-image-translator/releases/tag/beta-0.2.1) 下載 `comictextdetector.pt.onnx`，放至：

```
comic-text-detector/data/comictextdetector.pt.onnx
```

### 4. 設定翻譯後端

**Claude**（預設）：安裝並登入 [Claude Code CLI](https://claude.ai/code)

**Gemini**（可選）：
```bash
npm install -g @google/gemini-cli
gemini auth login
```

---

## 使用方式

### GUI（推薦）

```bash
python app.py
```

| 步驟 | 操作 |
|------|------|
| 建立專案 | 點「建立新專案」選圖片資料夾，或「開啟PDF」選 PDF 檔 |
| 等待 OCR | 進度即時顯示於下方 log 區 |
| 選擇後端 | 工具列下拉選單切換 Claude / Gemini |
| 翻譯 | 勾選「需翻譯」的列後點「翻譯」 |
| 編輯 | 可直接修改日文原文或繁體中文譯文 |
| 儲存 | 點「儲存」寫回 `result_translated.txv` |
| 輸出 PDF | PDF 專案可點「輸出」，以便利貼 annotation 寫回原 PDF |

### 命令列

```bash
python main.py              # OCR + 翻譯
python main.py --no-translate  # 僅 OCR
```

---

## 術語表

編輯 `glossary.txt`，每行一筆，格式為：

```
日文原文<Tab>繁體中文譯文
でんさい	電子票據
```

以 `#` 開頭的行為註解，翻譯時會優先套用術語表。

---

## 輸出格式

| 檔案 | 說明 |
|------|------|
| `result.txv` | OCR 原始結果（TSV） |
| `result_translated.txv` | 含翻譯結果（TSV） |
| `original.pdf` | PDF 專案的來源複本（含便利貼標注後覆寫） |
| `*.jpg` | 各文字框截圖 |

---

## 相依套件

| 套件 | 用途 |
|------|------|
| manga-ocr | 日文 OCR |
| opencv-python | 影像處理 |
| Pillow | 影像操作 |
| torch | 深度學習推論 |
| PySide6 | GUI 框架 |
| pymupdf | PDF 處理 |
| anthropic | Claude API SDK |

---

## 專案結構

```
JPocr/
├── app.py                  # GUI 主程式
├── ocr.py                  # OCR 引擎（資料夾 / PDF）
├── save_translation.py     # 翻譯結果驗證儲存工具
├── translate-prompt.md     # 翻譯規則（Claude / Gemini 共用）
├── glossary.txt            # 術語表
├── requirements.txt
├── init.bat                # Windows 安裝腳本
├── comic-text-detector/    # 文字偵測模組（需另行 clone）
└── .claude/commands/       # Claude Code skill 定義
```
