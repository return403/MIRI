"""Messablauf: Positionsraster über die Achsen, je Punkt Oszilloskop auslesen und speichern."""

from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QProgressBar, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.workers import RunSettings, build_points, parse_positions
from .widgets import spin

DEFAULT_OUT = str(Path(__file__).resolve().parents[2] / 'messungen')


class RunPanel(QWidget):
    start_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        d = RunSettings()
        self._pos_edits = {}            # achse -> QLineEdit

        # ---- Positionen
        pbox = QGroupBox('Positionen je Achse (leer = Achse bleibt stehen)')
        v = QVBoxLayout(pbox)
        self.pos_table = QTableWidget(0, 2)
        self.pos_table.setHorizontalHeaderLabels(['Achse', 'Positionen [°]'])
        self.pos_table.verticalHeader().setVisible(False)
        self.pos_table.horizontalHeader().setStretchLastSection(True)
        self.pos_table.setMaximumHeight(150)
        v.addWidget(self.pos_table)
        hint = QLabel('Bereich „Start:Stop:Schritt“, z. B. 0:90:5 – oder Liste „0; 10; 22,5“. '
                      'Mehrere Achsen = Raster (kleinste Achsnummer außen).')
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- Ablauf
        abox = QGroupBox('Ablauf')
        f = QFormLayout(abox)
        self.settle = spin(0, 600, d.settle_s, 2, 0.1, ' s')
        self.settle.setToolTip('Wartezeit nach Erreichen der Position, vor der Aufnahme')
        f.addRow('Beruhigungszeit', self.settle)
        self.repeats = QSpinBox()
        self.repeats.setRange(1, 10000)
        self.repeats.setValue(d.repeats)
        f.addRow('Aufnahmen je Punkt', self.repeats)
        self.move_timeout = spin(1, 3600, d.move_timeout_s, 0, 5, ' s')
        f.addRow('Max. Fahrzeit', self.move_timeout)
        self.laser = QCheckBox('Laser je Aufnahme freigeben (ARM 1 … ARM 0)')
        self.laser.setToolTip('Freigabe nur während das Oszilloskop auf den Trigger wartet. '
                              'Braucht Schlüssel und ESP32 in READY.')
        f.addRow(self.laser)
        self.skip = QCheckBox('Kein Trigger: Punkt überspringen statt abbrechen')
        f.addRow(self.skip)
        self.home = QCheckBox('Am Ende auf 0° fahren')
        f.addRow(self.home)

        # ---- Speichern
        sbox = QGroupBox('Speichern')
        f = QFormLayout(sbox)
        self.out_dir = QLineEdit(DEFAULT_OUT)
        btn_dir = QPushButton('…')
        btn_dir.setFixedWidth(30)
        btn_dir.clicked.connect(self._choose_dir)
        row = QHBoxLayout()
        row.addWidget(self.out_dir, 1)
        row.addWidget(btn_dir)
        f.addRow('Ordner', row)
        self.prefix = QLineEdit(d.prefix)
        f.addRow('Name des Laufs', self.prefix)
        fmt = QHBoxLayout()
        self.save_npz = QCheckBox('NPZ (komprimiert)')
        self.save_npz.setChecked(d.save_npz)
        self.save_csv = QCheckBox('CSV')
        self.save_csv.setChecked(d.save_csv)
        self.save_csv.setToolTip('Wie test.py. Bei Mpts sehr groß und langsam.')
        self.save_png = QCheckBox('PNG')
        self.save_png.setChecked(d.save_png)
        fmt.addWidget(self.save_npz)
        fmt.addWidget(self.save_csv)
        fmt.addWidget(self.save_png)
        f.addRow('Formate', fmt)
        self.dpi = QSpinBox()
        self.dpi.setRange(50, 600)
        self.dpi.setValue(d.png_dpi)
        f.addRow('PNG-Auflösung [dpi]', self.dpi)

        # ---- Start
        self.summary = QLabel()
        self.btn_start = QPushButton('Messablauf starten')
        self.btn_start.setStyleSheet('QPushButton { font-weight: bold; padding: 6px; }')
        self.btn_start.clicked.connect(self.start_requested)
        self.btn_cancel = QPushButton('Abbrechen')
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_requested)
        self.progress = QProgressBar()
        self.progress.setFormat('%v / %m Aufnahmen')
        self.transfer = QProgressBar()
        self.transfer.setFormat('Übertragung %p %')
        self.status = QLabel('bereit')
        self.status.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(pbox)
        lay.addWidget(abox)
        lay.addWidget(sbox)
        lay.addWidget(self.summary)
        row = QHBoxLayout()
        row.addWidget(self.btn_start, 1)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)
        lay.addWidget(self.progress)
        lay.addWidget(self.transfer)
        lay.addWidget(self.status)
        lay.addStretch(1)

        self.repeats.valueChanged.connect(self._update_summary)
        self._update_summary()

    def _choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, 'Ordner für Messungen', self.out_dir.text())
        if path:
            self.out_dir.setText(path)

    def set_axes(self, axes):
        """Für jede bekannte Achse eine Zeile (Zeilen bleiben, Text bleibt erhalten)."""
        for a in sorted(axes):
            if a in self._pos_edits:
                continue
            self._add_axis_row(a)

    def _add_axis_row(self, axis, text=''):
        edit = QLineEdit(text)
        edit.setPlaceholderText('z. B. 0:90:5')
        edit.textChanged.connect(self._update_summary)
        self._pos_edits[axis] = edit
        rows = sorted(self._pos_edits)
        r = rows.index(axis)
        self.pos_table.insertRow(r)
        self.pos_table.setItem(r, 0, QTableWidgetItem(f'Achse {axis}'))
        self.pos_table.setCellWidget(r, 1, edit)
        self._update_summary()

    def positions(self):
        """achse -> [grad]; wirft ValueError mit Achsnummer."""
        result = {}
        for a, edit in self._pos_edits.items():
            try:
                values = parse_positions(edit.text())
            except ValueError as exc:
                raise ValueError(f'Achse {a}: {exc}') from None
            if values:
                result[a] = values
        return result

    def _update_summary(self):
        try:
            pos = self.positions()
        except ValueError as exc:
            self.summary.setText(f'⚠ {exc}')
            return
        n = len(build_points(pos))
        r = self.repeats.value()
        axes = ', '.join(f'A{a}: {len(v)}' for a, v in sorted(pos.items())) or 'keine Bewegung'
        self.summary.setText(f'{n} Punkt(e) ({axes}) × {r} = {n * r} Aufnahme(n)')

    def set_running(self, running: bool):
        self.btn_start.setEnabled(not running)
        self.btn_cancel.setEnabled(running)

    def settings(self) -> RunSettings:
        return RunSettings(
            positions=self.positions(),
            settle_s=self.settle.value(),
            repeats=self.repeats.value(),
            move_timeout_s=self.move_timeout.value(),
            laser=self.laser.isChecked(),
            skip_no_trigger=self.skip.isChecked(),
            return_home=self.home.isChecked(),
            record=True,
            out_dir=self.out_dir.text().strip() or DEFAULT_OUT,
            prefix=self.prefix.text().strip() or 'lauf',
            save_npz=self.save_npz.isChecked(),
            save_csv=self.save_csv.isChecked(),
            save_png=self.save_png.isChecked(),
            png_dpi=self.dpi.value(),
        )

    def get_config(self):
        cfg = {k: v for k, v in asdict(RunSettings()).items() if k != 'positions'}
        cfg.update(settle_s=self.settle.value(), repeats=self.repeats.value(),
                   move_timeout_s=self.move_timeout.value(), laser=self.laser.isChecked(),
                   skip_no_trigger=self.skip.isChecked(), return_home=self.home.isChecked(),
                   out_dir=self.out_dir.text(), prefix=self.prefix.text(),
                   save_npz=self.save_npz.isChecked(), save_csv=self.save_csv.isChecked(),
                   save_png=self.save_png.isChecked(), png_dpi=self.dpi.value())
        cfg['positions_text'] = {str(a): e.text() for a, e in self._pos_edits.items()}
        return cfg

    def set_config(self, cfg):
        d = RunSettings()
        self.settle.setValue(cfg.get('settle_s', d.settle_s))
        self.repeats.setValue(int(cfg.get('repeats', d.repeats)))
        self.move_timeout.setValue(cfg.get('move_timeout_s', d.move_timeout_s))
        self.laser.setChecked(bool(cfg.get('laser', d.laser)))
        self.skip.setChecked(bool(cfg.get('skip_no_trigger', d.skip_no_trigger)))
        self.home.setChecked(bool(cfg.get('return_home', d.return_home)))
        self.out_dir.setText(cfg.get('out_dir') or DEFAULT_OUT)
        self.prefix.setText(cfg.get('prefix', d.prefix))
        self.save_npz.setChecked(bool(cfg.get('save_npz', d.save_npz)))
        self.save_csv.setChecked(bool(cfg.get('save_csv', d.save_csv)))
        self.save_png.setChecked(bool(cfg.get('save_png', d.save_png)))
        self.dpi.setValue(int(cfg.get('png_dpi', d.png_dpi)))
        for a, text in (cfg.get('positions_text') or {}).items():
            a = int(a)
            if a in self._pos_edits:
                self._pos_edits[a].setText(text)
            else:
                self._add_axis_row(a, text)
