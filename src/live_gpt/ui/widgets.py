"""Text widgets for the transcript and rendered replies."""
from __future__ import annotations

import re
import webbrowser

from PySide6.QtCore import QPoint, QRect, QSize, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QPalette,
    QRegion,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QWidget,
)

from ..localization import localization, tr
from ..reply_images import ImagePreviewDialog, ReplyImageLoader, ReplyImageMarkup
from ..response_content import reply_code_blocks
from .common import CHECK_ICON_PATH, COPY_ICON_PATH, EXIT_ICON_PATH, SEND_ICON_PATH
from .theme import PALETTE, themed


_COPY_BUTTON_STYLE = themed("""
    QPushButton { padding: 0; min-width: 0; min-height: 0;
        color: $text; background: transparent;
        border: none; border-radius: 10px; }
    QPushButton:hover { background: $button_flat_hover; }
""")


class LinkTextEdit(QPlainTextEdit):
    """Selectable plain text with clickable HTTP(S) and Markdown links."""

    def _link_at(self, position: QPoint) -> str | None:
        offset = self.cursorForPosition(position).position()
        text = self.toPlainText()
        for match in re.finditer(r"\[([^\]]+)\]\((https?://[^\s)]+)\)|https?://[^\s<>]+", text):
            if match.start() <= offset < match.end():
                return (match.group(2) or match.group()).rstrip(".,;:!?)\"'")
        for label, url in getattr(self, "response_links", ()):
            for match in re.finditer(re.escape(label), text):
                if match.start() <= offset < match.end():
                    return url
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._link_press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        position = event.position().toPoint()
        if (event.button() == Qt.MouseButton.LeftButton
                and (position - getattr(self, "_link_press", position)).manhattanLength() < 4
                and not self.textCursor().hasSelection()):
            link = self._link_at(position)
            if link:
                QDesktopServices.openUrl(QUrl(link))


