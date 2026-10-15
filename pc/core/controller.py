"""Steuerung von ESP32 (Master) und beliebig vielen Achsmodulen (Nano).

Alles hier läuft im Qt-Hauptthread. Andere Threads (Messablauf) benutzen nur run_blocking()
und snapshot().

Pflichten der GUI laut firmware/src/protocol.h, für Module die per USB an der GUI hängen
(link = 0 am Master):
  - jedem Gerät regelmäßig ein Lebenszeichen schicken (HB)
  - dem Master den Zustand des Moduls melden: AXIS <achse> <state> <err>
  - dem Modul ESTOP senden, sobald der Master ERROR meldet
  - nach ACK am Master auch das Modul quittieren (ACK <code des Moduls>)
  - Handrad: Master meldet "W <achse> <grad>", die GUI schickt "JOG <grad>" ans Modul
"""

import copy
import threading
import time
from concurrent.futures import Future

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal

from ..devices import protocol
from ..devices.protocol import STATE_BOOT, STATE_ERROR, STATE_READY, fmt_float
from ..devices.serial_device import LineDevice, open_serial_port
from .state import SOURCE_MASTER, SOURCE_NONE, SOURCE_USB, AxisState, MasterState

DEFAULT_HB_TIMEOUT_MS = 1500
MODULE_BOOT_DELAY_MS = 1800     # Nano startet beim Öffnen des Ports neu (Bootloader)
VER_RETRIES = 4


