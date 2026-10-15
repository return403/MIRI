"""Achsen: Telemetrie aller Achsen und Handsteuerung."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QComboBox, QGridLayout, QGroupBox, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..devices.protocol import DRV_NAMES, ENC_NAMES, ERROR_NAMES, STATE_NAMES
from .widgets import STATE_COLORS, spin

COLUMNS = ['Achse', 'Anschluss', 'Zustand', 'Fehler', 'Position [°]', 'Ziel [°]', 'fährt',
           'Encoder', 'Treiber']


class StepperPanel(QWidget):

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.ctrl = controller

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._select_from_table)

        box = QGroupBox('Handsteuerung')
        g = QGridLayout(box)
        self.axis = QComboBox()
        g.addWidget(QLabel('Achse'), 0, 0)
        g.addWidget(self.axis, 0, 1)

        self.target = spin(-180, 180, 0, 3, 1.0, ' °')
        self.btn_move = QPushButton('Fahren (absolut)')
        self.btn_move.clicked.connect(lambda: self._move('MOVE', self.target.value()))
        g.addWidget(QLabel('Ziel'), 1, 0)
        g.addWidget(self.target, 1, 1)
        g.addWidget(self.btn_move, 1, 2)

        self.jog = spin(0.001, 90, 1.0, 3, 0.5, ' °')
        self.btn_jog_minus = QPushButton('◀ −')
        self.btn_jog_plus = QPushButton('+ ▶')
        self.btn_jog_minus.clicked.connect(lambda: self._move('JOG', -self.jog.value()))
        self.btn_jog_plus.clicked.connect(lambda: self._move('JOG', self.jog.value()))
        force_tip = 'Mit Strg: erzwungen ohne Encoder fahren'
        for b in (self.btn_move, self.btn_jog_minus, self.btn_jog_plus):
            b.setToolTip(force_tip)
        g.addWidget(QLabel('Schritt'), 2, 0)
        g.addWidget(self.jog, 2, 1)
        g.addWidget(self.btn_jog_minus, 2, 2)
        g.addWidget(self.btn_jog_plus, 2, 3)

        self.btn_home = QPushButton('Auf 0 fahren (HOME)')
        self.btn_home.clicked.connect(lambda: self._cmd('HOME'))
        self.btn_stop = QPushButton('Stopp')
        self.btn_stop.clicked.connect(lambda: self._cmd('STOP'))
        self.btn_zero = QPushButton('Nullpunkt setzen')
        self.btn_zero.clicked.connect(self._zero)
        self.btn_cal = QPushButton('Kalibrieren')
        self.btn_cal.clicked.connect(self._cal)
        g.addWidget(self.btn_home, 3, 0, 1, 2)
        g.addWidget(self.btn_stop, 3, 2, 1, 2)
        g.addWidget(self.btn_zero, 4, 0, 1, 2)
        g.addWidget(self.btn_cal, 4, 2, 1, 2)
        g.setColumnStretch(4, 1)

        self.hint = QLabel('Nullpunkt setzen und Kalibrieren gehen nur ohne Laserfreigabe. '
                           'Das Handrad am ESP32 wirkt auch auf Achsen am USB. '
                           'Strg + Fahren/◀/▶: erzwungen ohne Encoder und Softlimits, auch im '
                           'Fehlerzustand, nur ohne Laserfreigabe; danach referenziert die Achse neu.')
        self.hint.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addWidget(box)
        lay.addWidget(self.hint)

        self._controls = [self.btn_move, self.btn_jog_minus, self.btn_jog_plus, self.btn_home,
                          self.btn_zero, self.btn_cal]

    def set_busy(self, busy: bool):
        """Während eines Messablaufs keine Handbefehle (außer Stopp)."""
        for w in self._controls:
            w.setEnabled(not busy)

    def _current_axis(self):
        data = self.axis.currentData()
        return None if data is None else int(data)

    def _select_from_table(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            idx = self.axis.findData(int(self.table.item(rows[0].row(), 0).text()))
            if idx >= 0:
                self.axis.setCurrentIndex(idx)

    def _cmd(self, verb, value=None):
        axis = self._current_axis()
        if axis is None:
            return

        def done(ok, reply, _lines):
            if not ok:
                self.ctrl.log.emit(f'Achse {axis} {verb}: {reply}')
        self.ctrl.axis_command(axis, verb, value, done)

    def _move(self, verb, value):
        """MOVE/JOG, mit gedrückter Strg-Taste erzwungen ohne Encoder (FMOVE/FJOG)."""
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            axis = self._current_axis()
            if axis is not None:
                self.ctrl.log.emit(f'Achse {axis}: erzwungene Fahrt ohne Encoder ({verb} {value:g}°)')
            verb = 'F' + verb
        self._cmd(verb, value)

    def _zero(self):
        axis = self._current_axis()
        if axis is not None and QMessageBox.question(
                self, 'Nullpunkt', f'Aktuelle Position von Achse {axis} als 0° übernehmen?'
        ) == QMessageBox.Yes:
            self._cmd('ZERO')

    def _cal(self):
        axis = self._current_axis()
        if axis is not None and QMessageBox.question(
                self, 'Kalibrieren', f'Achse {axis} kalibrieren? Die Achse bewegt sich dabei.'
        ) == QMessageBox.Yes:
            self._cmd('CAL')

    def refresh(self, master, axes):
        ordered = sorted(axes)
        if [self.axis.itemData(i) for i in range(self.axis.count())] != ordered:
            current = self._current_axis()
            self.axis.clear()
            for a in ordered:
                self.axis.addItem(f'Achse {a}', a)
            if current in ordered:
                self.axis.setCurrentIndex(ordered.index(current))

        self.table.setRowCount(len(ordered))
        for row, a in enumerate(ordered):
            st = axes[a]
            tel = st.tel
            online = st.alive
            if st.source == 'USB':
                where = f'USB {st.port}'
            elif st.source == 'ESP32':
                where = 'ESP32-UART'
            else:
                where = 'nicht verbunden'
            values = [str(a), where,
                      STATE_NAMES.get(tel.state, str(tel.state)) if online else '—',
                      ERROR_NAMES.get(tel.err, str(tel.err)) if online and tel.err else '',
                      f'{tel.pos:.2f}' if online else '',
                      f'{tel.target:.2f}' if online else '',
                      'ja' if online and tel.busy else '',
                      ENC_NAMES.get(tel.enc, str(tel.enc)) if online else '',
                      DRV_NAMES.get(tel.drv, str(tel.drv)) if online else '']
            for col, text in enumerate(values):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    item.setTextAlignment(Qt.AlignCenter)
                    self.table.setItem(row, col, item)
                if item.text() != text:
                    item.setText(text)
            state_item = self.table.item(row, 2)
            state_item.setBackground(QColor(STATE_COLORS[tel.state]) if online
                                     and tel.state in STATE_COLORS else QColor(0, 0, 0, 0))
            state_item.setForeground(QColor('white') if online else QColor('gray'))
