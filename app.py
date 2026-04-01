import sys
import csv
import json
import re
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QScrollArea, QLabel, QTextEdit, QFrame, QFileDialog,
    QMessageBox, QToolBar, QSizePolicy, QLineEdit, QPushButton,
    QCheckBox, QComboBox,
    # QButtonGroup, QRadioButton,  # 套用範圍選項暫時隱藏
)
from PySide6.QtGui import QPixmap, QFont, QAction, QColor, QPalette
from PySide6.QtWidgets import QStyle
from PySide6.QtCore import Qt, QThread, Signal


# ---------------------------------------------------------------------------
# Background worker: runs ocr.py as subprocess
# ---------------------------------------------------------------------------

class OcrWorker(QThread):
    """呼叫 ocr.py，即時將 stdout 轉發至 log signal；支援資料夾與 PDF 路徑。"""
    finished = Signal(str)   # result folder path
    error    = Signal(str)
    log      = Signal(str)   # 即時進度行

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        import subprocess
        script = Path(__file__).parent / "ocr.py"
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", str(script), self.path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # stderr 合併進 stdout 一起即時顯示
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.log.emit(line)
                if "Results saved to" in line:
                    folder = line.split("Results saved to")[-1].strip()
                    proc.wait()
                    self.finished.emit(folder)
                    return
            proc.wait()
            self.error.emit("OCR 結束但找不到輸出資料夾。")
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Background worker: calls `claude -p` to translate all JP texts at once
# ---------------------------------------------------------------------------