class TranscriptEditor(LinkTextEdit):
    """Editable transcript with contextual actions inside the input."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._response_mode = False
        self._response_complete = False
        self._full_response_text = ""
        self._screenshot_selected = False

        self.clear_button = QPushButton(self)
        self.clear_button.setObjectName("clearButton")
        self.clear_button.setIcon(QIcon(str(EXIT_ICON_PATH)))
        self.clear_button.setIconSize(QSize(16, 16))
        self.clear_button.setFixedSize(32, 32)
        self.clear_button.setAccessibleName(tr("Delete text"))
        self.clear_button.setToolTip(tr("Delete text"))

        self.send_button = QPushButton(self)
        self.send_button.setObjectName("sendButton")
        self.send_button.setIcon(QIcon(str(SEND_ICON_PATH)))
        self.send_button.setIconSize(QSize(16, 16))
        self.send_button.setFixedSize(32, 32)
        self.send_button.setAccessibleName(tr("Send"))
        self.send_button.setToolTip(tr("Send"))

        self.send_without_screenshot_button = QPushButton(
            tr("No Screenshot"),
            self,
        )
        self.send_without_screenshot_button.setObjectName(
            "sendWithoutScreenshotButton"
        )
        self.send_without_screenshot_button.setIcon(
            QIcon(str(SEND_ICON_PATH))
        )
        self.send_without_screenshot_button.setIconSize(QSize(16, 16))
        self.send_without_screenshot_button.setFixedSize(132, 32)
        self.send_without_screenshot_button.setAccessibleName(
            tr("Send without screenshot")
        )
        self.send_without_screenshot_button.setToolTip(
            tr("Send the text without the selected screenshot")
        )

        self.textChanged.connect(self._sync_action_visibility)
        self._sync_action_visibility()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_action_buttons()

    def _position_action_buttons(self) -> None:
        margin = 8
        spacing = 6
        y = self.height() - self.send_button.height() - margin
        right = self.width() - margin
        for button in (
            self.send_button,
            self.send_without_screenshot_button,
            self.clear_button,
        ):
            if button.isHidden():
                continue
            right -= button.width()
            button.move(right, y)
            button.raise_()
            right -= spacing

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._response_mode:
            self.begin_composing(preserve_text=True)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        is_enter = event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        )
        wants_newline = bool(
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        )
        if is_enter and not wants_newline and not self._response_mode:
            if self.toPlainText().strip() or self._screenshot_selected:
                self.send_button.click()
            event.accept()
            return
        super().keyPressEvent(event)

    @property
    def is_showing_response(self) -> bool:
        return self._response_mode

    @property
    def is_response_complete(self) -> bool:
        return self._response_complete

    def begin_response(self) -> None:
        self.response_links = ()
        self._response_mode = True
        self._response_complete = False
        self._full_response_text = ""
        self.setReadOnly(True)
        self.clear()
        self._sync_action_visibility()

    def update_response(self, text: str) -> None:
        if not self._response_mode:
            self.begin_response()
        if self.toPlainText() == text:
            return
        self._full_response_text = text
        self.setPlainText(text)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def finish_response(self) -> None:
        self._response_complete = True

    def begin_reading(self) -> None:
        self._response_mode = True
        self._response_complete = False
        self.setReadOnly(True)
        self.clear()

    def finish_reading(self) -> None:
        self.setPlainText(self._full_response_text)
        self._response_complete = True

    def begin_composing(self, *, preserve_text: bool = False) -> None:
        text = self.toPlainText() if preserve_text else ""
        if not preserve_text:
            self.response_links = ()
        self._response_mode = False
        self._response_complete = False
        self._full_response_text = ""
        self.setReadOnly(False)
        self.setPlainText(text)
        self._sync_action_visibility()

    def set_screenshot_selected(self, selected: bool) -> None:
        self._screenshot_selected = selected
        if selected:
            self.send_button.setText(tr("With Screenshot"))
            self.send_button.setFixedSize(144, 32)
            self.send_button.setAccessibleName(tr("Send with screenshot"))
            self.send_button.setToolTip(
                tr("Send the message with the selected screenshot")
            )
        else:
            self.send_button.setText("")
            self.send_button.setFixedSize(32, 32)
            self.send_button.setAccessibleName(tr("Send"))
            self.send_button.setToolTip(tr("Send the message"))
        self._sync_action_visibility()
        self._position_action_buttons()

    def set_hint(self, message: str, *, error: bool = False) -> None:
        self.setPlaceholderText(message)
        color = QColor(PALETTE["error_hint" if error else "text_muted"])
        palette = self.palette()
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            palette.setColor(group, QPalette.ColorRole.PlaceholderText, color)
        self.setPalette(palette)
        self.viewport().update()

    def _sync_action_visibility(self) -> None:
        has_text = bool(self.toPlainText().strip()) and not self._response_mode
        can_send = (
            not self._response_mode
            and (has_text or self._screenshot_selected)
        )
        self.clear_button.setVisible(has_text)
        self.send_button.setVisible(can_send)
        self.send_without_screenshot_button.setVisible(
            has_text and self._screenshot_selected
        )
        visible_buttons = [
            button
            for button in (
                self.clear_button,
                self.send_without_screenshot_button,
                self.send_button,
            )
            if not button.isHidden()
        ]
        right_margin = sum(button.width() for button in visible_buttons)
        if visible_buttons:
            right_margin += 8 + 6 * (len(visible_buttons) - 1)
        self.setViewportMargins(0, 0, right_margin, 0)
        self._position_action_buttons()


class ReplyDisplay(QTextBrowser):
    """Read-only HTML reply with links opened in a new browser tab."""

    image_layout_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._images: dict[str, tuple[str, str, QSize]] = {}
        self._image_loader = ReplyImageLoader(self)
        self._image_loader.ready.connect(self._image_loaded)
        self._image_preview: ImagePreviewDialog | None = None
        self._image_resize_timer = QTimer(self)
        self._image_resize_timer.setSingleShot(True)
        self._image_resize_timer.timeout.connect(self._resize_images)
        self._code_blocks: list[tuple[str, str]] = []
        self._copy_buttons: list[QPushButton] = []
        self._code_cards: list[QFrame] = []
        self._copy_timers: list[QTimer] = []
        self._code_positions: dict[int, int] = {}
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._open_link)
        self.document().setDefaultStyleSheet(themed("""
            p { margin-top: 0; margin-bottom: 12px; }
            a { color: $link; text-decoration: underline; }
            th { background-color: $table_header; font-weight: bold; }
            td, th { padding: 6px; }
            pre { white-space: pre-wrap; }
            code { font-family: Consolas, monospace; }
            blockquote { margin-left: 16px; color: $text_quote; }
        """))
        self.viewport().setObjectName("replyViewport")
        self.viewport().setStyleSheet("QWidget#replyViewport { background: transparent; }")
        self.verticalScrollBar().valueChanged.connect(self._position_copy_buttons)
        self.horizontalScrollBar().valueChanged.connect(self._position_copy_buttons)
        self._copy_position_timer = QTimer(self)
        self._copy_position_timer.setSingleShot(True)
        self._copy_position_timer.timeout.connect(self._position_copy_buttons)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._copy_position_timer.start(0)
        )
        localization.changed.connect(self._translate_copy_controls)

    def _translate_copy_controls(self) -> None:
        for button, timer in zip(self._copy_buttons, self._copy_timers):
            self._set_copy_feedback(button, timer.isActive())

    @staticmethod
    def _set_copy_feedback(button: QPushButton, copied: bool) -> None:
        button.setText("")
        button.setIcon(QIcon(str(CHECK_ICON_PATH if copied else COPY_ICON_PATH)))
        label = tr("Copied!") if copied else tr("Copy code")
        button.setToolTip(label)
        button.setAccessibleName(label)

    def setHtml(self, html: str) -> None:  # noqa: N802
        formatted, self._code_blocks = reply_code_blocks(html, tr("Plain text"))
        images = ReplyImageMarkup()
        images.feed(formatted)
        images.close()
        self._images = images.images
        # Qt retains resources added by asynchronous downloads across setHtml.
        # Positional image names can now refer to a different reply's source.
        # Replace them before layout can reuse an old thumbnail and skip loading.
        for name in self._images:
            url = QUrl(name)
            resource_type = QTextDocument.ResourceType.ImageResource
            self.document().addResource(resource_type, url, self.loadResource(resource_type, url))
        super().setHtml("".join(images.parts))
        self._resize_images()
        while len(self._copy_buttons) > len(self._code_blocks):
            self._copy_timers.pop().stop()
            button = self._copy_buttons.pop()
            button.hide()
            button.deleteLater()
            card = self._code_cards.pop()
            card.hide()
            card.deleteLater()
        while len(self._copy_buttons) < len(self._code_blocks):
            index = len(self._copy_buttons)
            card = QFrame(self)
            card.setObjectName("replyCodeCard")
            card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            card.setStyleSheet(themed("background-color: $code_card; border: none; border-radius: 18px;"))
            card.lower()
            self._code_cards.append(card)
            button = QPushButton(self.viewport())
            button.setObjectName("replyCodeCopyButton")
            button.setFixedSize(36, 36)
            button.setIconSize(QSize(22, 22))
            button.setAccessibleName(tr("Copy code"))
            button.setToolTip(tr("Copy code"))
            button.setStyleSheet(_COPY_BUTTON_STYLE)
            button.clicked.connect(lambda _checked=False, index=index: self._copy_code(index))
            timer = QTimer(button)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda button=button: self._set_copy_feedback(button, False))
            self._copy_buttons.append(button)
            self._copy_timers.append(timer)
        for button, timer in zip(self._copy_buttons, self._copy_timers):
            timer.stop()
            self._set_copy_feedback(button, False)
        self._code_positions = {}
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    for name in fragment.charFormat().anchorNames():
                        if name.startswith("live-gpt-code-"):
                            self._code_positions.setdefault(int(name.removeprefix("live-gpt-code-")), fragment.position())
                iterator += 1
            block = block.next()
        self._position_copy_buttons()

    def _copy_code(self, index: int) -> None:
        if 0 <= index < len(self._code_blocks):
            QApplication.clipboard().setText(self._code_blocks[index][1])
            self._set_copy_feedback(self._copy_buttons[index], True)
            self._copy_timers[index].start(1500)

    def _position_copy_buttons(self, *_args: object) -> None:
        for index, button in enumerate(getattr(self, "_copy_buttons", ())):
            card = self._code_cards[index]
            position = self._code_positions.get(index)
            if position is None:
                button.hide()
                card.hide()
                continue
            cursor = QTextCursor(self.document())
            cursor.setPosition(position)
            table = cursor.currentTable()
            if table is None:
                button.hide()
                card.hide()
                continue
            bounds = self.document().documentLayout().frameBoundingRect(table)
            header_bounds = self.cursorRect(cursor)
            header_format = table.cellAt(0, 0).format().toTableCellFormat()
            last_cell = table.cellAt(table.rows() - 1, 0)
            code_end = self.cursorRect(last_cell.lastCursorPosition())
            code_format = last_cell.format().toTableCellFormat()
            left = header_bounds.left() - int(header_format.leftPadding())
            top = header_bounds.top() - int(header_format.topPadding())
            height = code_end.bottom() + 1 + int(code_format.bottomPadding()) - top
            code_bounds = QRect(left, top, int(bounds.width()), height)
            viewport_origin = self.viewport().pos()
            card_bounds = code_bounds.translated(viewport_origin)
            card.setGeometry(card_bounds)
            visible_bounds = self.viewport().geometry().translated(-card.pos())
            card.setMask(QRegion(card.rect()).intersected(QRegion(visible_bounds)))
            card.setVisible(self.viewport().geometry().intersects(card_bounds))
            x = min(code_bounds.right() - button.width() - 16,
                    self.viewport().width() - button.width() - 4)
            y = self.cursorRect(cursor).top() - 8
            button.move(max(4, x), y)
            button.setVisible(y + button.height() > 0 and y < self.viewport().height())
            button.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_copy_buttons()
        if hasattr(self, "_image_resize_timer"):
            self._image_resize_timer.start(0)

    def loadResource(self, resource_type: int, name: QUrl):  # noqa: N802
        if resource_type == QTextDocument.ResourceType.ImageResource:
            image = self._images.get(name.toString())
            if image is None:
                return None
            source, _label, size = image
            loaded = self._image_loader.request(source)
            return loaded if loaded is not None else self._image_loader.placeholder(
                source, size.scaled(QSize(280, 190), Qt.AspectRatioMode.KeepAspectRatio))
        return super().loadResource(resource_type, name)

    def _image_loaded(self, source: str) -> None:
        for name, (url, _label, size) in self._images.items():
            if url == source:
                image = self._image_loader.cache.get(source)
                self.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(name),
                    image if image is not None else self._image_loader.placeholder(
                        source, size.scaled(QSize(280, 190), Qt.AspectRatioMode.KeepAspectRatio)))
        self._resize_images()
        self.document().markContentsDirty(0, self.document().characterCount())
        self.image_layout_changed.emit()

    def _resize_images(self) -> None:
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    image_format = fragment.charFormat().toImageFormat()
                    info = self._images.get(image_format.name())
                    if info is not None:
                        source, _label, original_size = info
                        image = self._image_loader.cache.get(source)
                        cursor = QTextCursor(self.document())
                        cursor.setPosition(fragment.position())
                        table = cursor.currentTable()
                        columns = table.columns() if table else 1
                        width = min(280, max(40, (self.viewport().width() - 32) // columns - 16))
                        size = (image.size() if image is not None else original_size).scaled(
                            QSize(width, 190), Qt.AspectRatioMode.KeepAspectRatio)
                        if image_format.width() != size.width() or image_format.height() != size.height():
                            image_format.setWidth(size.width())
                            image_format.setHeight(size.height())
                            cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                            cursor.setCharFormat(image_format)
                iterator += 1
            block = block.next()

    def _open_link(self, url: QUrl) -> None:
        if url.toString() in self._images:
            source, label, _size = self._images[url.toString()]
            if self._image_preview is not None:
                self._image_preview.close()
            preview = ImagePreviewDialog(self._image_loader, source, label, self)
            self._image_preview = preview
            preview.finished.connect(lambda _result: setattr(self, "_image_preview", None))
            preview.show()
            return
        if url.scheme().lower() in ("http", "https"):
            webbrowser.open_new_tab(url.toString())


__all__ = ["LinkTextEdit", "TranscriptEditor", "ReplyDisplay"]
