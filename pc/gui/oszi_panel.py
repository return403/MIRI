"""Oszilloskop: alle sinnvollen freien Parameter aus test.py und die Kurvenanzeige."""

from dataclasses import asdict

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core.workers import ScopeSettings
from ..data.recorder import plot_trace
from ..devices.siglent import TRIGGER_COUPLINGS, TRIGGER_SLOPES
from .widgets import SIEdit, spin


class OsziPanel(QWidget):
    single_requested = Signal()

    def __init__(self, resource_lister, parent=None):
        super().__init__(parent)
        self.resource_lister = resource_lister
        d = ScopeSettings()

        # ---- Verbindung
        conn = QGroupBox('Verbindung (VISA)')
        f = QFormLayout(conn)
        self.resource = QComboBox()
        self.resource.setEditable(True)
        self.resource.addItem(d.resource)
        btn_find = QPushButton('Suchen')
        btn_find.clicked.connect(self.find_resources)
        row = QHBoxLayout()
        row.addWidget(self.resource, 1)
        row.addWidget(btn_find)
        f.addRow('Ressource', row)
        self.timeout = QSpinBox()
        self.timeout.setRange(1000, 600000)
        self.timeout.setSingleStep(1000)
        self.timeout.setSuffix(' ms')
        self.timeout.setValue(d.timeout_ms)
        f.addRow('Timeout', self.timeout)
        self.chunk = QSpinBox()
        self.chunk.setRange(10_000, 14_000_000)
        self.chunk.setSingleStep(100_000)
        self.chunk.setGroupSeparatorShown(True)
        self.chunk.setValue(d.chunk_points)
        self.chunk.setToolTip('Übertragung in Blöcken dieser Größe (alle Punkte werden gelesen)')
        f.addRow('Blockgröße [Punkte]', self.chunk)

        # ---- Kanal / Zeitbasis
        acq = QGroupBox('Kanal und Zeitbasis')
        f = QFormLayout(acq)
        self.apply = QCheckBox('Einstellungen vor der Messung ans Oszilloskop senden')
        self.apply.setChecked(d.apply)
        self.apply.setToolTip('Aus: es gilt, was am Gerät eingestellt ist')
        f.addRow(self.apply)
        self.channel = QComboBox()
        self.channel.addItems(['1', '2', '3', '4'])
        f.addRow('Kanal', self.channel)
        self.vdiv = SIEdit(d.vdiv, 'V')
        f.addRow('Vertikal [/div]', self.vdiv)
        self.offset = SIEdit(d.offset, 'V')
        f.addRow('Offset', self.offset)
        self.tdiv = SIEdit(d.tdiv, 's')
        f.addRow('Zeitbasis [/div]', self.tdiv)
        self.trdl = SIEdit(d.trdl, 's')
        self.trdl.setToolTip('Horizontale Verschiebung des Triggerpunkts (TRDL)')
        f.addRow('Trigger-Verzögerung', self.trdl)

        # ---- Trigger
        trg = QGroupBox('Trigger (Flanke)')
        f = QFormLayout(trg)
        self.trig_level = SIEdit(d.trig_level, 'V')
        f.addRow('Pegel', self.trig_level)
        self.trig_slope = QComboBox()
        self.trig_slope.addItems(TRIGGER_SLOPES)
        f.addRow('Flanke (POS = steigend)', self.trig_slope)
        self.trig_coupling = QComboBox()
        self.trig_coupling.addItems(TRIGGER_COUPLINGS)
        f.addRow('Kopplung', self.trig_coupling)
        self.trig_wait = spin(0.1, 3600, d.trig_wait, 1, 1.0, ' s')
        f.addRow('Max. Wartezeit', self.trig_wait)

        # ---- Einzelmessung
        self.btn_single = QPushButton('Einzelmessung')
        self.btn_single.setToolTip('Eine getriggerte Aufnahme ohne Achsbewegung')
        self.btn_single.clicked.connect(self.single_requested)
        self.save_single = QCheckBox('speichern (Ordner/Formate wie im Messablauf)')

        lay = QVBoxLayout(self)
        lay.addWidget(conn)
        lay.addWidget(acq)
        lay.addWidget(trg)
        row = QHBoxLayout()
        row.addWidget(self.btn_single)
        row.addWidget(self.save_single, 1)
        lay.addLayout(row)
        lay.addStretch(1)

    def find_resources(self):
        current = self.resource.currentText()
        try:
            found = self.resource_lister()
        except Exception as exc:
            found = []
            self.resource.setToolTip(f'Suche fehlgeschlagen: {exc}')
        self.resource.clear()
        self.resource.addItems(found or [current])
        idx = self.resource.findText(current)
        if idx >= 0:
            self.resource.setCurrentIndex(idx)

    def settings(self) -> ScopeSettings:
        """Wirft ValueError bei ungültiger Eingabe."""
        return ScopeSettings(
            resource=self.resource.currentText().strip(),
            timeout_ms=self.timeout.value(),
            channel=int(self.channel.currentText()),
            apply=self.apply.isChecked(),
            vdiv=self.vdiv.value(),
            offset=self.offset.value(),
            tdiv=self.tdiv.value(),
            trdl=self.trdl.value(),
            trig_level=self.trig_level.value(),
            trig_slope=self.trig_slope.currentText(),
            trig_coupling=self.trig_coupling.currentText(),
            trig_wait=self.trig_wait.value(),
            chunk_points=self.chunk.value(),
        )

    def get_config(self) -> dict:
        try:
            cfg = asdict(self.settings())
        except ValueError:
            cfg = asdict(ScopeSettings())
        cfg['save_single'] = self.save_single.isChecked()
        return cfg

    def set_config(self, cfg: dict):
        d = asdict(ScopeSettings())
        d.update({k: v for k, v in cfg.items() if k in d})
        s = ScopeSettings(**d)
        if self.resource.findText(s.resource) < 0:
            self.resource.addItem(s.resource)
        self.resource.setCurrentText(s.resource)
        self.timeout.setValue(s.timeout_ms)
        self.chunk.setValue(s.chunk_points)
        self.apply.setChecked(s.apply)
        self.channel.setCurrentText(str(s.channel))
        self.vdiv.setValue(s.vdiv)
        self.offset.setValue(s.offset)
        self.tdiv.setValue(s.tdiv)
        self.trdl.setValue(s.trdl)
        self.trig_level.setValue(s.trig_level)
        self.trig_slope.setCurrentText(s.trig_slope)
        self.trig_coupling.setCurrentText(s.trig_coupling)
        self.trig_wait.setValue(s.trig_wait)
        self.save_single.setChecked(bool(cfg.get('save_single', False)))


class PlotWidget(QWidget):
    """Letzte Aufnahme, Darstellung wie test.py."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure = Figure(figsize=(8, 4))
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.info = QLabel('Noch keine Aufnahme')
        self.info.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(NavigationToolbar2QT(self.canvas, self))
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.info)
        self._empty()

    def _empty(self):
        self.ax.clear()
        self.ax.set_xlabel('Zeit [ms]')
        self.ax.set_ylabel('Spannung [V]')
        self.ax.grid(True)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def show_trace(self, d: dict):
        info = d['info']
        self.ax.clear()
        pos = ', '.join(f'A{a}={p[1]:.2f}°' for a, p in sorted(d['positions'].items()))
        title = (f'CH{d["channel"]} – {d["n"]:,} Punkte @ {info["sara"]:.3g} Sa/s'
                 + (f' – {pos}' if pos else ''))
        plot_trace(self.ax, d['t'], d['y'], info, d['trig_level'], d['trig_slope'], title)
        self.figure.tight_layout()
        self.canvas.draw_idle()
        self.info.setText(
            f'{d["name"] or "nicht gespeichert"}   |   U: {d["u_min"]:.4g} … {d["u_max"]:.4g} V'
            f'   |   Übertragung {d["t_read"]:.1f} s')