class Controller(QObject):
    log = Signal(str)
    devices_changed = Signal()
    _invoke = Signal(object)

    def __init__(self, transport_factory=open_serial_port, parent=None):
        super().__init__(parent)
        self.transport_factory = transport_factory
        self._lock = threading.RLock()

        self.master_dev = None
        self.master = MasterState()
        self.module_devs = {}           # achse -> LineDevice (nur verifizierte)
        self._connecting = {}           # achse -> LineDevice (VER läuft noch)
        self.axes = {}                  # achse -> AxisState
        self.hb_timeout_ms = DEFAULT_HB_TIMEOUT_MS
        self._hb_values = {}            # gerätename -> hbtimeout in ms
        self._estop_sent = set()
        self._link_warned = set()

        self._invoke.connect(self._run_invoked, Qt.QueuedConnection)

        self._hb_timer = QTimer(self)
        self._hb_timer.timeout.connect(self._heartbeat)
        self._apply_hb_interval()
        self._hb_timer.start()

    # ================================================================ Verbindungen

    def connect_master(self, port: str, done=None):
        """done(ok, text)"""
        if self.master_dev is not None:
            self.disconnect_master()
        try:
            transport = self.transport_factory(port)
        except Exception as exc:
            self._report(done, False, str(exc))
            return
        dev = LineDevice(f'ESP32 {port}', transport, self)
        dev.log.connect(self.log)
        dev.telemetry.connect(self._on_master_line)
        dev.closed.connect(self._on_master_closed)
        self.master_dev = dev
        with self._lock:
            self.master = MasterState(port=port)

        def verified(ok, text):
            if not ok:
                self.disconnect_master()
                self._report(done, False, text)
                return
            with self._lock:
                self.master.connected = True
                self.master.version = text
            self.log.emit(f'ESP32 an {port}: {text}')
            self._query_hbtimeout(dev)
            dev.send('STATUS')
            self.devices_changed.emit()
            self._report(done, True, text)

        self._verify(dev, 'MIRI-MASTER', verified, delay_ms=0)

    def disconnect_master(self):
        dev, self.master_dev = self.master_dev, None
        if dev is not None:
            self._forget_hbtimeout(dev)
            dev.close()
        with self._lock:
            self.master = MasterState()
            for st in self.axes.values():
                if st.source == SOURCE_MASTER:
                    st.source = SOURCE_NONE
                    st.alive = False
        self.devices_changed.emit()

    def _on_master_closed(self):
        if self.master_dev is not None and not self.master_dev.is_open:
            self.disconnect_master()

    def connect_module(self, port: str, axis: int, done=None):
        if axis in self.module_devs or axis in self._connecting:
            self._report(done, False, f'Achse {axis} ist schon einem Modul zugeordnet')
            return
        try:
            transport = self.transport_factory(port)
        except Exception as exc:
            self._report(done, False, str(exc))
            return
        dev = LineDevice(f'Nano A{axis} {port}', transport, self)
        dev.log.connect(self.log)
        dev.telemetry.connect(lambda payload, a=axis: self._on_module_line(a, payload))
        dev.closed.connect(lambda a=axis, d=dev: self._on_module_closed(a, d))
        self._connecting[axis] = dev

        def verified(ok, text):
            self._connecting.pop(axis, None)
            if not ok:
                dev.close()
                self._report(done, False, text)
                return
            self.module_devs[axis] = dev
            with self._lock:
                st = self.axes.setdefault(axis, AxisState(axis))
                st.source = SOURCE_USB
                st.port = port
            self.log.emit(f'Nano Achse {axis} an {port}: {text}')
            self._query_hbtimeout(dev)
            dev.send('STATUS')
            self.devices_changed.emit()
            self._report(done, True, text)

        self._verify(dev, 'MIRI-AXIS', verified, delay_ms=MODULE_BOOT_DELAY_MS)

    def disconnect_module(self, axis: int):
        dev = self.module_devs.pop(axis, None) or self._connecting.pop(axis, None)
        if dev is not None:
            self._forget_hbtimeout(dev)
            dev.close()
        with self._lock:
            st = self.axes.get(axis)
            if st is not None and st.source == SOURCE_USB:
                st.source = SOURCE_NONE
                st.alive = False
                st.port = ''
        self.devices_changed.emit()

    def _on_module_closed(self, axis, dev):
        if self.module_devs.get(axis) is dev:
            self.disconnect_module(axis)

    def disconnect_all(self):
        for axis in list(self.module_devs) + list(self._connecting):
            self.disconnect_module(axis)
        self.disconnect_master()

    def _verify(self, dev, expected, done, delay_ms):
        """VER schicken (mit Wiederholung), Antwort muss 'V <expected> ...' sein."""
        attempts = [0]

        def ask():
            if not dev.is_open:
                done(False, 'Port geschlossen')
                return
            attempts[0] += 1
            dev.request('VER', answer, timeout_ms=1000)

        def answer(ok, reply, _lines):
            if ok and reply.startswith('V '):
                if expected in reply:
                    done(True, reply[2:])
                else:
                    done(False, f'falsches Gerät: {reply[2:]} (erwartet {expected})')
            elif attempts[0] < VER_RETRIES:
                ask()
            else:
                done(False, f'keine Antwort auf VER ({reply})')

        QTimer.singleShot(delay_ms, ask)

    def _query_hbtimeout(self, dev):
        def answer(ok, reply, _lines):
            if ok:
                self._hb_values[dev.name] = int(protocol.parse_param(reply)[1])
                self._update_hb_timeout()
        dev.request('GET hbtimeout', answer)

    def _forget_hbtimeout(self, dev):
        self._hb_values.pop(dev.name, None)
        self._update_hb_timeout()

    def _update_hb_timeout(self):
        # das strengste Gerät bestimmt den Takt
        self.hb_timeout_ms = min(self._hb_values.values(), default=DEFAULT_HB_TIMEOUT_MS)
        self._apply_hb_interval()

    def _apply_hb_interval(self):
        # Firmware verlangt mindestens alle hbtimeout/2 eine Zeile, wir nehmen ein Drittel
        self._hb_timer.setInterval(max(50, min(250, self.hb_timeout_ms // 3)))

    @staticmethod
    def _report(done, ok, text):
        if done is not None:
            done(ok, text)

    # ================================================================ Telemetrie

    def _on_master_line(self, payload: str):
        tag = payload[:1]
        try:
            if tag == 'S':
                summary = protocol.parse_summary(payload)
                with self._lock:
                    self.master.summary = summary
                    self.master.updated = time.monotonic()
                if summary.state == STATE_ERROR:
                    self._estop_modules(summary.err)
                else:
                    self._estop_sent.clear()
            elif tag == 'A':
                axis, tel, alive, mode = protocol.parse_master_axis(payload)
                if mode == protocol.LINK_UNUSED:
                    self._master_axis_unused(axis)
                    return
                link = mode == protocol.LINK_UART
                with self._lock:
                    self.master.links[axis] = link
                    self.master.axis_alive[axis] = alive
                    st = self.axes.setdefault(axis, AxisState(axis))
                    if link:
                        if axis in self.module_devs and axis not in self._link_warned:
                            self._link_warned.add(axis)
                            self.log.emit(f'Achse {axis}: ESP32 meldet link=1, das USB-Modul '
                                          f'wird ignoriert (link{axis} am ESP32 auf 0 setzen)')
                        st.source = SOURCE_MASTER
                        st.tel = tel
                        st.alive = alive
                        st.updated = time.monotonic()
                    elif st.source != SOURCE_USB:
                        # Modul soll an der GUI hängen, ist aber (noch) nicht verbunden
                        st.source = SOURCE_NONE
                        st.tel.state = tel.state
                        st.alive = False
            elif tag == 'W':
                axis, degrees = protocol.parse_wheel(payload)
                self._on_wheel(axis, degrees)
        except (IndexError, ValueError):
            self.log.emit(f'ESP32: unverständliche Zeile {payload!r}')

    def _master_axis_unused(self, axis: int):
        """link<axis> = 2 am ESP32: Achse gibt es für den Master nicht."""
        with self._lock:
            self.master.links.pop(axis, None)
            self.master.axis_alive.pop(axis, None)
            st = self.axes.get(axis)
            if st is not None and st.source != SOURCE_USB:
                del self.axes[axis]
        if axis in self.module_devs and axis not in self._link_warned:
            self._link_warned.add(axis)
            self.log.emit(f'Achse {axis}: am ESP32 als „nicht benutzt“ eingestellt (link{axis} = 2). '
                          f'Der Nano läuft, aber der ESP32 überwacht ihn nicht. Zum Einbinden im '
                          f'Tab Parameter am ESP32 link{axis} = 0 setzen und speichern.')

    def _on_module_line(self, axis: int, payload: str):
        # erst nach erfolgreichem VER zählt das Modul (sonst Meldung an den Master ohne HB)
        if not payload.startswith('M ') or axis not in self.module_devs:
            return
        try:
            tel = protocol.parse_module(payload)
        except (IndexError, ValueError):
            self.log.emit(f'Nano A{axis}: unverständliche Zeile {payload!r}')
            return
        with self._lock:
            st = self.axes.setdefault(axis, AxisState(axis))
            if self.master.links.get(axis):
                return          # gehört dem ESP32, siehe Warnung oben
            changed = not st.alive or (st.tel.state, st.tel.err) != (tel.state, tel.err)
            st.source = SOURCE_USB
            st.tel = tel
            st.alive = True
            st.updated = time.monotonic()
        # Zustandswechsel sofort an den Master, nicht erst beim nächsten Takt
        if changed and self.master.connected and self.master.links.get(axis) is False:
            self.master_dev.send(f'AXIS {axis} {tel.state} {tel.err}')

    def _module_fresh(self, axis: int) -> bool:
        st = self.axes.get(axis)
        return (st is not None and st.source == SOURCE_USB
                and st.age() * 1000.0 <= self.hb_timeout_ms)

    def _heartbeat(self):
        with self._lock:
            for axis, st in self.axes.items():
                if st.source == SOURCE_USB:
                    st.alive = self._module_fresh(axis)

        # Module: Lebenszeichen
        for dev in self.module_devs.values():
            dev.send('HB')

        # Master: Zustand der USB-Module melden (zählt auch als Lebenszeichen), sonst HB.
        # Nur Achsen, die der Master kennt und die laut Master an der GUI hängen.
        # Ein stummes Modul wird nicht gemeldet -> Master erkennt "Modul weg".
        if self.master_dev is None or not self.master.connected:
            return
        reported = False
        for axis in self.module_devs:
            if self.master.links.get(axis) is False and self._module_fresh(axis):
                tel = self.axes[axis].tel
                self.master_dev.send(f'AXIS {axis} {tel.state} {tel.err}')
                reported = True
        if not reported:
            self.master_dev.send('HB')

    def _estop_modules(self, err: int):
        for axis, dev in self.module_devs.items():
            st = self.axes.get(axis)
            if axis in self._estop_sent or st is None or st.tel.state == STATE_ERROR:
                continue
            self._estop_sent.add(axis)
            s = self.master.summary
            self.log.emit(f'ESP32 im Fehlerzustand {err} ({protocol.ERROR_NAMES.get(err, "?")}, '
                          f'Achse {s.err_axis}) -> ESTOP an Nano Achse {axis}')
            dev.request(f'ESTOP {err}', urgent=True)

    def _on_wheel(self, axis: int, degrees: float):
        dev = self.module_devs.get(axis)
        st = self.axes.get(axis)
        if dev is None or st is None or st.tel.state != STATE_READY:
            return
        dev.request(f'JOG {fmt_float(degrees)}')

    # ================================================================ Befehle

    def route(self, axis: int):
        """(gerät, mit_achsnummer) für Achsbefehle, oder (None, grund)."""
        if self.master.links.get(axis) and self.master_dev is not None:
            return self.master_dev, True
        if axis in self.module_devs:
            return self.module_devs[axis], False
        return None, f'Achse {axis} nicht erreichbar'

    def axis_command(self, axis: int, verb: str, value=None, callback=None):
        """verb: MOVE, JOG, FMOVE, FJOG, HOME, ZERO, CAL, STOP. callback(ok, antwort, zeilen)

        FMOVE/FJOG: erzwungen ohne Encoder, auch im Fehlerzustand. Bei Laserfreigabe gesperrt
        (der Master prüft das selbst, für Module am USB prüfen wir es hier).
        """
        dev, via_master = self.route(axis)
        if dev is None:
            if callback is not None:
                callback(False, via_master, [])
            return
        if verb in ('FMOVE', 'FJOG') and self.master.summary.armed:
            if callback is not None:
                callback(False, 'Laserfreigabe aktiv', [])
            return
        if verb == 'STOP' and via_master:
            parts = ['STOP']            # am Master stoppt STOP immer alle Achsen
        else:
            parts = [verb] + ([str(axis)] if via_master else [])
        if value is not None:
            parts.append(fmt_float(value))
        dev.request(' '.join(parts), callback, urgent=(verb == 'STOP'))

    def stop_all(self):
        if self.master_dev is not None:
            self.master_dev.request('STOP', urgent=True)
        for dev in self.module_devs.values():
            dev.request('STOP', urgent=True)

    def arm(self, on: bool, callback=None):
        if self.master_dev is None:
            if callback is not None:
                callback(False, 'ESP32 nicht verbunden', [])
            return
        self.master_dev.request(f'ARM {1 if on else 0}', callback, urgent=not on)

    def ack(self, callback=None):
        """Fehler quittieren: erst den Master, dann die USB-Module mit ihrem eigenen Code.

        Solange der Master im Fehler bleibt, bleiben auch die Module gestoppt (ESTOP-Regel).
        Der Master nimmt ACK nur an, wenn er ALLE seine Achsen hört (allAlive in der Firmware).
        """
        def ack_modules():
            for axis, dev in self.module_devs.items():
                st = self.axes.get(axis)
                if st is not None and st.tel.state == STATE_ERROR:
                    dev.request(f'ACK {st.tel.err}',
                                lambda ok, r, l, a=axis, e=st.tel.err: self._module_ack_done(
                                    a, e, ok, r))

        summary = self.master.summary
        if self.master_dev is not None and summary.state == STATE_ERROR:
            def done(ok, reply, lines):
                if ok:
                    with self._lock:
                        self.master.summary.state = STATE_BOOT
                    self._estop_sent.clear()
                    ack_modules()
                else:
                    reply = self._master_ack_reason(reply)
                    self.log.emit(f'Quittieren abgelehnt: {reply}')
                if callback is not None:
                    callback(ok, reply, lines)
            self.master_dev.request(f'ACK {summary.err}', done)
        else:
            ack_modules()
            if callback is not None:
                callback(True, 'OK', [])

    def _master_ack_reason(self, reply):
        missing = self.master.missing_axes()
        if missing:
            not_here = [a for a in missing if a not in self.module_devs
                        and not self.master.links.get(a)]
            text = (f'{reply}: der ESP32 hört Achse(n) {missing} nicht und quittiert erst, '
                    f'wenn alle seine Achsen da sind.')
            if not_here:
                text += (f' Für Achse(n) {not_here} ist kein Nano verbunden – Nano verbinden, '
                         f'oder die Achse am ESP32 abschalten: Tab Parameter, ESP32, '
                         f'link<n> = 2, dann speichern.')
            return text
        return f'{reply}: ESP32 nicht im Fehlerzustand oder GUI-Lebenszeichen fehlt'

    def _module_ack_done(self, axis, err, ok, reply):
        if not ok:
            st = self.axes.get(axis)
            hint = ''
            if st is not None and (st.tel.enc or st.tel.drv):
                hint = (f' Encoder: {protocol.ENC_NAMES.get(st.tel.enc, st.tel.enc)}, '
                        f'Treiber: {protocol.DRV_NAMES.get(st.tel.drv, st.tel.drv)}.')
            self.log.emit(f'Nano Achse {axis}: ACK {err} abgelehnt ({reply}). Der Nano quittiert '
                          f'nur ohne Encoder-/Treiberfehler und mit passendem Fehlercode.{hint}')

    # Parameter. target = 'master' oder Achsnummer eines USB-Moduls.

    def param_device(self, target):
        if target == 'master':
            return self.master_dev
        return self.module_devs.get(target)

    def param_request(self, target, command, callback, multi=False):
        dev = self.param_device(target)
        if dev is None:
            callback(False, 'nicht verbunden', [])
            return
        dev.request(command, callback, multi=multi, timeout_ms=5000 if multi else 3000)

    # ================================================================ andere Threads

    def invoke(self, fn):
        """fn() im Hauptthread ausführen (aus jedem Thread aufrufbar)."""
        self._invoke.emit(fn)

    def _run_invoked(self, fn):
        fn()

    def run_blocking(self, start, timeout=5.0):
        """Aus einem Arbeitsthread: start(callback) im Hauptthread ausführen und auf
        callback(ok, antwort, ...) warten. Liefert (ok, antwort)."""
        if QThread.currentThread() is self.thread():
            raise RuntimeError('run_blocking nicht aus dem Hauptthread aufrufen')
        fut = Future()

        def callback(ok, reply, *_rest):
            if not fut.done():
                fut.set_result((ok, reply))

        self.invoke(lambda: start(callback))
        return fut.result(timeout)

    def snapshot(self):
        """Kopie von (master, axes) für Anzeige und Arbeitsthreads."""
        with self._lock:
            return copy.deepcopy(self.master), copy.deepcopy(self.axes)
