"""Leiste oben: Zustand des ESP32, Laser, Quittieren und NOT-STOPP."""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ..devices.protocol import ERROR_NAMES, STATE_ERROR, STATE_NAMES, STATE_READY
from .widgets import STATE_COLORS, badge_style


class StatusPanel(QWidget):

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.ctrl = controller

        self.state = QLabel()
        self.error = QLabel()
        self.key = QLabel()
        self.laser = QLabel()

        self.btn_ack = QPushButton('Fehler quittieren')
        self.btn_ack.clicked.connect(lambda: self.ctrl.ack(self._log_result('ACK')))

        self.btn_arm = QPushButton('Laser freigeben')
        self.btn_arm.setCheckable(True)
        self.btn_arm.clicked.connect(self._toggle_arm)

        self.btn_stop = QPushButton('STOPP alle Achsen')
        self.btn_stop.setStyleSheet('QPushButton { background: #c62828; color: white; '
                                    'font-weight: bold; padding: 6px 18px; }')
        self.btn_stop.clicked.connect(self.ctrl.stop_all)

        lay = QHBoxLayout(self)
        lay.addWidget(QLabel('ESP32:'))
        lay.addWidget(self.state)
        lay.addWidget(self.error, 1)
        lay.addWidget(self.key)
        lay.addWidget(self.laser)
        lay.addWidget(self.btn_ack)
        lay.addWidget(self.btn_arm)
        lay.addWidget(self.btn_stop)

    def _log_result(self, what):
        def done(ok, reply, _lines):
            if not ok:
                self.ctrl.log.emit(f'{what}: {reply}')
        return done

    def _toggle_arm(self, checked):
        self.ctrl.arm(checked, self._log_result('ARM'))

    def refresh(self, master, axes):
        if not master.connected:
            self.state.setText('nicht verbunden')
            self.state.setStyleSheet(badge_style('#8a8a8a'))
            self.error.setText('')
            self.key.setText('')
            self.laser.setText('')
            self.btn_arm.setEnabled(False)
        else:
            s = master.summary
            self.state.setText(STATE_NAMES.get(s.state, str(s.state)))
            self.state.setStyleSheet(badge_style(STATE_COLORS.get(s.state, '#8a8a8a')))
            text = ''
            if s.state == STATE_ERROR:
                text = f'Fehler {s.err}: {ERROR_NAMES.get(s.err, "?")} (Achse {s.err_axis})'
            missing = master.missing_axes()
            if missing:
                text += f'  –  ESP32 hört Achse(n) {missing} nicht'
            self.error.setText(text.strip(' –'))
            self.key.setText('Schlüssel: ' + ('EIN' if s.key else 'aus'))
            self.laser.setText('Laser: ' + ('AN' if s.laser else 'aus'))
            self.laser.setStyleSheet(badge_style('#c62828') if s.laser else '')
            self.btn_arm.setEnabled(s.state == STATE_READY and s.key or s.armed)
            self.btn_arm.setChecked(s.armed)

        any_error = (master.connected and master.summary.state == STATE_ERROR) or any(
            st.tel.state == STATE_ERROR for st in axes.values() if st.source == 'USB')
        self.btn_ack.setEnabled(any_error)
