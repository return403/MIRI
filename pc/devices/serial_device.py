"""Eine serielle Gegenstelle (Master oder Achsmodul) mit Zeilenprotokoll.

Läuft komplett im Qt-Hauptthread (QSerialPort, ereignisgesteuert). Anfragen werden der Reihe
nach gestellt, immer nur eine offen. Die Firmware beantwortet jede Anfrage mit genau einer
Zeile (OK, ERR ..., P ..., V ...), LIST mit mehreren P-Zeilen und einem OK am Ende.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtSerialPort import QSerialPort

from . import protocol

BAUD = 115200
REQUEST_TIMEOUT_MS = 3000       # Master wartet selbst max. 1,5 s auf ein Modul


@dataclass
class Request:
    command: str
    callback: Optional[Callable[[bool, str, list], None]]
    multi: bool = False             # LIST: P-Zeilen sammeln bis OK
    timeout_ms: int = REQUEST_TIMEOUT_MS
    lines: list = field(default_factory=list)
    deadline: float = 0.0


def open_serial_port(name: str):
    port = QSerialPort()
    port.setPortName(name)
    port.setBaudRate(BAUD)
    port.setDataBits(QSerialPort.Data8)
    port.setParity(QSerialPort.NoParity)
    port.setStopBits(QSerialPort.OneStop)
    port.setFlowControl(QSerialPort.NoFlowControl)
    if not port.open(QSerialPort.ReadWrite):
        raise IOError(f'{name}: {port.errorString()}')
    # ESP32-Autoreset (DTR/RTS) nicht auslösen
    port.setDataTerminalReady(False)
    port.setRequestToSend(False)
    return port


class LineDevice(QObject):
    telemetry = Signal(str)         # S/A/W/M-Zeilen (Nutzdaten ohne Prüfsumme)
    log = Signal(str)
    closed = Signal()

    def __init__(self, name: str, transport, parent=None):
        super().__init__(parent)
        self.name = name
        self.transport = transport
        self._buffer = bytearray()
        self._queue = deque()
        self._pending: Optional[Request] = None
        self.last_rx = 0.0

        transport.readyRead.connect(self._on_ready_read)
        if hasattr(transport, 'errorOccurred'):
            transport.errorOccurred.connect(self._on_error)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._check_timeout)
        self._timer.start()

    # ------------------------------------------------------------ senden

    def send(self, payload: str):
        """Abschicken ohne Antwort (HB, STATUS, AXIS)."""
        if self.transport is not None and self.transport.isOpen():
            self.transport.write(protocol.encode(payload))

    def request(self, command: str, callback=None, multi=False, timeout_ms=REQUEST_TIMEOUT_MS,
                urgent=False):
        """callback(ok, antwortzeile, gesammelte_P_zeilen)"""
        req = Request(command, callback, multi, timeout_ms)
        if urgent:
            self._queue.appendleft(req)
        else:
            self._queue.append(req)
        self._next()

    def _next(self):
        if self._pending is not None or not self._queue:
            return
        self._pending = self._queue.popleft()
        self._pending.deadline = time.monotonic() + self._pending.timeout_ms / 1000.0
        self.send(self._pending.command)

    def _finish(self, ok: bool, reply: str):
        req, self._pending = self._pending, None
        if not ok:
            self.log.emit(f'{self.name}: {req.command} -> {reply}')
        if req.callback is not None:
            try:
                req.callback(ok, reply, req.lines)
            except Exception as exc:     # Fehler im Callback darf den Port nicht blockieren
                self.log.emit(f'{self.name}: Fehler in Rückruf: {exc}')
        self._next()

    def _check_timeout(self):
        if self._pending is not None and time.monotonic() > self._pending.deadline:
            self._finish(False, 'TIMEOUT (keine Antwort)')

    # ------------------------------------------------------------ empfangen

    def _on_ready_read(self):
        self._buffer += bytes(self.transport.readAll())
        while True:
            nl = self._buffer.find(b'\n')
            if nl < 0:
                break
            raw = self._buffer[:nl].decode('ascii', errors='replace').strip()
            del self._buffer[:nl + 1]
            if raw:
                self._handle(raw)
        if len(self._buffer) > 4096:      # Müll ohne Zeilenende
            self._buffer.clear()

    def _handle(self, raw: str):
        payload = protocol.decode(raw)
        if payload is None:
            # z. B. Bootmeldungen des ESP32 oder Störungen
            self.log.emit(f'{self.name}: ungültige Zeile {raw!r}')
            return
        self.last_rx = time.monotonic()

        if not protocol.is_reply(payload):
            self.telemetry.emit(payload)
            return

        req = self._pending
        if req is None:
            self.log.emit(f'{self.name}: unerwartet {payload}')
            return

        if req.multi and payload.startswith('P '):
            req.lines.append(payload)
            req.deadline = time.monotonic() + req.timeout_ms / 1000.0
            return
        if payload.startswith('ERR'):
            self._finish(False, payload)
        else:
            self._finish(True, payload)

    # ------------------------------------------------------------ Verbindung

    def _on_error(self, error):
        if error == QSerialPort.ResourceError:
            self.log.emit(f'{self.name}: Verbindung verloren')
            self.close()

    def close(self):
        if self.transport is None:
            return
        self._timer.stop()
        try:
            self.transport.close()
        finally:
            self.transport = None
        while self._pending is not None or self._queue:
            if self._pending is None:
                self._pending = self._queue.popleft()
            self._finish(False, 'getrennt')
        self.closed.emit()

    @property
    def is_open(self) -> bool:
        return self.transport is not None and self.transport.isOpen()
