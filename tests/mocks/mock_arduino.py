"""Nachbildung von ESP32-Master und Nano-Achsmodulen für Simulation und Tests.

Verhält sich am Protokoll wie die Firmware (firmware/src/protocol.cpp, slave/slave_main.cpp),
stark vereinfacht: kein Encoder/Treiber, Bewegung mit konstanter Geschwindigkeit.
Alle Module hängen per USB an der GUI (link = 0).
"""

import time

from PySide6.QtCore import QObject, QTimer, Signal

from pc.devices import protocol

BOOT, HOMING, READY, ERROR = 0, 1, 2, 3
ERR_GUI_LOST, ERR_POS_NOT_REACHED, ERR_TIMEOUT, ERR_MODULE_LOST = 1, 6, 9, 10


def now_ms():
    return time.monotonic() * 1000.0


class MockTransport(QObject):
    """Gleiche Schnittstelle wie QSerialPort, soweit LineDevice sie braucht."""
    readyRead = Signal()

    def __init__(self, device):
        super().__init__()
        self.device = device
        self._out = bytearray()
        self._open = True
        device.attach(self)

    def write(self, data: bytes):
        if self._open:
            self.device.receive(bytes(data))
        return len(data)

    def readAll(self):
        data, self._out = bytes(self._out), bytearray()
        return data

    def isOpen(self):
        return self._open

    def close(self):
        self._open = False
        self.device.detach()

    def push(self, data: bytes):
        if self._open:
            self._out += data
            QTimer.singleShot(0, self, self.readyRead.emit)


class SimDevice(QObject):

    def __init__(self, params):
        super().__init__()
        self.transport = None
        self.params = dict(params)
        self.defaults = dict(params)
        self._rx = bytearray()
        self.hb_seen = False
        self.hb_at = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self.tick)

    def attach(self, transport):
        self.transport = transport
        self.reset()
        self.timer.start()

    def detach(self):
        self.transport = None
        self.timer.stop()

    def reset(self):
        pass

    def send(self, payload):
        if self.transport is not None:
            self.transport.push(protocol.encode(payload))

    def reply(self, ok, reason=''):
        self.send('OK' if ok else f'ERR {reason}')

    def receive(self, data):
        self._rx += data
        while b'\n' in self._rx:
            raw, _, rest = bytes(self._rx).partition(b'\n')
            self._rx = bytearray(rest)
            payload = protocol.decode(raw.decode('ascii', 'replace'))
            if payload is None:
                self.reply(False, 'CHK')
                continue
            self.hb_seen = True
            self.hb_at = now_ms()
            parts = payload.split()
            if parts:
                self.dispatch(parts[0], parts[1:])

    def hb_fresh(self):
        return self.hb_seen and now_ms() - self.hb_at <= self.params['hbtimeout']

    def param_command(self, cmd, args):
        if cmd == 'GET':
            if not args or args[0] not in self.params:
                self.reply(False, 'NAME')
            else:
                self.send(f'P {args[0]} {self.params[args[0]]:.3f}')
            return True
        if cmd == 'SET':
            if len(args) < 2:
                self.reply(False, 'ARG')
            elif args[0] not in self.params:
                self.reply(False, 'NAME')
            else:
                self.params[args[0]] = float(args[1])
                self.reply(True)
            return True
        if cmd == 'LIST':
            for name, value in self.params.items():
                self.send(f'P {name} {value:.3f}')
            self.reply(True)
            return True
        if cmd == 'SAVE':
            self.reply(True)
            return True
        if cmd == 'DEFAULTS':
            self.params = dict(self.defaults)
            self.reply(True)
            return True
        return False


# ==================================================================== Nano

