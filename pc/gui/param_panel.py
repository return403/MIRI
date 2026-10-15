"""Parameter der Firmware lesen, ändern, im EEPROM speichern (LIST / SET / SAVE)."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..devices import protocol
from ..devices.protocol import fmt_float


class ParamPanel(QWidget):

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.ctrl = controller
        self._filling = False

        self.target = QComboBox()
        btn_read = QPushButton('Lesen')
        btn_read.clicked.connect(self.read)
        btn_save = QPushButton('Im EEPROM speichern')
        btn_save.clicked.connect(self.save)
        btn_defaults = QPushButton('Werkseinstellungen')
        btn_defaults.clicked.connect(self.defaults)

        top = QHBoxLayout()
        top.addWidget(QLabel('Gerät'))
        top.addWidget(self.target, 1)
        top.addWidget(btn_read)
        top.addWidget(btn_save)
        top.addWidget(btn_defaults)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['Name', 'Wert'])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._changed)

        self.status = QLabel('Wert ändern und Enter drücken = SET. Erst „Im EEPROM speichern“ '
                             'macht es dauerhaft. Der ESP32 zeigt auch die Parameter der Module '
                             'an seinem UART.')
        self.status.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.status)

        self.ctrl.devices_changed.connect(self.update_targets)
        self.update_targets()

    def update_targets(self):
        current = self.target.currentData()
        self.target.clear()
        if self.ctrl.master.connected:
            self.target.addItem('ESP32 (Master)', 'master')
        for axis in sorted(self.ctrl.module_devs):
            self.target.addItem(f'Nano Achse {axis} (USB)', axis)
        idx = self.target.findData(current)
        if idx >= 0:
            self.target.setCurrentIndex(idx)

    def _done(self, what):
        def done(ok, reply, _lines):
            self.status.setText(f'{what}: {"OK" if ok else reply}')
        return done

    def read(self):
        target = self.target.currentData()
        if target is None:
            return
        self.status.setText('lese ...')

        def done(ok, reply, lines):
            if not ok:
                self.status.setText(f'LIST: {reply}')
                return
            self._filling = True
            self.table.setRowCount(0)
            for line in lines:
                name, value = protocol.parse_param(line)
                r = self.table.rowCount()
                self.table.insertRow(r)
                item = QTableWidgetItem(name)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, 0, item)
                v = QTableWidgetItem(f'{value:g}')
                v.setData(Qt.UserRole, value)
                self.table.setItem(r, 1, v)
            self._filling = False
            self.status.setText(f'{len(lines)} Parameter gelesen')
        self.ctrl.param_request(target, 'LIST', done, multi=True)

    def _changed(self, item):
        if self._filling or item.column() != 1:
            return
        target = self.target.currentData()
        name = self.table.item(item.row(), 0).text()
        old = item.data(Qt.UserRole)
        try:
            value = float(item.text().replace(',', '.'))
        except ValueError:
            self._set_item(item, old, '#ffcdd2')
            return

        def done(ok, reply, _lines):
            if ok:
                self._set_item(item, value, '#c8e6c9')
                self.status.setText(f'{name} = {value:g}')
            else:
                self._set_item(item, old, '#ffcdd2')
                self.status.setText(f'{name}: {reply}')
        self.ctrl.param_request(target, f'SET {name} {fmt_float(value)}', done)

    def _set_item(self, item, value, color):
        self._filling = True
        item.setText(f'{value:g}')
        item.setData(Qt.UserRole, value)
        item.setBackground(QColor(color))
        self._filling = False

    def save(self):
        target = self.target.currentData()
        if target is not None:
            self.ctrl.param_request(target, 'SAVE', self._done('SAVE'))

    def defaults(self):
        target = self.target.currentData()
        if target is not None and QMessageBox.question(
                self, 'Werkseinstellungen',
                'Alle Parameter auf Werkseinstellungen setzen (noch nicht gespeichert)?\n'
                'Kalibrierung und Nullpunkt gehen dabei verloren, sobald gespeichert wird.'
        ) == QMessageBox.Yes:
            self.ctrl.param_request(target, 'DEFAULTS', self._done('DEFAULTS'))
