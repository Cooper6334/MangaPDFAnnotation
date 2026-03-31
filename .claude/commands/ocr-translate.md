執行漫畫 OCR 並翻譯日文文字為繁體中文。

## 步驟

### 1. 取得目標資料夾
若 $ARGUMENTS 有提供路徑則直接使用，否則請使用 AskUserQuestion 詢問使用者漫畫圖片的資料夾路徑。

### 2. 執行 OCR
使用 Bash 執行：
```
python C:/code/JPocr/ocr.py <資料夾路徑>
```
記錄輸出中 "Results saved to" 後面的路徑，這是輸出資料夾（例如 `C:/code/JPocr/result_20260331_143025`）。

### 3. 讀取 OCR 結果
讀取輸出資料夾中的 `result.txv`，解析所有資料列（跳過 header）。
每列格式為：`圖檔檔名 \t 第幾筆 \t 文字內容`

### 4. 翻譯
將每列的「文字內容」從日文翻譯成繁體中文。

### 5. 產生 translations.json
在輸出資料夾中建立 `translations.json`，格式如下：
```json
[
  {"filename": "page01.jpg", "index": "1", "translation": "翻譯結果"},
  ...
]
```

### 6. 執行 insert.py
使用 Bash 執行：
```
python C:/code/JPocr/insert.py <result.txv路徑> <translations.json路徑>
```

### 7. 完成
告知使用者輸出結果位於哪個資料夾，包含 `result.txv`（原始）與 `result_translated.txv`（含翻譯）。
