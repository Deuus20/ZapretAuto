"""Карточка «Проверки» в настройках: какие встроенные плашки показывать и какие свои адреса проверять.

Свои строки сохраняются сразу: отдельной кнопки «Сохранить» нет. Тип выбирается списком, чтобы не
заставлять человека помнить синтаксис tcp:// и stun://.
"""
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import tester
from app.gui.shared import SettingRow, Switch, fit_combo, q
from app.settings import load_settings

KINDS = (("Сайт", "site"), ("Сервер по TCP", "tcp"), ("Сервер по UDP (STUN)", "stun"))


class ChecksBox(QFrame):
    """Встроенные плашки галочками, свои адреса строками: тип, адрес и порт, без ручного tcp://."""

    changed = Signal(dict)  # {"hidden": [...], "custom": [{"name", "url"}]} - сразу, без кнопки

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("group")
        self.boxes: dict[str, Switch] = {}
        self._rows: list[tuple[QWidget, QComboBox, QLineEdit, QLineEdit]] = []
        self._good: dict[QWidget, dict] = {}  # последняя рабочая версия строки: пока порт не дописан, живёт она
        self._saved: dict = {"hidden": [], "custom": []}
        self._loading = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._changed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 12)
        lay.setSpacing(0)
        for label in tester.QUICK:
            sw = Switch()
            sw.setToolTip(f"Показывать плашку «{label}» в проверке доступа")
            sw.toggled.connect(lambda _v: self._changed())
            self.boxes[label] = sw
            lay.addWidget(SettingRow(label, f"Показывать плашку «{label}» в проверке доступа", sw))
        head = QHBoxLayout()
        head.setContentsMargins(12, 10, 0, 4)
        head.setSpacing(8)
        head.addWidget(q(QLabel("Свои адреса"), objectName="rowTitle"))
        head.addStretch()
        add = q(QPushButton("Добавить адрес"), objectName="tonal")
        add.setToolTip("Добавить свою проверку: сайт или сервер с портом")
        add.clicked.connect(lambda: self.add_row("site", "", ""))
        head.addWidget(add)
        lay.addLayout(head)
        hint = q(QLabel("Сайт: example.com или https://example.com. Сервер: IP или имя и порт, например "
                        "192.168.1.10 и 27015. Каждая строка - отдельная плашка."), objectName="muted", wordWrap=True)
        hrow = QHBoxLayout()
        hrow.setContentsMargins(12, 0, 0, 6)  # как у строк: подсказка не должна висеть левее подписей
        hrow.addWidget(hint)
        lay.addLayout(hrow)
        self.own = QWidget()
        self.own_rows = QVBoxLayout(self.own)
        self.own_rows.setContentsMargins(0, 0, 0, 0)
        self.own_rows.setSpacing(0)
        lay.addWidget(self.own)
        self.empty = q(QLabel("Своих адресов пока нет: нажмите «Добавить адрес»."), objectName="empty",
                       alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        lay.addWidget(self.empty)
        self.status = q(QLabel(), objectName="muted", wordWrap=True)
        lay.addWidget(self.status)
        self.load()

    def load(self):
        """Заполнить по настройкам: галочки и свои строки."""
        self._loading = True
        try:
            c = load_settings().get("checks")
            c = c if isinstance(c, dict) else {}
            hidden = {x for x in (c.get("hidden") or []) if isinstance(x, str)}
            for label, sw in self.boxes.items():
                sw.setChecked(label not in hidden)
            for row, _k, _a, _p in list(self._rows):
                self._drop(row)
            self._good.clear()
            items = c.get("custom")
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict):
                    kind, addr, port = self._split(item.get("url") or "")
                    self.add_row(kind, addr, port)
            self.status.setText("")
            self._saved = self.result_checks()[0]
        finally:
            self._loading = False

    @staticmethod
    def _split(url: str) -> tuple[str, str, str]:
        """Сохранённый URL обратно в поля: тип, адрес и порт."""
        url = str(url or "").strip()
        for prefix, kind in (("tcp://", "tcp"), ("stun://", "stun")):
            if url.startswith(prefix):
                rest = url[len(prefix):]
                host, sep, port = rest.rpartition(":")
                if not sep or not port.isdigit():  # в настройках мог остаться адрес без порта
                    return kind, rest, ""
                return kind, host, port
        return "site", url[len("https://"):] if url.startswith("https://") else url, ""

    def add_row(self, kind: str, addr: str, port: str):
        row = q(QWidget(), objectName="row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 4, 8, 4)
        h.setSpacing(8)
        kind_cb = fit_combo(QComboBox(), rows=4)
        for title, value in KINDS:
            kind_cb.addItem(title, value)
        i = kind_cb.findData(kind)
        kind_cb.setCurrentIndex(max(i, 0))
        kind_cb.setFixedWidth(190)
        addr_edit = QLineEdit(addr)
        addr_edit.setPlaceholderText("example.com")
        port_edit = QLineEdit(str(port))
        port_edit.setPlaceholderText("порт")
        port_edit.setFixedWidth(80)
        rm = q(QPushButton("×"), objectName="text")
        rm.setFixedWidth(28)
        rm.setToolTip("Удалить эту строку")
        rm.setAccessibleName("Удалить адрес")
        rm.clicked.connect(lambda: self._drop(row))
        h.addWidget(kind_cb)
        h.addWidget(addr_edit, 1)
        h.addWidget(port_edit)
        h.addWidget(rm)
        self.own_rows.addWidget(row)
        self._rows.append((row, kind_cb, addr_edit, port_edit))
        kind_cb.currentIndexChanged.connect(lambda _i: self._kind_changed(kind_cb, addr_edit, port_edit))
        addr_edit.textChanged.connect(lambda _t: self._timer.start(700))
        port_edit.textChanged.connect(lambda _t: self._timer.start(700))
        self._kind_changed(kind_cb, addr_edit, port_edit)
        self.empty.setVisible(False)
        if not self._loading:
            self._timer.start(700)

    @staticmethod
    def _kind_changed(kind_cb: QComboBox, addr_edit: QLineEdit, port_edit: QLineEdit):
        server = kind_cb.currentData() != "site"
        port_edit.setVisible(server)
        addr_edit.setPlaceholderText("192.168.1.10" if server else "example.com")

    def _drop(self, row: QWidget):
        for i, (w, _k, _a, _p) in enumerate(self._rows):
            if w is row:
                self._rows.pop(i)
                break
        self._good.pop(row, None)
        self.own_rows.removeWidget(row)
        row.hide()
        row.deleteLater()
        self.empty.setVisible(not self._rows)
        if not self._loading:
            self._timer.start(700)

    def _row_url(self, kind_cb: QComboBox, addr_edit: QLineEdit,
                 port_edit: QLineEdit) -> tuple[str | None, str, str]:
        """(url, имя для плашки, ошибка). Имя берётся из адреса, чтобы не спрашивать его отдельно."""
        kind = kind_cb.currentData()
        addr = addr_edit.text().strip()
        if not addr:
            return None, "", "Заполните адрес или удалите пустую строку."
        if kind == "site":
            url = addr if addr.startswith(("http://", "https://")) else "https://" + addr
            host = urlparse(url).hostname or ""
            if not host:
                return None, "", f"«{addr}» не похож на адрес сайта."
            if not tester.valid_target(url):
                return None, "", f"«{addr}» не похож на адрес сайта."
            return url, host, ""
        host = addr
        port = port_edit.text().strip()
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            return None, "", "Укажите порт числом от 1 до 65535."
        url = f"{kind}://{host}:{port}"
        if not tester.valid_target(url):
            return None, "", f"«{host}:{port}» не похож на адрес сервера."
        return url, f"{host}:{port}", ""

    def result_checks(self) -> tuple[dict, str]:
        """(настройки, ошибка). Пустые строки пропускаются, а ошибка в одной строке не мешает сохранить
        остальное: у такой строки остаётся её прошлое значение, а галочки применяются сразу."""
        hidden = [label for label, sw in self.boxes.items() if not sw.isChecked()]
        custom: list[dict] = []
        seen: set[str] = set()
        err = ""
        for row, kind_cb, addr_edit, port_edit in self._rows:
            if not addr_edit.text().strip() and not port_edit.text().strip():
                self._good.pop(row, None)  # пустая строка: этой проверки больше нет
                continue
            url, name, problem = self._row_url(kind_cb, addr_edit, port_edit)
            if problem:
                err = err or problem
                keep = self._good.get(row)  # недописанный адрес не должен стирать уже сохранённый
                if keep and keep["name"] not in seen:
                    seen.add(keep["name"])
                    custom.append(keep)
                continue
            if name in seen:
                err = err or f"«{name}» уже добавлен."
                continue
            seen.add(name)
            entry = {"name": name, "url": url}
            self._good[row] = entry
            custom.append(entry)
        return {"hidden": hidden, "custom": custom}, err

    def _changed(self):
        """Любое изменение: проверить и отдать наверх для сохранения и обновления плашек."""
        if self._loading:  # load() сам выставляет галочки: это не действие пользователя
            return
        cfg, err = self.result_checks()
        if err:
            self.status.setText(err)
        if cfg != self._saved:  # то же самое второй раз не сохраняем: иначе каждый символ звал проверку
            self._saved = cfg
            if not err:
                self.status.setText("Сохранено")
            self.changed.emit(cfg)
