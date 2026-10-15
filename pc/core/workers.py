"""Messablauf im eigenen Thread: Achsen fahren, Oszilloskop auslesen, speichern.

Das Oszilloskop gehört während eines Laufs allein diesem Thread. Achsbefehle gehen über
Controller.run_blocking() in den Hauptthread, wo die seriellen Ports leben.
"""

import itertools
import re
import time
from dataclasses import asdict, dataclass, field

import numpy as np
from PySide6.QtCore import QThread, Signal

from ..data.recorder import Recorder, envelope
from ..devices.protocol import ERROR_NAMES, STATE_ERROR, STATE_NAMES, STATE_READY
from ..devices.siglent import Cancelled, SiglentSDS1104XE

TARGET_MATCH_DEG = 0.05      # Telemetrie hat 2 Nachkommastellen


@dataclass
class ScopeSettings:
    resource: str = 'USB0::0xF4EC::0xEE38::SDSMMFCC6R2573::INSTR'
    timeout_ms: int = 30000
    channel: int = 1
    apply: bool = True              # False = Einstellungen am Gerät lassen
    vdiv: float = 1.5
    offset: float = 0.0
    tdiv: float = 2e-4
    trdl: float = 0.0
    trig_level: float = 1.5
    trig_slope: str = 'POS'
    trig_coupling: str = 'DC'
    trig_wait: float = 10.0
    chunk_points: int = 1_000_000


@dataclass
class RunSettings:
    positions: dict = field(default_factory=dict)   # achse -> [grad, ...]; leer = nur messen
    settle_s: float = 0.5
    repeats: int = 1
    move_timeout_s: float = 60.0
    laser: bool = False             # vor jeder Aufnahme ARM 1, danach ARM 0
    skip_no_trigger: bool = False   # kein Trigger: Punkt überspringen statt abbrechen
    return_home: bool = False
    record: bool = True
    out_dir: str = 'messungen'
    prefix: str = 'lauf'
    save_npz: bool = True
    save_csv: bool = False
    save_png: bool = True
    png_dpi: int = 150


def parse_positions(text: str):
    """'0:90:5' (Start:Stop:Schritt) oder Liste '0; 10; 22,5'. Leer -> []."""
    text = text.strip()
    if not text:
        return []
    if ':' in text:
        parts = [float(p.replace(',', '.')) for p in text.split(':')]
        if len(parts) != 3 or parts[2] == 0:
            raise ValueError(f'Bereich als Start:Stop:Schritt angeben: {text!r}')
        start, stop, step = parts
        if (stop - start) * step < 0:
            step = -step
        n = int(np.floor((stop - start) / step + 1e-9)) + 1
        if n > 100_000:
            raise ValueError('mehr als 100000 Positionen')
        return [round(start + i * step, 3) for i in range(n)]
    return [float(p.replace(',', '.')) for p in re.split(r'[;\s]+', text) if p]


def build_points(positions: dict):
    """Raster über alle Achsen (erste Achse außen). Ohne Achsen genau ein Punkt."""
    axes = sorted(a for a, values in positions.items() if values)
    if not axes:
        return [{}]
    return [dict(zip(axes, combo)) for combo in itertools.product(*(positions[a] for a in axes))]