class TranslateWorker(QThread):
    """
    透過 `claude -p /translate-manga <input_json>` 呼叫 translate-manga skill。
    Skill 會讀取 glossary.txt、翻譯文字，並對困難項目加上 flag。

    Signal:
        finished(object)  dict: {(filename, index): {"translation": str, "flag": bool}}
        error(str)
    """
    finished = Signal(object)
    error = Signal(str)
    log   = Signal(str)

    def __init__(self, rows: list[tuple[str, str, str]], result_dir: Path,
                 backend: str = "claude"):
        """rows: [(filename, index, jp_text), ...]; backend: 'claude' | 'gemini'"""
        super().__init__()
        self.rows = rows
        self.result_dir = result_dir
        self.backend = backend

    BATCH_SIZE = 20  # 每批翻譯筆數

    def run(self):
        import subprocess

        input_path = self.result_dir / "translate_input.json"
        batches = [
            self.rows[i: i + self.BATCH_SIZE]
            for i in range(0, len(self.rows), self.BATCH_SIZE)
        ]
        total_batches = len(batches)
        all_translations: dict = {}

        for batch_idx, batch in enumerate(batches):
            self.log.emit(f"── 翻譯第 {batch_idx + 1}/{total_batches} 批（{len(batch)} 筆）[{self.backend}] ──")

            # ── 1. 寫入本批輸入 JSON ──────────────────────────────────────
            input_path.write_text(
                json.dumps(
                    [{"filename": f, "index": i, "text": t} for f, i, t in batch],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            output_path = self.result_dir / f"translate_output_{batch_idx}.json"
            output_path.unlink(missing_ok=True)

            # ── 2. 依 backend 呼叫翻譯指令 ───────────────────────────────
            if self.backend == "claude":
                items = self._run_claude(batch_idx, input_path, output_path)
            else:
                items = self._run_gemini(batch_idx, input_path)

            if items is None:
                return

            for item in items:
                all_translations[(item["filename"], str(item["index"]))] = {
                    "translation": item.get("translation", ""),
                    "ai_message":  item.get("ai_message", ""),
                }

            self.log.emit(f"[第 {batch_idx + 1} 批完成，累計 {len(all_translations)} 筆]")

        self.finished.emit(all_translations)

    # ── backend helpers ───────────────────────────────────────────────────

    def _run_claude(self, batch_idx: int, input_path: Path, output_path: Path):
        """呼叫 claude CLI，透過 skill 翻譯並儲存至 output_path。回傳 items 或 None。"""
        import subprocess
        try:
            proc = subprocess.Popen(
                [
                    "claude",
                    "--allowedTools", "Read,Write,Bash",
                    "--output-format", "stream-json",
                    "-p", f"/translate-manga {input_path} {output_path}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            self.error.emit("找不到 `claude` 指令。\n請確認 Claude Code CLI 已安裝並在 PATH 中。")
            return None

        output_lines: list[str] = []
        for raw_line in proc.stdout:
            raw_line = raw_line.rstrip()
            if not raw_line:
                continue
            output_lines.append(raw_line)
            try:
                event = json.loads(raw_line)
                etype = event.get("type", "")
                if etype == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            text = block["text"].strip()
                            if text:
                                self.log.emit(text)
                        elif block.get("type") == "tool_use":
                            self.log.emit(f"[工具呼叫] {block.get('name','?')}")
                elif etype == "result" and event.get("subtype") == "error":
                    self.log.emit(f"[錯誤] {event.get('error','')}")
            except json.JSONDecodeError:
                self.log.emit(raw_line)

        proc.wait()
        if proc.returncode != 0:
            self.error.emit(
                f"claude 指令失敗（第 {batch_idx + 1} 批，return code {proc.returncode}）：\n"
                + "\n".join(output_lines[-20:])
            )
            return None

        if not output_path.exists():
            self.error.emit(
                f"第 {batch_idx + 1} 批：找不到輸出檔案 {output_path.name}。\n\n"
                + "\n".join(output_lines[-20:])
            )
            return None

        try:
            return json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self.error.emit(f"第 {batch_idx + 1} 批 JSON 解析錯誤：{exc}")
            return None

    def _run_gemini(self, batch_idx: int, input_path: Path):
        """呼叫 gemini CLI，將 prompt+JSON 直接嵌入，從 stdout 解析結果。回傳 items 或 None。"""
        import subprocess

        # 讀取術語表
        glossary_path = Path(__file__).parent / "glossary.txt"
        glossary = ""
        if glossary_path.exists():
            lines = [
                l for l in glossary_path.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")
            ]
            glossary = "\n".join(lines) if lines else "（無）"
        else:
            glossary = "（無）"

        input_json = input_path.read_text(encoding="utf-8")
        rules = (Path(__file__).parent / "translate-prompt.md").read_text(encoding="utf-8")

        prompt = (
            f"以下是待翻譯的漫畫 OCR JSON 陣列：\n\n{input_json}\n\n"
            f"術語表：\n{glossary}\n\n"
            f"{rules}\n\n"
            "只輸出純 JSON 陣列，不要任何說明文字、不要 markdown 包裝。"
        )

        try:
            proc = subprocess.Popen(
                ["gemini", "-p", prompt],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            self.error.emit("找不到 `gemini` 指令。\n請確認 Gemini CLI 已安裝：npm install -g @google/gemini-cli")
            return None

        output_lines: list[str] = []
        for line in proc.stdout:
            line = line.rstrip()
            output_lines.append(line)
            if line:
                self.log.emit(line)

        proc.wait()
        if proc.returncode != 0:
            self.error.emit(
                f"gemini 指令失敗（第 {batch_idx + 1} 批，return code {proc.returncode}）：\n"
                + "\n".join(output_lines[-20:])
            )
            return None

        raw = "\n".join(output_lines)
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if not match:
            self.error.emit(
                f"第 {batch_idx + 1} 批（Gemini）無法解析 JSON 陣列。\n\n原始回應（前 800 字）：\n{raw[:800]}"
            )
            return None

        try:
            return json.loads(match.group())
        except json.JSONDecodeError as exc:
            self.error.emit(f"第 {batch_idx + 1} 批（Gemini）JSON 解析錯誤：{exc}")
            return None


# ---------------------------------------------------------------------------
# Clickable image label
# ---------------------------------------------------------------------------

class _ClickableImage(QLabel):
    def __init__(self, image_path: Path):
        super().__init__()
        self._image_path = image_path

    def mousePressEvent(self, event):
        if self._image_path.exists():
            import os
            os.startfile(str(self._image_path))


# ---------------------------------------------------------------------------
# Single data row widget
# ---------------------------------------------------------------------------

class RowWidget(QWidget):
    """
    每列佈局：
      編號 | 截圖 | 日文原文   | AI訊息     | 需翻譯 checkbox
                 | 繁體中文   |            | 已確認 checkbox
                                           | 重新OCR 按鈕（未實作）
    """

    IMG_W = 130
    IMG_H = 75   # 單格高度，兩格合計約 160px

    def __init__(
        self,
        filename: str,
        index: str,
        jp_text: str,
        zh_text: str,
        image_path: Path,
        ai_message: str = "",
        confirmed: bool = False,
        need_translate: bool = True,
    ):
        super().__init__()
        self.filename = filename
        self.index = index

        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(8)

        # ── 編號 ──────────────────────────────────────────────────────────
        idx_lbl = QLabel(index)
        idx_lbl.setFixedWidth(28)
        idx_lbl.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        idx_lbl.setStyleSheet("color:#666; font-size:12px; padding-top:6px;")
        outer.addWidget(idx_lbl)

        # ── 截圖（高度涵蓋兩列）─────────────────────────────────────────
        img_lbl = _ClickableImage(image_path)
        img_h = self.IMG_H * 2 + 4
        img_lbl.setFixedSize(self.IMG_W, img_h)
        img_lbl.setAlignment(Qt.AlignCenter)
        img_lbl.setStyleSheet(
            "border:1px solid #d0d0d0; background:#fafafa; border-radius:3px;"
        )
        if image_path.exists():
            pix = QPixmap(str(image_path)).scaled(
                self.IMG_W, img_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            img_lbl.setPixmap(pix)
            img_lbl.setCursor(Qt.PointingHandCursor)
        else:
            img_lbl.setText("無圖")
            img_lbl.setStyleSheet(img_lbl.styleSheet() + " color:#aaa;")
        outer.addWidget(img_lbl)

        # ── 日文 / 繁體中文（上下兩格）──────────────────────────────────
        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        self.jp_edit = QTextEdit()
        self.jp_edit.setPlainText(jp_text)
        self.jp_edit.setFixedHeight(self.IMG_H)
        self.jp_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        text_col.addWidget(self.jp_edit)

        self.zh_edit = QTextEdit()
        self.zh_edit.setPlainText(zh_text)
        self.zh_edit.setFixedHeight(self.IMG_H)
        self.zh_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        text_col.addWidget(self.zh_edit)

        outer.addLayout(text_col, 2)

        # ── AI訊息（只讀，有訊息時顯示黃底）────────────────────────────
        self.ai_edit = QTextEdit()
        self.ai_edit.setReadOnly(True)
        self.ai_edit.setPlainText(ai_message)
        self.ai_edit.setFixedHeight(self.IMG_H * 2 + 4)
        self.ai_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._update_ai_style(bool(ai_message))
        outer.addWidget(self.ai_edit, 1)

        # ── 需翻譯 / 已確認 checkbox + 重新OCR 按鈕 ─────────────────────
        right_col = QVBoxLayout()
        right_col.setSpacing(4)

        self.translate_cb = QCheckBox("需翻譯")
        self.translate_cb.setChecked(need_translate)
        self.translate_cb.setFixedWidth(72)
        right_col.addWidget(self.translate_cb)

        self.confirm_cb = QCheckBox("已確認")
        self.confirm_cb.setChecked(confirmed)
        self.confirm_cb.setFixedWidth(72)
        right_col.addWidget(self.confirm_cb)

        right_col.addStretch()

        btn_reocr = QPushButton("重新OCR")
        btn_reocr.setEnabled(False)   # 功能未實作
        btn_reocr.setFixedWidth(72)
        btn_reocr.setToolTip("重新OCR功能尚未實作")
        right_col.addWidget(btn_reocr)

        outer.addLayout(right_col)

    def _update_ai_style(self, has_message: bool):
        if has_message:
            self.ai_edit.setStyleSheet(
                "background:#fff8e1; color:#5d4037; border:1px solid #ffe082;"
                " border-radius:3px;"
            )
        else:
            self.ai_edit.setStyleSheet(
                "background:#f9f9f9; color:#bbb; border:1px solid #e0e0e0;"
                " border-radius:3px;"
            )

    # ── public helpers ────────────────────────────────────────────────────
    def get_row(self) -> tuple:
        """回傳 (filename, index, jp, zh, ai_message, confirmed, need_translate)。"""
        return (
            self.filename,
            self.index,
            self.jp_edit.toPlainText(),
            self.zh_edit.toPlainText(),
            self.ai_edit.toPlainText(),
            "1" if self.confirm_cb.isChecked() else "0",
            "1" if self.translate_cb.isChecked() else "0",
        )

    def set_translation(self, zh: str, ai_message: str = ""):
        self.zh_edit.setPlainText(zh)
        self.ai_edit.setPlainText(ai_message)
        self._update_ai_style(bool(ai_message))


# ---------------------------------------------------------------------------
# Section header (filename divider)
# ---------------------------------------------------------------------------

class SectionHeader(QFrame):
    def __init__(self, filename: str):
        super().__init__()
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background: #e8e8e8; border-radius: 4px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)

        lbl = QLabel(filename)
        font = QFont()
        font.setBold(True)
        lbl.setFont(font)
        layout.addWidget(lbl)
        layout.addStretch()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Manga OCR 編輯器")
        self.resize(1200, 750)

        self.result_dir: Path | None = None
        self.row_widgets: list[RowWidget] = []
        self._ocr_worker: OcrWorker | None = None
        self._translate_worker: TranslateWorker | None = None
        # key: (filename, index) → (page_num, pdf_x1, pdf_y1, pdf_x2, pdf_y2)
        self._coord_map: dict[tuple[str, str], tuple[int, float, float, float, float]] = {}

        self._build_toolbar()
        self._build_central()

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build_toolbar(self):
        tb = QToolBar("主工具列")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        style = self.style()

        act_src = QAction(style.standardIcon(QStyle.SP_DirOpenIcon), "建立新專案", self)
        act_src.setToolTip("選擇漫畫圖片資料夾，自動執行 OCR 並載入結果")
        act_src.triggered.connect(self._on_open_source)
        tb.addAction(act_src)

        act_pdf = QAction(style.standardIcon(QStyle.SP_FileIcon), "開啟PDF", self)
        act_pdf.setToolTip("選擇 PDF 漫畫檔，自動執行 OCR 並載入結果（支援輸出標注 PDF）")
        act_pdf.triggered.connect(self._on_open_pdf)
        tb.addAction(act_pdf)

        tb.addSeparator()

        act_result = QAction(style.standardIcon(QStyle.SP_DialogOpenButton), "讀取", self)
        act_result.setToolTip("直接開啟已有的 result 資料夾")
        act_result.triggered.connect(self._on_open_result)
        tb.addAction(act_result)

        tb.addSeparator()

        self.cmb_backend = QComboBox()
        self.cmb_backend.addItems(["Claude", "Gemini"])
        self.cmb_backend.setFixedWidth(90)
        self.cmb_backend.setToolTip("選擇翻譯方案")
        tb.addWidget(self.cmb_backend)

        self.act_translate = QAction(style.standardIcon(QStyle.SP_MessageBoxInformation), "翻譯", self)
        self.act_translate.setToolTip("將日文翻譯為繁體中文")
        self.act_translate.triggered.connect(self._on_translate)
        self.act_translate.setEnabled(False)
        tb.addAction(self.act_translate)

        self.act_save = QAction(style.standardIcon(QStyle.SP_DialogSaveButton), "儲存", self)
        self.act_save.setToolTip("將目前編輯內容寫回 result_translated.txv")
        self.act_save.triggered.connect(self._on_save)
        self.act_save.setEnabled(False)
        tb.addAction(self.act_save)

        self.act_export = QAction(style.standardIcon(QStyle.SP_ArrowForward), "輸出", self)
        self.act_export.setToolTip("將翻譯結果以便利貼 annotation 加回 original.pdf（僅 PDF 專案可用）")
        self.act_export.triggered.connect(self._on_export)
        self.act_export.setEnabled(False)
        tb.addAction(self.act_export)

        tb.addSeparator()


    def _build_central(self):
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 取代列 ──────────────────────────────────────────────────────
        bar = QFrame()
        bar.setStyleSheet("background:#f7f7f7; border-bottom:1px solid #ddd;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 6, 10, 6)
        bar_layout.setSpacing(8)

        bar_layout.addWidget(QLabel("取代："))
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("尋找文字 A")
        self.find_edit.setFixedWidth(200)
        bar_layout.addWidget(self.find_edit)

        bar_layout.addWidget(QLabel("→"))

        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("取代為文字 B")
        self.replace_edit.setFixedWidth(200)
        bar_layout.addWidget(self.replace_edit)

        # # 套用範圍：日文 / 中文 / 兩者（暫時隱藏，固定只套用中文）
        # self.radio_jp = QRadioButton("日文")
        # self.radio_zh = QRadioButton("中文")
        # self.radio_both = QRadioButton("兩者")
        # self.radio_zh.setChecked(True)
        # for r in (self.radio_jp, self.radio_zh, self.radio_both):
        #     bar_layout.addWidget(r)

        btn_replace = QPushButton("取代全部")
        btn_replace.clicked.connect(self._on_replace_all)
        bar_layout.addWidget(btn_replace)

        self.replace_result_lbl = QLabel("")
        self.replace_result_lbl.setStyleSheet("color:#888;")
        bar_layout.addWidget(self.replace_result_lbl)
        bar_layout.addStretch()

        outer.addWidget(bar)

        # ── 捲動區域（填滿剩餘空間）─────────────────────────────────────
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.vbox = QVBoxLayout(self.content)
        self.vbox.setAlignment(Qt.AlignTop)
        self.vbox.setSpacing(2)
        self.vbox.setContentsMargins(8, 8, 8, 8)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll, 1)   # stretch=1，填滿所有剩餘空間

        # ── log 區域（固定置底）──────────────────────────────────────────
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(100)
        self.log_edit.setStyleSheet(
            "background:#1e1e1e; color:#d4d4d4; font-family:Consolas,monospace;"
            " font-size:12px; border:none; border-top:1px solid #555;"
        )
        outer.addWidget(self.log_edit, 0)  # stretch=0，不擴張

        self.setCentralWidget(container)

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------

    def _start_ocr(self, path: str, label: str):
        self._set_status(f"{label}，請稍候…")
        self.act_save.setEnabled(False)
        self.act_export.setEnabled(False)
        self.act_translate.setEnabled(False)

        self._ocr_worker = OcrWorker(path)
        self._ocr_worker.finished.connect(self._on_ocr_done)
        self._ocr_worker.error.connect(self._on_ocr_error)
        self._ocr_worker.log.connect(self._log)
        self._ocr_worker.start()

    def _on_open_source(self):
        folder = QFileDialog.getExistingDirectory(self, "選擇漫畫圖片資料夾")
        if folder:
            self._start_ocr(folder, "OCR 執行中")

    def _on_open_pdf(self):
        pdf_file, _ = QFileDialog.getOpenFileName(
            self, "選擇漫畫 PDF", "", "PDF 檔案 (*.pdf)"
        )
        if pdf_file:
            self._start_ocr(pdf_file, "OCR 執行中（PDF）")

    def _on_ocr_done(self, result_folder: str):
        self._set_status("OCR 完成")
        self._load_result(Path(result_folder))

    def _on_ocr_error(self, msg: str):
        self._set_status("OCR 失敗")
        QMessageBox.critical(self, "OCR 錯誤", msg)

    def _on_open_result(self):
        default_dir = str(Path(__file__).parent / "results")
        folder = QFileDialog.getExistingDirectory(self, "選擇 result 資料夾", default_dir)
        if not folder:
            return
        self._load_result(Path(folder))

    def _on_translate(self):
        if not self.row_widgets:
            return

        # 收集勾選「需翻譯」且有日文內容的列
        rows_to_translate = [
            (rw.filename, rw.index, rw.jp_edit.toPlainText())
            for rw in self.row_widgets
            if rw.translate_cb.isChecked() and rw.jp_edit.toPlainText().strip()
        ]
        if not rows_to_translate:
            return

        self.act_translate.setEnabled(False)
        self.act_save.setEnabled(False)

        backend = self.cmb_backend.currentText().lower()
        self._set_status(f"翻譯中（共 {len(rows_to_translate)} 筆，{backend}）…")
        self._translate_worker = TranslateWorker(rows_to_translate, self.result_dir, backend)
        self._translate_worker.finished.connect(self._on_translate_done)
        self._translate_worker.error.connect(self._on_translate_error)
        self._translate_worker.log.connect(self._log)
        self._translate_worker.start()

    def _on_translate_done(self, translations):
        # translations: {(filename, index): {"translation": str, "flag": bool}}
        matched = 0
        flagged = 0
        for rw in self.row_widgets:
            key = (rw.filename, rw.index)
            if key in translations:
                item = translations[key]
                rw.set_translation(item["translation"], ai_message=item.get("ai_message", ""))
                matched += 1
                if item.get("ai_message"):
                    flagged += 1

        self.act_translate.setEnabled(True)
        self.act_save.setEnabled(True)
        self.act_export.setEnabled(True)
        flag_note = f"，{flagged} 筆有AI訊息" if flagged else ""
        self._set_status(f"翻譯完成（共 {matched} 筆{flag_note}）")

    def _on_translate_error(self, msg: str):
        self.act_translate.setEnabled(True)
        self.act_save.setEnabled(True)
        self.act_export.setEnabled(True)
        self._set_status("翻譯失敗")
        QMessageBox.critical(self, "翻譯錯誤", msg)

    def _on_replace_all(self):
        find = self.find_edit.text()
        replace = self.replace_edit.text()
        if not find:
            self.replace_result_lbl.setText("請輸入尋找文字")
            return

        apply_jp = False  # 固定只套用中文（日文 / 兩者 選項已隱藏）
        apply_zh = True

        count = 0
        for rw in self.row_widgets:
            if apply_jp:
                old = rw.jp_edit.toPlainText()
                new = old.replace(find, replace)
                if old != new:
                    rw.jp_edit.setPlainText(new)
                    count += old.count(find)
            if apply_zh:
                old = rw.zh_edit.toPlainText()
                new = old.replace(find, replace)
                if old != new:
                    rw.zh_edit.setPlainText(new)
                    count += old.count(find)

        if count:
            self.replace_result_lbl.setText(f"已取代 {count} 處")
            self.replace_result_lbl.setStyleSheet("color: #2a7a2a;")
        else:
            self.replace_result_lbl.setText("找不到符合文字")
            self.replace_result_lbl.setStyleSheet("color: #888;")

    def _on_save(self):
        if not self.result_dir:
            return
        out = self.result_dir / "result_translated.txv"
        has_coords = bool(self._coord_map)
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")
            header = ["圖檔檔名", "第幾筆", "文字內容", "翻譯結果", "AI訊息", "已確認", "需翻譯"]
            if has_coords:
                header += ["page_num", "pdf_x1", "pdf_y1", "pdf_x2", "pdf_y2"]
            writer.writerow(header)
            for rw in self.row_widgets:
                filename, index, jp, zh, ai_msg, confirmed, need_translate = rw.get_row()
                row = [filename, index, jp, zh, ai_msg, confirmed, need_translate]
                if has_coords:
                    coords = self._coord_map.get((rw.filename, rw.index))
                    if coords:
                        page_num, x1, y1, x2, y2 = coords
                        row += [page_num, f"{x1:.2f}", f"{y1:.2f}", f"{x2:.2f}", f"{y2:.2f}"]
                    else:
                        row += ["", "", "", "", ""]
                writer.writerow(row)
        self._set_status(f"已儲存 → {out.name}")

    def _on_export(self):
        if not self.result_dir:
            return

        original_pdf = self.result_dir / "original.pdf"
        if not original_pdf.exists():
            QMessageBox.warning(self, "無法輸出",
                                "此專案非 PDF 來源（找不到 original.pdf）。\n"
                                "請使用「開啟PDF」建立專案。")
            return

        if not self._coord_map:
            QMessageBox.warning(self, "無法輸出",
                                "沒有 PDF 座標資料。\n"
                                "請確認是否由「開啟PDF」建立的專案，且 result.txv 含有座標欄位。")
            return

        try:
            import fitz
        except ImportError:
            QMessageBox.critical(self, "缺少套件", "請先安裝 pymupdf：\npip install pymupdf")
            return

        try:
            doc = fitz.open(str(original_pdf))
            count = 0

            for rw in self.row_widgets:
                filename, index, jp, zh, ai_msg, confirmed, need_translate = rw.get_row()
                zh = zh.strip()
                if not zh:
                    continue

                coords = self._coord_map.get((filename, index))
                if not coords:
                    continue

                page_num, pdf_x1, pdf_y1, pdf_x2, pdf_y2 = coords
                page = doc[page_num]

                # 便利貼 annotation 置於文字框左上角
                point = fitz.Point(pdf_x1, pdf_y1)
                annot = page.add_text_annot(point, zh, icon="Note")
                annot.update()
                count += 1

            doc.saveIncr()   # 增量儲存回 original.pdf
            self._set_status(f"已輸出 {count} 筆便利貼標注 → {original_pdf.name}")
            QMessageBox.information(self, "輸出完成",
                                    f"已加入 {count} 筆便利貼標注\n→ {original_pdf}")

        except Exception as exc:
            QMessageBox.critical(self, "輸出錯誤", str(exc))

    # ------------------------------------------------------------------
    # Load & render
    # ------------------------------------------------------------------

    def _load_result(self, result_dir: Path):
        # prefer translated file if available
        txv = result_dir / "result_translated.txv"
        if not txv.exists():
            txv = result_dir / "result.txv"
        if not txv.exists():
            QMessageBox.warning(self, "錯誤", f"找不到 result.txv：\n{result_dir}")
            return

        self._coord_map = {}
        rows = []
        with open(txv, newline="", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader, [])
            col = {name: idx for idx, name in enumerate(header)}
            for row in reader:
                if len(row) < 3:
                    continue
                filename      = row[0]
                index         = row[1]
                jp            = row[2]
                zh            = row[col["翻譯結果"]]  if "翻譯結果" in col and len(row) > col["翻譯結果"]  else ""
                ai_msg        = row[col["AI訊息"]]    if "AI訊息"   in col and len(row) > col["AI訊息"]    else ""
                confirmed     = row[col["已確認"]] == "1" if "已確認" in col and len(row) > col["已確認"] else False
                need_translate = row[col["需翻譯"]] != "0" if "需翻譯" in col and len(row) > col["需翻譯"] else True
                if "page_num" in col:
                    pi = col["page_num"]
                    try:
                        if len(row) > pi + 4 and row[pi] != "":
                            self._coord_map[(filename, index)] = (
                                int(row[pi]),
                                float(row[pi + 1]), float(row[pi + 2]),
                                float(row[pi + 3]), float(row[pi + 4]),
                            )
                    except ValueError:
                        pass
                rows.append((filename, index, jp, zh, ai_msg, confirmed, need_translate))

        self.result_dir = result_dir
        self._render_rows(rows)
        self.act_save.setEnabled(True)
        self.act_export.setEnabled(bool(self._coord_map))
        self.act_translate.setEnabled(True)
        pdf_note = f"　含 {len(self._coord_map)} 筆PDF座標" if self._coord_map else ""
        self._set_status(f"{result_dir.name}　共 {len(rows)} 筆{pdf_note}")

    def _render_rows(self, rows: list[tuple[str, str, str, str]]):
        # clear previous content
        while self.vbox.count():
            item = self.vbox.takeAt(0)
            if w := item.widget():
                w.deleteLater()
        self.row_widgets.clear()


        current_file = None
        for filename, index, jp, zh, ai_msg, confirmed, need_translate in rows:
            if filename != current_file:
                current_file = filename
                self.vbox.addWidget(SectionHeader(filename))

            stem = Path(filename).stem
            img_path = self.result_dir / f"{stem}_{index}.jpg"
            rw = RowWidget(filename, index, jp, zh, img_path,
                           ai_message=ai_msg, confirmed=confirmed,
                           need_translate=need_translate)
            self.row_widgets.append(rw)
            self.vbox.addWidget(rw)

        self.vbox.addStretch()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, text: str, busy: bool = False):
        self._log(text)

    def _log(self, text: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_edit.append(f"[{ts}] {text}")
        # 自動捲到最底
        sb = self.log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