class SimNano(SimDevice):
    HOMING_MS = 1500

    def __init__(self):
        super().__init__({
            'softmin0': -90.0, 'softmax0': 90.0, 'vmax0': 45.0, 'accel0': 180.0,
            'postol0': 0.3, 'gear0': 5.0, 'usteps0': 16.0, 'current0': 600.0,
            'hbtimeout': 1500.0, 'boottimeout': 10000.0, 'telemetry': 200.0, 'detent': 4.0,
        })

    def reset(self):
        self.state = BOOT
        self.err = 0
        self.since = now_ms()
        self.pos = 0.0
        self.target = 0.0
        self.last_tel = 0.0
        self.reported = (None, None)
        self.hb_seen = False
        self.last_tick = now_ms()
        self.forced = False

    def enter(self, state, err=0):
        self.state = state
        self.err = err
        self.since = now_ms()
        if state == ERROR:
            self.target = self.pos     # Motor stromlos
            self.forced = False

    @property
    def busy(self):
        return abs(self.target - self.pos) > 1e-6

    def tick(self):
        t = now_ms()
        dt = (t - self.last_tick) / 1000.0
        self.last_tick = t
        if (self.state in (READY, HOMING) or self.forced) and self.busy:
            step = self.params['vmax0'] * dt
            delta = self.target - self.pos
            self.pos = self.target if abs(delta) <= step else self.pos + step * (1 if delta > 0 else -1)
        if self.forced and not self.busy:
            self.forced = False

        if self.state == BOOT:
            if self.forced:
                # erzwungene Fahrt aus READY: erst danach neu referenzieren
                if not self.hb_fresh():
                    self.enter(ERROR, ERR_GUI_LOST)
                else:
                    self.since = t
            elif self.hb_fresh():
                self.enter(HOMING)
            elif t - self.since >= self.params['boottimeout']:
                self.enter(ERROR, ERR_GUI_LOST)
        elif self.state in (HOMING, READY):
            if not self.hb_fresh():
                self.enter(ERROR, ERR_GUI_LOST)
            elif self.state == HOMING and t - self.since >= self.HOMING_MS:
                self.enter(READY)
        elif self.state == ERROR and self.forced and not self.hb_fresh():
            self.target = self.pos
            self.forced = False

        changed = (self.state, self.err) != self.reported
        if changed or t - self.last_tel >= self.params['telemetry']:
            self.last_tel = t
            self.reported = (self.state, self.err)
            self.send(f'M {self.state} {self.err} {int(self.busy)} {self.pos:.2f} '
                      f'{self.target:.2f} 0 0')

    def dispatch(self, cmd, args):
        if cmd == 'HB':
            return
        if cmd == 'VER':
            self.send('V MIRI-AXIS 1.0-sim')
            return
        if cmd == 'STATUS':
            self.reported = (None, None)
            return
        if self.param_command(cmd, args):
            return
        if cmd in ('MOVE', 'JOG'):
            try:
                value = float(args[0])
            except (IndexError, ValueError):
                self.reply(False, 'ARG')
                return
            goal = value if cmd == 'MOVE' else self.target + value
            ok = (self.state == READY
                  and self.params['softmin0'] <= goal <= self.params['softmax0'])
            if ok:
                self.target = goal
            self.reply(ok, 'STATE')
            return
        if cmd in ('FMOVE', 'FJOG'):
            try:
                value = float(args[0])
            except (IndexError, ValueError):
                self.reply(False, 'ARG')
                return
            ok = self.state in (READY, ERROR) or self.forced
            if ok:
                self.target = value if cmd == 'FMOVE' else self.target + value
                self.forced = True
                if self.state == READY:
                    self.enter(BOOT)
            self.reply(ok, 'STATE')
            return
        if cmd == 'HOME':
            ok = self.state == READY
            if ok:
                self.target = 0.0
            self.reply(ok, 'STATE')
            return
        if cmd == 'STOP':
            ok = self.state == READY or self.forced
            if ok:
                self.target = self.pos
            self.reply(ok, 'STATE')
            return
        if cmd == 'ZERO':
            ok = self.state == READY and not self.busy
            if ok:
                self.pos = self.target = 0.0
            self.reply(ok, 'STATE')
            return
        if cmd == 'CAL':
            ok = self.state == READY and not self.busy
            if ok:
                self.enter(HOMING)
            self.reply(ok, 'STATE')
            return
        if cmd == 'ESTOP':
            code = int(float(args[0])) if args else ERR_GUI_LOST
            if self.state != ERROR:
                self.enter(ERROR, code)
            else:
                self.target = self.pos
                self.forced = False
            self.reply(True)
            return
        if cmd == 'ACK':
            ok = (self.state == ERROR and args and int(float(args[0])) == self.err
                  and self.hb_fresh())
            if ok:
                self.enter(BOOT)
            self.reply(bool(ok), 'STATE')
            return
        self.reply(False, 'UNK')


# ==================================================================== ESP32

