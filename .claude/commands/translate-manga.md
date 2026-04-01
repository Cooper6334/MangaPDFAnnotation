將漫畫 OCR 結果從日文翻譯成繁體中文，並對翻譯困難的項目加上備註。

$ARGUMENTS 的格式為：`<input_json_path> <output_path>`

## 步驟

1. 使用 Read 工具讀取 `C:/code/JPocr/translate-prompt.md`，取得翻譯規則。

2. 使用 Read 工具讀取 `C:/code/JPocr/glossary.txt`（若不存在則略過）。
   解析非空白、非 # 開頭的每行，格式為 `日文\t繁體中文`，建立術語對照表。

3. 使用 Read 工具讀取 `input_json_path` 指定的 JSON 檔案，取得待翻譯項目。

4. 依翻譯規則與術語表翻譯所有項目，組成 JSON 陣列（無說明文字、無 markdown 包裝）：
   ```
   [
     {"filename": "page01.jpg", "index": "1", "translation": "繁體中文翻譯", "ai_message": ""},
     ...
   ]
   ```

5. 使用 Write 工具將上述 JSON 寫入暫存檔（將 `output_path` 副檔名改為 `_temp.json`）。

6. 使用 Bash 工具執行驗證並儲存：
   ```
   python "C:/code/JPocr/save_translation.py" "<temp_path>" "<output_path>"
   ```
   成功時印出 `TRANSLATION_SAVED: N items → <output_path>`。
