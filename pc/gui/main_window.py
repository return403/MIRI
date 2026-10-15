"""Hauptfenster: Statusleiste oben, Einstellungen links, Kurve und Protokoll rechts."""

import datetime
import json
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QMainWindow, QMessageBox, QPlainTextEdit, QScrollArea,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ..core.workers import MeasurementWorker, RunSettings
from .devices_panel import DevicesPanel
from .oszi_panel import OsziPanel, PlotWidget
from .param_panel import ParamPanel
from .run_panel import RunPanel
from .status_panel import StatusPanel
from .stepper_panel import StepperPanel

CONFIG_FILE = Path(__file__).resolve().parents[2] / 'config' / 'ui.json'


def _scroll(widget):
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    return area


class MainWindow(QMainWindow):

    def __init__(self, controller, port_lister, resource_lister, scope_factory, title='MIRI'):
        super().__init__()
        self.setWindowTitle(title)
        self.ctrl = controller
        self.scope_factory = scope_factory
        self.worker = None

        self.status_panel = StatusPanel(controller)
        self.devices = DevicesPanel(controller, port_lister)
        self.stepper = StepperPanel(controller)
        self.oszi = OsziPanel(resource_lister)
        self.run_panel = RunPanel()
        self.params = ParamPanel(controller)
        self.plot = PlotWidget()
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)

        tabs = QTabWidget()
        tabs.addTab(_scroll(self.devices), 'Verbindung')
        tabs.addTab(self.stepper, 'Achsen')
        tabs.addTab(_scroll(self.oszi), 'Oszilloskop')
        tabs.addTab(_scroll(self.run_panel), 'Messablauf')
        tabs.addTab(self.params, 'Parameter')

        right = QSplitter(Qt.Vertical)
        right.addWidget(self.plot)
        right.addWidget(self.log_view)
        right.setSizes([600, 160])

        split = QSplitter(Qt.Horizontal)
        split.addWidget(tabs)
        split.addWidget(right)
        split.setSizes([520, 900])

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addWidget(self.status_panel)
        lay.addWidget(split, 1)
        self.setCentralWidget(central)
        self.resize(1500, 900)

        controller.log.connect(self.log)
        self.oszi.single_requested.connect(self.start_single)
        self.run_panel.start_requested.connect(self.start_run)
        self.run_panel.cancel_requested.connect(self.cancel_run)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        self.load_config()

    # ---------------------------------------------------------- Anzeige

    def log(self, text):
        stamp = datetime.datetime.now().strftime('%H:%M:%S')
        self.log_view.appendPlainText(f'{stamp}  {text}')

    def refresh(self):
        master, axes = self.ctrl.snapshot()
        self.status_panel.refresh(master, axes)
        self.stepper.refresh(master, axes)
        self.run_panel.set_axes(axes)

    # ---------------------------------------------------------- Messung

    def start_single(self):
        run = self.run_panel.settings() if self.oszi.save_single.isChecked() else RunSettings()
        run.positions = {}
        run.repeats = 1
        run.laser = self.run_panel.laser.isChecked()
        run.return_home = False
        run.record = self.oszi.save_single.isChecked()
        if run.record:
            run.prefix = (run.prefix or 'einzel') + '_einzel'
        self._start(run)

    def start_run(self):
        try:
            run = self.run_panel.settings()
        except ValueError as exc:
            QMessageBox.warning(self, 'Messablauf', str(exc))
            return
        if not (run.save_npz or run.save_csv or run.save_png):
            if QMessageBox.question(self, 'Messablauf',
                                    'Kein Speicherformat gewählt – trotzdem starten?'
                                    ) != QMessageBox.Yes:
                return
        _master, axes = self.ctrl.snapshot()
        missing = [a for a in run.positions if a not in axes or not axes[a].alive]
        if missing:
            QMessageBox.warning(self, 'Messablauf',
                                f'Achse(n) {missing} nicht verbunden oder ohne Telemetrie.')
            return
        self._start(run)

    def _start(self, run):
        if self.worker is not None:
            return
        try:
            scope = self.oszi.settings()
        except ValueError as exc:
            QMessageBox.warning(self, 'Oszilloskop', f'Ungültige Eingabe: {exc}')
            return
        self.save_config()

        w = MeasurementWorker(self.ctrl, scope, run, self.scope_factory, self)
        w.status.connect(self._on_status)
        w.progress.connect(self._on_progress)
        w.transfer.connect(self._on_transfer)
        w.trace.connect(self.plot.show_trace)
        w.finished_run.connect(self._on_finished)
        self.worker = w
        self.run_panel.set_running(True)
        self.oszi.btn_single.setEnabled(False)
        self.stepper.set_busy(True)
        self.run_panel.transfer.setValue(0)
        w.start()

    def cancel_run(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.run_panel.status.setText('breche ab ...')

    def _on_status(self, text):
        self.run_panel.status.setText(text)
        self.log(text)

    def _on_progress(self, done, total):
        self.run_panel.progress.setMaximum(max(total, 1))
        self.run_panel.progress.setValue(done)

    def _on_transfer(self, done, total):
        self.run_panel.transfer.setMaximum(max(total, 1))
        self.run_panel.transfer.setValue(done)

    def _on_finished(self, ok, text):
        self.worker.wait()
        self.worker.deleteLater()
        self.worker = None
        self.run_panel.set_running(False)
        self.oszi.btn_single.setEnabled(True)
        self.stepper.set_busy(False)
        self.run_panel.status.setText(text)
        self.log(text)
        if not ok and text != 'Abgebrochen':
            QMessageBox.warning(self, 'Messung', text)

    # ---------------------------------------------------------- Konfiguration

    def load_config(self):
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        try:
            self.devices.set_config(cfg.get('devices', {}))
            self.oszi.set_config(cfg.get('scope', {}))
            self.run_panel.set_config(cfg.get('run', {}))
        except Exception as exc:        # alte/kaputte Datei darf den Start nicht verhindern
            self.log(f'Konfiguration {CONFIG_FILE} nicht vollständig geladen: {exc}')

    def save_config(self):
        cfg = {'devices': self.devices.get_config(), 'scope': self.oszi.get_config(),
               'run': self.run_panel.get_config()}
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                                   encoding='utf-8')
        except OSError as exc:
            self.log(f'Konfiguration nicht gespeichert: {exc}')

    def closeEvent(self, event):
        if self.worker is not None:
            if QMessageBox.question(self, 'Beenden', 'Messung läuft. Abbrechen und beenden?'
                                    ) != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.requestInterruption()
            # Hauptthread nicht blockieren: der Worker braucht ihn noch (Laser aus, Stopp)
            deadline = time.monotonic() + 15.0
            while self.worker is not None and self.worker.isRunning() \
                    and time.monotonic() < deadline:
                QApplication.processEvents()
                self.worker.wait(20)
        self.save_config()
        self.ctrl.disconnect_all()
        event.accept()
