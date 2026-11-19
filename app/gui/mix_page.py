"""Страница «Микс»: взять профили разных групп из разных стратегий и сохранить результат пресетом."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import strategies
from app.gui.labels import group_name, short_name
from app.gui.shared import InputRow, fit_combo, q


class MixPage(QWidget):
    saved = Signal(str)  # имя пресета, который надо выбрать в списке стратегий
    deleted = Signal(str)  # имя пресета, файл которого удалили

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.by: dict[str, strategies.Strategy] = {}
        self._rows: list[tuple[str, QComboBox]] = []
        self._picks: dict[str, str] = {}
        self._edited = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(q(QLabel("Микс"), objectName="h1"))
        lay.addWidget(q(QLabel("Возьмите за основу любую стратегию и замените нужные группы: Discord из одной, "
                               "YouTube из другой, игры из третьей. Результат сохранится пресетом «свой: …» и "
                               "появится в списке стратегий."), objectName="muted", wordWrap=True))
        lay.addSpacing(4)
        area = q(QScrollArea(), widgetResizable=True)
        body = q(QWidget(), objectName="page")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 8, 24)
        bl.setSpacing(10)
        # строки групп добавляются после постройки окна: без этого QScrollArea сожмёт тело ниже минимума
        # и строки наедут друг на друга
        bl.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        base_card = q(QFrame(), objectName="card")
        cl = QVBoxLayout(base_card)
        cl.setContentsMargins(14, 12, 14, 14)
        cl.setSpacing(8)
        cl.addWidget(q(QLabel("Пресет"), objectName="h2"))
        self.base = fit_combo(QComboBox())
        self.base.setToolTip("Стратегия, из которой берутся все группы, кроме заменённых ниже")
        self.base.currentTextChanged.connect(lambda _t: self._fill())
        cl.addWidget(InputRow("Основа", "Стратегия, из которой берутся все группы, кроме заменённых ниже",
                              self.base, width=260))
        self.name = QLineEdit()
        self.name.setPlaceholderText("например, микс ALT8+FTAA")
        self.name.textEdited.connect(lambda _t: setattr(self, "_edited", True))
        cl.addWidget(InputRow("Имя пресета", "Так пресет будет называться в списке стратегий", self.name, width=260))
        bl.addWidget(base_card)
        bl.addWidget(q(QLabel("Группы целей"), objectName="section"))
        self.box = q(QFrame(), objectName="group")
        self.rows = QVBoxLayout(self.box)
        self.rows.setContentsMargins(4, 4, 4, 4)
        self.rows.setSpacing(0)
        bl.addWidget(self.box)
        self.empty = q(QLabel("Стратегий не нашлось: проверьте папку vendor/zapret."),
                       objectName="empty", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.empty.hide()
        bl.addWidget(self.empty, 1)
        bl.addWidget(q(QLabel("Мои пресеты"), objectName="section"))
        self.presets_box = q(QFrame(), objectName="group")
        self.presets_rows = QVBoxLayout(self.presets_box)
        self.presets_rows.setContentsMargins(4, 4, 4, 4)
        self.presets_rows.setSpacing(0)
        bl.addWidget(self.presets_box)
        self.presets_empty = q(QLabel("Своих пресетов нет: сохраните микс или добавьте файл в «Дополнительно»."),
                               objectName="empty", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        bl.addWidget(self.presets_empty)
        bl.addStretch()
        area.setWidget(body)
        lay.addWidget(area, 1)
        line = QHBoxLayout()
        line.setSpacing(8)
        self.btn_save = q(QPushButton("Сохранить микс"), objectName="primary")
        self.btn_save.setToolTip("Записать пресет в data/presets и сразу включить его")
        self.btn_save.clicked.connect(self._save)
        self.status = q(QLabel(), objectName="muted", wordWrap=True)
        self.lbl_changed = q(QLabel(), objectName="muted")
        self.lbl_changed.setToolTip("Сколько групп взято не из основы")
        self.btn_reset = q(QPushButton("Сбросить замены"), objectName="text")
        self.btn_reset.setToolTip("Вернуть все группы к стратегии-основе")
        self.btn_reset.clicked.connect(self._reset)
        line.addWidget(self.btn_save)
        line.addWidget(self.status, 1)
        line.addWidget(self.lbl_changed)
        line.addWidget(self.btn_reset)
        lay.addLayout(line)  # кнопка всегда на виду, а не в конце прокрутки
        self.reload()

    def reload(self, base: str | None = None, picks: dict[str, str] | None = None):
        """Перечитать стратегии и пресеты; сохранить выбранную основу, если она ещё существует."""
        self._picks = dict(picks or {})  # пустой словарь - это «вернули в основу», а не «не менять»
        items = strategies.load_all()
        self.by = {s.name: s for s in items}
        cur = base or self.base.currentText()
        self.base.blockSignals(True)
        self.base.clear()
        self.base.addItems([s.name for s in items])
        if cur in self.by:
            self.base.setCurrentText(cur)
        self.base.blockSignals(False)
        if picks:
            self._picks = picks
        self._fill()
        self._fill_presets()

    def reload_keep(self):
        """Перечитать список, сохранив выбранную основу и замены (переключение вкладок, удаление пресета)."""
        picks = {g: cb.currentData() for g, cb in self._rows if cb.currentData()}
        self.reload(self.base.currentText(), picks)

    def _clear_presets(self):
        while self.presets_rows.count():
            it = self.presets_rows.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()

    def _fill_presets(self):
        self._clear_presets()
        presets = strategies.user_presets()
        for s in presets:
            row = q(QWidget(), objectName="row")
            h = QHBoxLayout(row)
            h.setContentsMargins(12, 4, 8, 4)
            h.setSpacing(8)
            h.addWidget(q(QLabel(s.name), objectName="rowTitle"), 1)
            rm = q(QPushButton("Удалить"), objectName="text")
            rm.setToolTip("Удалить файл пресета из data/presets")
            rm.clicked.connect(lambda _checked=False, n=s.name: self._delete(n))
            h.addWidget(rm)
            self.presets_rows.addWidget(row)
        self.presets_box.setVisible(bool(presets))  # пустая карточка рисовалась тонкой полоской
        self.presets_empty.setVisible(not presets)

    def _delete(self, name: str):
        if QMessageBox.question(self, "Удалить пресет", f"Удалить пресет «{name}»? Файл исчезнет из data/presets."
                                ) != QMessageBox.StandardButton.Yes:
            return
        if not strategies.delete_preset(name):
            self.status.setText(f"Не получилось удалить {name}")
            return
        self.status.setText(f"Пресет {name} удалён")
        self.deleted.emit(name)
        self.reload_keep()

    def _clear(self):
        while self.rows.count():
            it = self.rows.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        self._rows = []

    def _fill(self):
        self._clear()
        self.empty.setVisible(not self.by)
        self.btn_save.setEnabled(bool(self.by))
        base = self.by.get(self.base.currentText())
        if not base:
            return
        seen = []
        for p in base.profiles:
            if p.group not in seen:
                seen.append(p.group)
        for g in seen:
            cb = fit_combo(QComboBox())
            cb.addItem("как в основе", None)
            for s in self.by.values():
                if s is not base and any(x.group == g for x in s.profiles):
                    cb.addItem(s.name, s.name)
            want = self._picks.get(g)
            if want:
                j = cb.findData(want)
                if j >= 0:
                    cb.setCurrentIndex(j)
            cb.currentIndexChanged.connect(lambda _i: self._rename())
            self.rows.addWidget(InputRow(group_name(g), g, cb, width=260))
            self._rows.append((g, cb))
        self._rename()

    def _rename(self):
        self._update_changed()
        if self._edited:
            return
        names = [self.base.currentText()] + [cb.currentData() for _g, cb in self._rows if cb.currentData()]
        self.name.setText("микс " + "+".join(short_name(n) for n in dict.fromkeys(names)))

    def _update_changed(self):
        """Сводка перед сохранением: сколько групп заменено и есть ли что сбрасывать."""
        total = len(self._rows)
        n = sum(1 for _g, cb in self._rows if cb.currentData())
        self.lbl_changed.setText(f"заменено {n} из {total}" if total else "")
        self.btn_reset.setEnabled(bool(n))

    def _reset(self):
        """Вернуть все группы к основе: одна кнопка вместо правки каждой строки."""
        for _g, cb in self._rows:
            cb.setCurrentIndex(0)
        self._rename()

    def _save(self):
        base = self.by.get(self.base.currentText())
        if not base:
            self.status.setText("Нет стратегий: сначала обновите список.")
            return
        picks = {g: cb.currentData() for g, cb in self._rows if cb.currentData()}
        name = self.name.text().strip() or "микс"
        mixed = strategies.mix(name, {g: self.by[n] for g, n in picks.items() if n in self.by}, base)
        try:
            saved = strategies.save_preset(name, mixed)
        except (OSError, ValueError) as e:
            self.status.setText(f"Не получилось сохранить: {e}")
            return
        self.status.setText(f"Сохранено: {saved}")  # выбор в списке делает окно: страница про него не знает
        self.saved.emit(saved)
