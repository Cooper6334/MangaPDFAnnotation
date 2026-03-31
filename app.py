import sys
import csv
import json
import re
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QScrollArea, QLabel, QTextEdit, QFrame, QFileDialog,
    QMessageBox, QToolBar, QSizePolicy, QLineEdit, QPushButton,
    QCheckBox,
    # QButtonGroup, QRadioButton,  # 套用範圍選項暫時隱藏
)
from PySide6.QtGui import QPixmap, QFont, QAction, QColor, QPalette
from PySide6.QtWidgets import QStyle
from PySide6.QtCore import Qt, QThread, Signal


# ---------------------------------------------------------------------------
# Background worker: runs ocr.py as subprocess
# ---------------------------------------------------------------------------

class OcrWorker(QThread):
    finished = Signal(str)   # result folder path
    error = Signal(str)

    def __init__(self, folder_path: str):
        super().__init__()
        self.folder_path = folder_path

    def run(self):
        import subprocess
        script = Path(__file__).parent / "ocr.py"
        try:
            result = subprocess.run(
                [sys.executable, str(script), self.folder_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in result.stdout.splitlines():
                if "Results saved to" in line:
                    folder = line.split("Results saved to")[-1].strip()
                    self.finished.emit(folder)
                    return
            self.error.emit(result.stderr or "OCR 結束但找不到輸出資料夾。")
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

    def __init__(self, rows: list[tuple[str, str, str]], result_dir: Path):
        """rows: [(filename, index, jp_text), ...]"""
        super().__init__()
        self.rows = rows
        self.result_dir = result_dir

    def run(self):
        import subprocess

        # ── 1. 將待翻譯資料寫成 JSON 檔案，供 skill 讀取 ─────────────────
        input_path = self.result_dir / "translate_input.json"
        input_path.write_text(
            json.dumps(
                [{"filename": f, "index": i, "text": t} for f, i, t in self.rows],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # ── 2. 呼叫 translate-manga skill ────────────────────────────────
        try:
            result = subprocess.run(
                [
                    "claude",
                    "--allowedTools", "Read",
                    "-p", f"/translate-manga {input_path}",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        except FileNotFoundError:
            self.error.emit("找不到 `claude` 指令。\n請確認 Claude Code CLI 已安裝並在 PATH 中。")
            return
        except subprocess.TimeoutExpired:
            self.error.emit("claude 指令逾時（180 秒）。")
            return

        if result.returncode != 0:
            self.error.emit(
                f"claude 指令失敗（return code {result.returncode}）：\n"
                f"{result.stderr or result.stdout}"
            )
            return

        # ── 3. 解析 JSON 回應 ─────────────────────────────────────────────
        raw = result.stdout.strip()
        print(f"[TranslateWorker] claude raw output ({len(raw)} chars):\n{raw[:800]}")

        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            self.error.emit(
                f"無法從 claude 回應中解析 JSON 陣列。\n\n原始回應（前 800 字）：\n{raw[:800]}"
            )
            return

        try:
            items = json.loads(match.group())
        except json.JSONDecodeError as exc:
            self.error.emit(
                f"JSON 解析錯誤：{exc}\n\n原始回應（前 800 字）：\n{raw[:800]}"
            )
            return

        translations = {
            (item["filename"], str(item["index"])): {
                "translation": item.get("translation", ""),
                "ai_message": item.get("ai_message", ""),
            }
            for item in items
        }
        print(f"[TranslateWorker] parsed {len(translations)} translations")
        self.finished.emit(translations)


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
      編號 | 截圖 | 日文原文   | AI訊息     | 已確認 checkbox
                 | 繁體中文   |            | 重新OCR 按鈕（未實作）
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

        # ── 已確認 checkbox + 重新OCR 按鈕 ──────────────────────────────
        right_col = QVBoxLayout()
        right_col.setSpacing(4)

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
        """回傳 (filename, index, jp, zh, ai_message, confirmed)。"""
        return (
            self.filename,
            self.index,
            self.jp_edit.toPlainText(),
            self.zh_edit.toPlainText(),
            self.ai_edit.toPlainText(),
            "1" if self.confirm_cb.isChecked() else "0",
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

        tb.addSeparator()

        act_result = QAction(style.standardIcon(QStyle.SP_DialogOpenButton), "讀取", self)
        act_result.setToolTip("直接開啟已有的 result 資料夾")
        act_result.triggered.connect(self._on_open_result)
        tb.addAction(act_result)

        tb.addSeparator()

        self.act_translate = QAction(style.standardIcon(QStyle.SP_MessageBoxInformation), "翻譯", self)
        self.act_translate.setToolTip("透過 translate-manga skill 將日文翻譯為繁體中文")
        self.act_translate.triggered.connect(self._on_translate)
        self.act_translate.setEnabled(False)
        tb.addAction(self.act_translate)

        self.act_save = QAction(style.standardIcon(QStyle.SP_DialogSaveButton), "儲存", self)
        self.act_save.setToolTip("將目前編輯內容寫回 result_translated.txv")
        self.act_save.triggered.connect(self._on_save)
        self.act_save.setEnabled(False)
        tb.addAction(self.act_save)

        self.act_export = QAction(style.standardIcon(QStyle.SP_ArrowForward), "輸出", self)
        self.act_export.setToolTip("輸出功能尚未實作")
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

    def _on_open_source(self):
        folder = QFileDialog.getExistingDirectory(self, "選擇漫畫圖片資料夾")
        if not folder:
            return
        self._set_status("OCR 執行中，請稍候…", busy=True)
        self.act_save.setEnabled(False)
        self.act_export.setEnabled(False)
        self.act_translate.setEnabled(False)

        self._ocr_worker = OcrWorker(folder)
        self._ocr_worker.finished.connect(self._on_ocr_done)
        self._ocr_worker.error.connect(self._on_ocr_error)
        self._ocr_worker.start()

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

        # 收集所有日文文字
        rows_to_translate = [
            (rw.filename, rw.index, rw.jp_edit.toPlainText())
            for rw in self.row_widgets
            if rw.jp_edit.toPlainText().strip()
        ]
        if not rows_to_translate:
            return

        self._set_status(f"翻譯中（共 {len(rows_to_translate)} 筆）…", busy=True)
        self.act_translate.setEnabled(False)
        self.act_save.setEnabled(False)

        self._translate_worker = TranslateWorker(rows_to_translate, self.result_dir)
        self._translate_worker.finished.connect(self._on_translate_done)
        self._translate_worker.error.connect(self._on_translate_error)
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
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(["圖檔檔名", "第幾筆", "文字內容", "翻譯結果", "AI訊息", "已確認"])
            for rw in self.row_widgets:
                writer.writerow(rw.get_row())
        self._set_status(f"已儲存 → {out.name}")

    def _on_export(self):
        # TODO: 實作輸出功能
        pass

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

        rows: list[tuple[str, str, str, str, bool]] = []
        with open(txv, newline="", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader, [])
            has_zh        = len(header) >= 4
            has_ai        = len(header) >= 5
            has_confirmed = len(header) >= 6
            for row in reader:
                if len(row) < 3:
                    continue
                filename, index, jp = row[0], row[1], row[2]
                zh        = row[3] if has_zh        and len(row) >= 4 else ""
                ai_msg    = row[4] if has_ai        and len(row) >= 5 else ""
                confirmed = row[5] == "1" if has_confirmed and len(row) >= 6 else False
                rows.append((filename, index, jp, zh, ai_msg, confirmed))

        self.result_dir = result_dir
        self._render_rows(rows)
        self.act_save.setEnabled(True)
        self.act_export.setEnabled(True)
        self.act_translate.setEnabled(True)
        self._set_status(f"{result_dir.name}　共 {len(rows)} 筆")

    def _render_rows(self, rows: list[tuple[str, str, str, str]]):
        # clear previous content
        while self.vbox.count():
            item = self.vbox.takeAt(0)
            if w := item.widget():
                w.deleteLater()
        self.row_widgets.clear()


        current_file = None
        for filename, index, jp, zh, ai_msg, confirmed in rows:
            if filename != current_file:
                current_file = filename
                self.vbox.addWidget(SectionHeader(filename))

            stem = Path(filename).stem
            img_path = self.result_dir / f"{stem}_{index}.jpg"
            rw = RowWidget(filename, index, jp, zh, img_path,
                           ai_message=ai_msg, confirmed=confirmed)
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