class MeasurementWorker(QThread):
    status = Signal(str)
    progress = Signal(int, int)         # aufnahmen fertig, aufnahmen gesamt
    transfer = Signal(int, int)         # punkte gelesen, punkte gesamt
    trace = Signal(object)              # dict für die Anzeige
    finished_run = Signal(bool, str)

    def __init__(self, controller, scope: ScopeSettings, run: RunSettings,
                 scope_factory=SiglentSDS1104XE, parent=None):
        super().__init__(parent)
        self.ctrl = controller
        self.scope_cfg = scope
        self.run_cfg = run
        self.scope_factory = scope_factory
        self._moving = False

    def _cancelled(self):
        return self.isInterruptionRequested()

    def _check_cancel(self):
        if self._cancelled():
            raise Cancelled()

    def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._check_cancel()
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))

    # ------------------------------------------------------------ Achsen

    def _command(self, axis, verb, value=None):
        ok, reply = self.ctrl.run_blocking(
            lambda cb: self.ctrl.axis_command(axis, verb, value, cb), timeout=6.0)
        if not ok:
            raise RuntimeError(f'Achse {axis}: {verb} {"" if value is None else value} '
                               f'abgelehnt: {reply}')

    def _move(self, point: dict):
        self._moving = True
        sent_at = {}
        for axis, value in point.items():
            self._command(axis, 'MOVE', value)
            sent_at[axis] = time.monotonic()
        self._wait_reached(point, sent_at)
        self._moving = False

    def _wait_reached(self, point, sent_at):
        deadline = time.monotonic() + self.run_cfg.move_timeout_s
        while True:
            self._check_cancel()
            _master, axes = self.ctrl.snapshot()
            done = True
            for axis, value in point.items():
                st = axes.get(axis)
                if st is None or not st.alive:
                    raise RuntimeError(f'Achse {axis} meldet sich nicht mehr')
                tel = st.tel
                if tel.state == STATE_ERROR:
                    raise RuntimeError(f'Achse {axis} im Fehlerzustand: '
                                       f'{ERROR_NAMES.get(tel.err, tel.err)}')
                # Nur Telemetrie nach dem Befehl zählt, und das Ziel muss stimmen
                # (alte Zeilen haben noch das alte Ziel).
                if (st.updated <= sent_at[axis] or tel.state != STATE_READY or tel.busy
                        or abs(tel.target - value) > TARGET_MATCH_DEG):
                    done = False
            if done:
                return
            if time.monotonic() > deadline:
                states = ', '.join(
                    f'A{a}: {STATE_NAMES.get(axes[a].tel.state, "?")} pos={axes[a].tel.pos:.2f}'
                    f' ziel={axes[a].tel.target:.2f}' for a in point if a in axes)
                raise TimeoutError(f'Position nicht in {self.run_cfg.move_timeout_s} s erreicht '
                                   f'({states}). Softlimits?')
            time.sleep(0.05)

    def _actual_positions(self, point):
        _master, axes = self.ctrl.snapshot()
        return {a: (v, axes[a].tel.pos if a in axes else float('nan'))
                for a, v in point.items()}

    def _arm(self, on: bool):
        ok, reply = self.ctrl.run_blocking(lambda cb: self.ctrl.arm(on, cb), timeout=6.0)
        if not ok and on:
            raise RuntimeError(f'Laserfreigabe abgelehnt ({reply}). Schlüssel an? '
                               f'ESP32 in READY?')

    # ------------------------------------------------------------ Ablauf

    def run(self):
        sc = self.scope_cfg
        rc = self.run_cfg
        scope = None
        laser_on = False
        ok, message = False, ''
        try:
            points = build_points(rc.positions)
            total = len(points) * rc.repeats
            self.progress.emit(0, total)

            self.status.emit('Verbinde Oszilloskop ...')
            scope = self.scope_factory(sc.resource, timeout=sc.timeout_ms)
            idn = scope.ask('*IDN?')
            self.status.emit(f'Oszilloskop: {idn}')

            if sc.apply:
                scope.configure_channel(sc.channel, sc.vdiv, sc.offset)
                scope.configure_timebase(sc.tdiv, sc.trdl)
                scope.configure_trigger(sc.channel, sc.trig_level, sc.trig_slope,
                                        sc.trig_coupling)

            recorder = None
            if rc.record:
                recorder = Recorder(rc.out_dir, rc.prefix, rc.positions.keys(),
                                    {'oszilloskop': asdict(sc), 'ablauf': asdict(rc),
                                     'idn': idn},
                                    rc.save_csv, rc.save_npz, rc.save_png, rc.png_dpi)
                self.status.emit(f'Speichere nach {recorder.run_dir}')

            count = 0
            for nr, point in enumerate(points, start=1):
                self._check_cancel()
                if point:
                    text = ', '.join(f'A{a} → {v:.3f}°' for a, v in point.items())
                    self.status.emit(f'Punkt {nr}/{len(points)}: fahre {text}')
                    self._move(point)
                    self._sleep(rc.settle_s)
                positions = self._actual_positions(point)

                for rep in range(rc.repeats):
                    self._check_cancel()
                    self.status.emit(f'Punkt {nr}/{len(points)}, Aufnahme {rep + 1}/'
                                     f'{rc.repeats}: warte auf Trigger ...')
                    scope.arm_single()
                    try:
                        if rc.laser:
                            self._arm(True)
                            laser_on = True
                        scope.wait_triggered(sc.trig_wait, self._cancelled)
                    except TimeoutError as exc:
                        if not rc.skip_no_trigger:
                            raise
                        self.status.emit(f'Punkt {nr}: {exc} – übersprungen')
                        count += 1
                        self.progress.emit(count, total)
                        continue
                    finally:
                        if laser_on:
                            self._arm(False)
                            laser_on = False

                    t0 = time.monotonic()
                    t, y, info = scope.capture_trace(
                        sc.channel, sc.chunk_points,
                        progress=lambda done, all_: self.transfer.emit(done, all_),
                        cancel=self._cancelled)
                    t_read = time.monotonic() - t0

                    name = ''
                    if recorder is not None:
                        name = recorder.save(nr, rep, positions, t, y, info, sc.channel,
                                             sc.trig_level, sc.trig_slope)
                    te, ye = envelope(t, y)
                    self.trace.emit({
                        't': te, 'y': ye, 'info': info, 'n': len(y), 'name': name,
                        'channel': sc.channel, 'trig_level': sc.trig_level,
                        'trig_slope': sc.trig_slope, 'positions': positions,
                        'u_min': float(np.min(y)), 'u_max': float(np.max(y)),
                        't_read': t_read,
                    })
                    count += 1
                    self.progress.emit(count, total)

            if rc.return_home and rc.positions:
                home = {a: 0.0 for a, v in rc.positions.items() if v}
                self.status.emit('Fahre auf 0 ...')
                self._move(home)

            where = f' – Daten in {recorder.run_dir}' if recorder is not None else ''
            ok, message = True, f'Fertig: {count} Aufnahme(n){where}'

        except Cancelled:
            if self._moving:
                self.ctrl.invoke(self.ctrl.stop_all)
            message = 'Abgebrochen'
        except Exception as exc:
            if self._moving:
                self.ctrl.invoke(self.ctrl.stop_all)
            message = f'Fehler: {exc}'
        finally:
            if laser_on:
                try:
                    self._arm(False)
                except Exception:
                    pass
            if scope is not None:
                try:
                    scope.close()
                except Exception:
                    pass

        # erst nach dem Aufräumen melden (Aufräumen braucht evtl. den Hauptthread)
        self.finished_run.emit(ok, message)
