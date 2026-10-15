"""Verbindung zum ESP32 und zu beliebig vielen Nanos (mindestens einer)."""

from PySide6.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)


class PortCombo(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setMinimumWidth(220)

    def set_ports(self, ports):
        """ports: [(name, beschreibung)]"""
        current = self.currentText()
        self.blockSignals(True)
        self.clear()
        for name, desc in ports:
            self.addItem(f'{name}  ({desc})' if desc else name, name)
        self.setCurrentText(current)
        self.blockSignals(False)

    def port(self) -> str:
        idx = self.findText(self.currentText())
        if idx >= 0 and self.itemData(idx):
            return self.itemData(idx)
        return self.currentText().split()[0] if self.currentText().strip() else ''

    def set_port(self, name: str):
        idx = self.findData(name)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self.setCurrentText(name)


class ModuleRow:
    def __init__(self, panel, axis):
        self.panel = panel
        self.port = PortCombo()
        self.axis = QSpinBox()
        self.axis.setRange(0, 15)
        self.axis.setValue(axis)
        self.axis.setPrefix('Achse ')
        self.btn = QPushButton('Verbinden')
        self.btn.clicked.connect(self.toggle)
        self.status = QLabel('getrennt')
        self.btn_remove = QPushButton('✕')
        self.btn_remove.setToolTip('Zeile entfernen')
        self.btn_remove.setFixedWidth(30)
        self.btn_remove.clicked.connect(lambda: panel.remove_row(self))
        self.connected_axis = None

    def widgets(self):
        return [self.port, self.axis, self.btn, self.status, self.btn_remove]

    def toggle(self):
        ctrl = self.panel.ctrl
        if self.connected_axis is not None:
            ctrl.disconnect_module(self.connected_axis)
            return
        port, axis = self.port.port(), self.axis.value()
        if not port:
            self.status.setText('Port wählen')
            return
        self.status.setText('verbinde ... (Nano startet neu)')
        self.btn.setEnabled(False)
        self.connected_axis = axis

        def done(ok, text):
            self.btn.setEnabled(True)
            if not ok:
                self.connected_axis = None
                self.status.setText(f'Fehler: {text}')
                ctrl.log.emit(f'Nano {port}: {text}')
            self.panel.update_rows()
        ctrl.connect_module(port, axis, done)

    def update(self):
        ctrl = self.panel.ctrl
        connected = self.connected_axis is not None and self.connected_axis in ctrl.module_devs
        connecting = self.connected_axis is not None and self.connected_axis in ctrl._connecting
        if self.connected_axis is not None and not connected and not connecting:
            self.connected_axis = None
            if not self.status.text().startswith('Fehler'):
                self.status.setText('getrennt')
        if connected:
            self.status.setText('verbunden')
        self.btn.setText('Trennen' if connected else 'Verbinden')
        for w in (self.port, self.axis, self.btn_remove):
            w.setEnabled(self.connected_axis is None)


class DevicesPanel(QWidget):

    def __init__(self, controller, port_lister, parent=None):
        super().__init__(parent)
        self.ctrl = controller
        self.port_lister = port_lister
        self.rows = []

        # ---- ESP32
        mbox = QGroupBox('ESP32 (Master: Schlüssel, Laser, Handrad)')
        h = QHBoxLayout(mbox)
        self.master_port = PortCombo()
        self.btn_master = QPushButton('Verbinden')
        self.btn_master.clicked.connect(self._toggle_master)
        self.master_status = QLabel('getrennt')
        h.addWidget(self.master_port, 1)
        h.addWidget(self.btn_master)
        h.addWidget(self.master_status, 1)

        # ---- Nanos
        nbox = QGroupBox('Achsmodule (Nano direkt per USB, link = 0 am ESP32)')
        v = QVBoxLayout(nbox)
        self.grid = QGridLayout()
        v.addLayout(self.grid)
        btn_add = QPushButton('+ Modul')
        btn_add.clicked.connect(lambda: self.add_row())
        row = QHBoxLayout()
        row.addWidget(btn_add)
        row.addStretch(1)
        v.addLayout(row)
        hint = QLabel('Nanos, die am UART des ESP32 hängen (link<n> = 1), brauchen hier keinen '
                      'Eintrag – sie erscheinen automatisch unter „Achsen“. '
                      'Die Achsnummer muss zur Achse am ESP32 passen.')
        hint.setWordWrap(True)
        v.addWidget(hint)

        btn_refresh = QPushButton('Ports aktualisieren')
        btn_refresh.clicked.connect(self.refresh_ports)
        btn_all = QPushButton('Alles verbinden')
        btn_all.clicked.connect(self.connect_all)
        btn_none = QPushButton('Alles trennen')
        btn_none.clicked.connect(self.ctrl.disconnect_all)
        bottom = QHBoxLayout()
        bottom.addWidget(btn_refresh)
        bottom.addStretch(1)
        bottom.addWidget(btn_all)
        bottom.addWidget(btn_none)

        lay = QVBoxLayout(self)
        lay.addWidget(mbox)
        lay.addWidget(nbox)
        lay.addLayout(bottom)
        lay.addStretch(1)

        self.ctrl.devices_changed.connect(self.update_rows)
        self.add_row()
        self.refresh_ports()

    # ---------------------------------------------------------- Zeilen

    def add_row(self, port='', axis=None):
        if axis is None:
            used = {r.axis.value() for r in self.rows}
            axis = next(a for a in range(16) if a not in used)
        row = ModuleRow(self, axis)
        row.port.set_ports(self._ports)
        if port:
            row.port.set_port(port)
        self.rows.append(row)
        self._relayout()
        return row

    def remove_row(self, row):
        if len(self.rows) <= 1:
            return                  # mindestens ein Nano
        self.rows.remove(row)
        for w in row.widgets():
            w.setParent(None)
            w.deleteLater()
        self._relayout()

    def _relayout(self):
        for r, row in enumerate(self.rows):
            for c, w in enumerate(row.widgets()):
                self.grid.addWidget(w, r, c)
        self.grid.setColumnStretch(3, 1)
        for row in self.rows:
            row.btn_remove.setVisible(len(self.rows) > 1)

    def update_rows(self):
        for row in self.rows:
            row.update()
        connected = self.ctrl.master_dev is not None
        if connected and self.ctrl.master.connected:
            self.master_status.setText(f'verbunden: {self.ctrl.master.version}')
        elif connected:
            self.master_status.setText('verbinde ...')
        elif not self.master_status.text().startswith('Fehler'):
            self.master_status.setText('getrennt')
        self.btn_master.setText('Trennen' if connected else 'Verbinden')
        self.master_port.setEnabled(not connected)

    # ---------------------------------------------------------- Aktionen

    @property
    def _ports(self):
        return getattr(self, '_port_cache', [])

    def refresh_ports(self):
        try:
            self._port_cache = self.port_lister()
        except Exception as exc:
            self._port_cache = []
            self.ctrl.log.emit(f'Ports: {exc}')
        self.master_port.set_ports(self._ports)
        for row in self.rows:
            row.port.set_ports(self._ports)

    def _toggle_master(self):
        if self.ctrl.master_dev is not None:
            self.ctrl.disconnect_master()
            return
        port = self.master_port.port()
        if not port:
            self.master_status.setText('Port wählen')
            return
        self.master_status.setText('verbinde ...')

        def done(ok, text):
            if not ok:
                self.master_status.setText(f'Fehler: {text}')
                self.ctrl.log.emit(f'ESP32 {port}: {text}')
            self.update_rows()
        self.ctrl.connect_master(port, done)
        self.update_rows()

    def connect_all(self):
        if self.ctrl.master_dev is None and self.master_port.port():
            self._toggle_master()
        for row in self.rows:
            if row.connected_axis is None and row.port.port():
                row.toggle()

    # ---------------------------------------------------------- Konfiguration

    def get_config(self):
        return {'master_port': self.master_port.port(),
                'modules': [{'port': r.port.port(), 'axis': r.axis.value()} for r in self.rows]}

    def set_config(self, cfg):
        self.master_port.set_port(cfg.get('master_port', ''))
        modules = cfg.get('modules') or []
        if modules:
            while len(self.rows) > 1:
                self.remove_row(self.rows[-1])
            first = self.rows[0]
            first.port.set_port(modules[0].get('port', ''))
            first.axis.setValue(int(modules[0].get('axis', 0)))
            for m in modules[1:]:
                self.add_row(m.get('port', ''), int(m.get('axis', 0)))
