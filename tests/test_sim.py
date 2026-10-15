"""Zusammenspiel Controller + Messablauf gegen die simulierten Geräte (ohne Fenster).

    QT_QPA_PLATFORM=offscreen python -m unittest tests.test_sim
"""

import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from pc.core.controller import Controller
from pc.core.workers import MeasurementWorker, RunSettings, ScopeSettings
from pc.devices.protocol import STATE_ERROR, STATE_READY
from tests.mocks.mock_arduino import SimBus
from tests.mocks.mock_oszi import MockScope

app = QCoreApplication.instance() or QCoreApplication([])


def spin_until(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


class SimTest(unittest.TestCase):

    def setUp(self):
        self.bus = SimBus()
        self.bus.devices['SIM-ESP32'].params['link1'] = 0.0     # zweite Achse benutzen
        self.ctrl = Controller(transport_factory=self.bus.open)
        results = []
        self.ctrl.connect_master('SIM-ESP32', lambda ok, t: results.append(ok))
        self.ctrl.connect_module('SIM-NANO-A', 0, lambda ok, t: results.append(ok))
        self.ctrl.connect_module('SIM-NANO-B', 1, lambda ok, t: results.append(ok))
        self.assertTrue(spin_until(lambda: len(results) == 3))
        self.assertEqual(results, [True, True, True])

    def tearDown(self):
        self.ctrl.disconnect_all()
        app.processEvents()

    def master_ready(self):
        master, axes = self.ctrl.snapshot()
        return (master.summary.state == STATE_READY
                and all(axes[a].tel.state == STATE_READY for a in (0, 1)))

    def test_boot_to_ready_and_run(self):
        self.assertTrue(spin_until(self.master_ready, 8), self.ctrl.snapshot())

        out = tempfile.mkdtemp()
        run = RunSettings(positions={0: [0, 10], 1: [-5]}, settle_s=0.05, repeats=2,
                          laser=True, return_home=True, out_dir=out, prefix='t',
                          save_csv=False, save_npz=True, save_png=True, png_dpi=50)
        worker = MeasurementWorker(self.ctrl, ScopeSettings(resource=MockScope.RESOURCE),
                                   run, MockScope)
        done = []
        traces = []
        worker.finished_run.connect(lambda ok, msg: done.append((ok, msg)))
        worker.trace.connect(traces.append)
        worker.start()
        self.assertTrue(spin_until(lambda: done, 60))
        worker.wait()
        self.assertTrue(done[0][0], done[0][1])
        self.assertEqual(len(traces), 4)
        self.assertAlmostEqual(traces[-1]['positions'][0][1], 10.0, places=1)

        run_dir = next(Path(out).iterdir())
        self.assertEqual(len(list(run_dir.glob('*.npz'))), 4)
        self.assertEqual(len((run_dir / 'messungen.csv').read_text().splitlines()), 5)

        # am Ende auf 0 gefahren, Laser wieder gesperrt
        _m, axes = self.ctrl.snapshot()
        self.assertTrue(spin_until(lambda: abs(self.ctrl.snapshot()[1][1].tel.pos) < 0.01, 3))
        self.assertFalse(self.ctrl.snapshot()[0].summary.armed)

    def test_master_error_estop_and_ack(self):
        self.assertTrue(spin_until(self.master_ready, 8))
        # Nano B verschwindet -> Master: Modul weg -> GUI schickt ESTOP an Nano A
        self.ctrl.disconnect_module(1)
        self.assertTrue(spin_until(
            lambda: self.ctrl.snapshot()[0].summary.state == STATE_ERROR, 5))
        self.assertTrue(spin_until(
            lambda: self.ctrl.snapshot()[1][0].tel.state == STATE_ERROR, 3))

        # Nano B wieder da, quittieren -> alles wieder READY
        ok = []
        self.ctrl.connect_module('SIM-NANO-B', 1, lambda o, t: ok.append(o))
        self.assertTrue(spin_until(lambda: ok, 5) and ok[0])
        self.assertTrue(spin_until(lambda: self.ctrl.snapshot()[1][1].alive, 3))
        spin_until(lambda: False, 0.1)      # AXIS-Meldung unterwegs zum Master
        acked = []
        self.ctrl.ack(lambda o, r, l: acked.append((o, r)))
        self.assertTrue(spin_until(lambda: acked, 3))
        self.assertTrue(acked[0][0], acked)
        self.assertTrue(spin_until(self.master_ready, 8), self.ctrl.snapshot())

    def test_soft_limit_rejected(self):
        self.assertTrue(spin_until(self.master_ready, 8))
        run = RunSettings(positions={0: [120]}, record=False)
        worker = MeasurementWorker(self.ctrl, ScopeSettings(), run, MockScope)
        done = []
        worker.finished_run.connect(lambda ok, msg: done.append((ok, msg)))
        worker.start()
        self.assertTrue(spin_until(lambda: done, 10))
        worker.wait()
        self.assertFalse(done[0][0])
        self.assertIn('MOVE', done[0][1])

    def command(self, verb, value=None, axis=0):
        res = []
        self.ctrl.axis_command(axis, verb, value, lambda o, r, _l: res.append((o, r)))
        self.assertTrue(spin_until(lambda: res, 3))
        return res[0]

    def test_forced_move(self):
        self.assertTrue(spin_until(self.master_ready, 8))

        # mit Laserfreigabe gesperrt
        armed = []
        self.ctrl.arm(True, lambda o, r, _l: armed.append(o))
        self.assertTrue(spin_until(lambda: self.ctrl.snapshot()[0].summary.armed, 3), armed)
        ok, reply = self.command('FJOG', 5.0)
        self.assertFalse(ok)
        self.assertIn('Laserfreigabe', reply)
        self.ctrl.arm(False)
        self.assertTrue(spin_until(lambda: not self.ctrl.snapshot()[0].summary.armed, 3))

        # aus READY: fährt, referenziert danach neu und ist wieder READY
        self.assertEqual(self.command('FJOG', 5.0), (True, 'OK'))
        self.assertTrue(spin_until(
            lambda: self.ctrl.snapshot()[1][0].tel.state != STATE_READY, 2))
        self.assertTrue(spin_until(
            lambda: self.master_ready() and abs(self.ctrl.snapshot()[1][0].tel.pos - 5.0) < 0.01, 8),
            self.ctrl.snapshot())

    def test_forced_move_in_error(self):
        self.assertTrue(spin_until(self.master_ready, 8))
        self.ctrl.module_devs[0].request('ESTOP 6', urgent=True)
        self.assertTrue(spin_until(
            lambda: self.ctrl.snapshot()[1][0].tel.state == STATE_ERROR, 3))

        # normale Fahrt abgelehnt, erzwungene geht und das Modul bleibt im Fehler
        self.assertFalse(self.command('JOG', 3.0)[0])
        self.assertEqual(self.command('FJOG', 3.0), (True, 'OK'))
        self.assertTrue(spin_until(lambda: abs(self.ctrl.snapshot()[1][0].tel.pos - 3.0) < 0.01, 3))
        self.assertEqual(self.ctrl.snapshot()[1][0].tel.state, STATE_ERROR)


class SingleNanoTest(unittest.TestCase):
    """Standard der Firmware: nur Achse 0 benutzt, ein Nano reicht."""

    def test_one_nano_ready_and_ack(self):
        bus = SimBus()
        ctrl = Controller(transport_factory=bus.open)
        ok = []
        ctrl.connect_master('SIM-ESP32', lambda o, t: ok.append(o))
        ctrl.connect_module('SIM-NANO-A', 0, lambda o, t: ok.append(o))
        self.assertTrue(spin_until(lambda: len(ok) == 2) and all(ok))

        def ready():
            m, axes = ctrl.snapshot()
            return m.summary.state == STATE_READY and axes[0].tel.state == STATE_READY
        self.assertTrue(spin_until(ready, 8), ctrl.snapshot())
        self.assertNotIn(1, ctrl.snapshot()[1])            # Achse 1 taucht nicht auf
        self.assertEqual(ctrl.snapshot()[0].missing_axes(), [])

        # Nano kurz weg -> Fehler, wieder da -> quittieren -> READY
        ctrl.disconnect_module(0)
        self.assertTrue(spin_until(
            lambda: ctrl.snapshot()[0].summary.state == STATE_ERROR, 5))
        ok.clear()
        ctrl.connect_module('SIM-NANO-A', 0, lambda o, t: ok.append(o))
        self.assertTrue(spin_until(lambda: ok, 5) and ok[0])
        spin_until(lambda: ctrl.snapshot()[1][0].tel.state == STATE_ERROR, 3)
        spin_until(lambda: False, 0.3)
        acked = []
        ctrl.ack(lambda o, r, l: acked.append((o, r)))
        self.assertTrue(spin_until(lambda: acked, 3) and acked[0][0], acked)
        self.assertTrue(spin_until(ready, 8), ctrl.snapshot())
        ctrl.disconnect_all()
        app.processEvents()


if __name__ == '__main__':
    unittest.main()