class SimMaster(SimDevice):
    AXIS_COUNT = 2

    def __init__(self, key_on=True):
        params = {}
        for a in range(self.AXIS_COUNT):
            params[f'jog{a}'] = 1.0
            params[f'jogfine{a}'] = 0.1
            params[f'link{a}'] = 0.0 if a == 0 else 2.0     # wie Firmware: nur Achse 0 benutzt
        params.update({'hbtimeout': 1500.0, 'boottimeout': 10000.0, 'telemetry': 200.0,
                       'detent': 4.0})
        super().__init__(params)
        self.key = key_on

    def reset(self):
        self.state = BOOT
        self.err = 0
        self.err_axis = 0
        self.since = now_ms()
        self.armed = False
        self.reports = {}       # achse -> (zeit, state, err)
        self.last_tel = 0.0
        self.reported = (None, None)
        self.hb_seen = False

    def used(self, a):
        return int(self.params[f'link{a}']) != 2

    def used_axes(self):
        return [a for a in range(self.AXIS_COUNT) if self.used(a)]

    def axis_alive(self, a):
        r = self.reports.get(a)
        return r is not None and now_ms() - r[0] <= self.params['hbtimeout']

    def axis_state(self, a):
        r = self.reports.get(a)
        return r[1] if r else BOOT

    def module_fault(self):
        for a in self.used_axes():
            if not self.axis_alive(a):
                return ERR_MODULE_LOST, a
            r = self.reports[a]
            if r[1] == ERROR:
                return (r[2] or ERR_MODULE_LOST), a
        return 0, 0

    def enter(self, state, err=0, axis=0):
        self.state = state
        self.err = err
        self.err_axis = axis
        self.since = now_ms()
        if state != READY:
            self.armed = False

    def tick(self):
        t = now_ms()
        if self.state == BOOT:
            fault, axis = self.module_fault()
            if fault == 0 and self.hb_fresh():
                self.enter(HOMING)
            elif t - self.since >= self.params['boottimeout']:
                self.enter(ERROR, fault or ERR_GUI_LOST, axis)
        elif self.state in (HOMING, READY):
            fault, axis = self.module_fault()
            if fault == 0 and not self.hb_fresh():
                fault, axis = ERR_GUI_LOST, 0
            if fault:
                self.enter(ERROR, fault, axis)
            elif all(self.axis_state(a) == READY for a in self.used_axes()):
                if self.state == HOMING:
                    self.enter(READY)
            elif self.state == READY:
                self.enter(HOMING)

        changed = (self.state, self.err) != self.reported
        if changed or t - self.last_tel >= self.params['telemetry']:
            self.last_tel = t
            self.reported = (self.state, self.err)
            laser = self.state == READY and self.armed and self.key
            self.send(f'S {self.state} {self.err} {self.err_axis} {int(self.armed)} '
                      f'{int(self.key)} {int(laser)} 0 0')
            for a in range(self.AXIS_COUNT):
                self.send(f'A {a} 0 0.00 0.00 0 0 {self.axis_state(a)} '
                          f'{int(self.axis_alive(a))} {int(self.params[f"link{a}"])}')

    def dispatch(self, cmd, args):
        if cmd == 'HB':
            return
        if cmd == 'VER':
            self.send('V MIRI-MASTER 1.0-sim')
            return
        if cmd == 'STATUS':
            self.reported = (None, None)
            return
        if cmd == 'AXIS':
            a, st, er = int(args[0]), int(args[1]), int(args[2])
            if a >= self.AXIS_COUNT:
                self.reply(False, 'ARG')
            elif not self.used(a):
                self.reply(False, 'STATE')
            else:
                self.reports[a] = (now_ms(), st, er)
            return
        if self.param_command(cmd, args):
            return
        if cmd in ('MOVE', 'JOG', 'HOME', 'ZERO', 'CAL'):
            self.reply(False, 'STATE' if self.state != READY else 'EXTERN')
            return
        if cmd in ('FMOVE', 'FJOG'):
            self.reply(False, 'STATE' if self.armed else 'EXTERN')
            return
        if cmd == 'STOP':
            self.reply(True)
            return
        if cmd == 'ARM':
            on = args and args[0] == '1'
            ok = not on or (self.state == READY and self.key)
            if ok:
                self.armed = bool(on)
            self.reply(ok, 'STATE')
            return
        if cmd == 'ACK':
            ok = (self.state == ERROR and args and int(float(args[0])) == self.err
                  and self.hb_fresh()
                  and all(self.axis_alive(a) for a in self.used_axes()))
            if ok:
                self.enter(BOOT)
            self.reply(bool(ok), 'STATE')
            return
        self.reply(False, 'UNK')


# ==================================================================== Ports

class SimBus:
    """Simulierte serielle Ports: ein ESP32 und zwei Nanos."""

    def __init__(self):
        self.devices = {'SIM-ESP32': SimMaster(), 'SIM-NANO-A': SimNano(),
                        'SIM-NANO-B': SimNano()}

    def list_ports(self):
        return [(name, 'ESP32' if 'ESP32' in name else 'Nano') for name in self.devices]

    def open(self, name):
        if name not in self.devices:
            raise IOError(f'{name}: kein simulierter Port')
        dev = self.devices[name]
        if dev.transport is not None:
            raise IOError(f'{name}: schon geöffnet')
        return MockTransport(dev)
