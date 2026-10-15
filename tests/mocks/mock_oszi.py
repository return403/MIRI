"""Nachbildung des SDS1104X-E (gleiche Methoden wie pc.devices.siglent.SiglentSDS1104XE)."""

import time

import numpy as np

from pc.devices.siglent import HORIZONTAL_DIVS, Cancelled

SIM_POINTS = 1_400_000


class MockScope:
    RESOURCE = 'SIM::SDS1104X-E::INSTR'

    def __init__(self, resource, timeout=30000):
        self.cfg = {'vdiv': 1.0, 'ofst': 0.0, 'tdiv': 2e-4, 'trdl': 0.0, 'level': 1.0}
        self.shots = 0

    def ask(self, command):
        if command == '*IDN?':
            return 'Siglent Technologies,SDS1104X-E,SIMULATION,8.2'
        return ''

    def close(self):
        pass

    def configure_channel(self, channel, vdiv, offset=0.0):
        self.cfg.update(vdiv=vdiv, ofst=offset)

    def configure_timebase(self, tdiv, delay=0.0):
        self.cfg.update(tdiv=tdiv, trdl=delay)

    def configure_trigger(self, channel, level, slope='POS', coupling='DC'):
        self.cfg['level'] = level

    def arm_single(self):
        pass

    def wait_triggered(self, wait_s=10.0, cancel=None):
        end = time.monotonic() + min(0.3, wait_s)
        while time.monotonic() < end:
            if cancel is not None and cancel():
                raise Cancelled()
            time.sleep(0.02)

    def acquire_single(self, wait_s=10.0, cancel=None):
        self.wait_triggered(wait_s, cancel)

    def capture_trace(self, channel, chunk_points=1_000_000, progress=None, cancel=None):
        c = self.cfg
        n = SIM_POINTS
        sara = n / (HORIZONTAL_DIVS * c['tdiv'])
        for done in range(0, n, chunk_points):
            if cancel is not None and cancel():
                raise Cancelled()
            time.sleep(0.05)
            if progress is not None:
                progress(min(n, done + chunk_points), n)
        self.shots += 1
        t = -c['trdl'] - c['tdiv'] * HORIZONTAL_DIVS / 2.0 + np.arange(n) / sara
        amp = 2.0 + 0.5 * np.sin(self.shots)
        tau = 2.0 * c['tdiv']
        y = np.where(t >= 0, amp * np.exp(-t / tau), 0.0)
        y = (y + np.random.default_rng(self.shots).normal(0, 0.05, n)).astype(np.float32)
        info = {'vdiv': c['vdiv'], 'ofst': c['ofst'], 'tdiv': c['tdiv'], 'trdl': c['trdl'],
                'sara': sara, 'sanu': n}
        return t, y, info
