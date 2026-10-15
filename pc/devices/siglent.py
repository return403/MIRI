"""Siglent SDS1104X-E über VISA. Auslesen wie in test.py (alle Punkte, blockweise, DAT2)."""

import re
import time as systime

import numpy as np
import pyvisa

# SDS1104X-E Display
HORIZONTAL_DIVS = 14
VERTICAL_DIVS = 8

# SDS1000X-E: 25 ADC-Codes pro vertikaler Division (U = code * VDIV/25 - OFST)
COUNTS_PER_DIV = 25.0

TRIGGER_SLOPES = ['POS', 'NEG']
TRIGGER_COUPLINGS = ['DC', 'AC', 'LFREJ', 'HFREJ']


def list_resources():
    rm = pyvisa.ResourceManager('@py')
    try:
        return list(rm.list_resources())
    finally:
        rm.close()


class Cancelled(Exception):
    pass


class SiglentSDS1104XE:

    def __init__(self, resource: str, timeout: int = 30000):
        self.rm = pyvisa.ResourceManager('@py')
        self.inst = self.rm.open_resource(resource)
        self.inst.timeout = timeout

        # Bei Binärdaten ist read_termination besser deaktiviert.
        self.inst.write_termination = '\n'
        self.inst.read_termination = None

        # Antworten ohne Header ("C1:VDIV ...") -> leichter zu parsen
        self.send('CHDR OFF')

    # -------------------------------------------------------- SCPI

    def ask(self, command: str) -> str:
        old_termination = self.inst.read_termination
        self.inst.read_termination = '\n'
        try:
            return self.inst.query(command).strip()
        finally:
            self.inst.read_termination = old_termination

    def send(self, command: str):
        return self.inst.write(command)

    def close(self):
        try:
            self.inst.close()
        finally:
            self.rm.close()

    @staticmethod
    def parse_number(response: str) -> float:
        """'1.00GSa/s' -> 1e9, '14.0Mpts' -> 14e6, '5.00E-04S' -> 5e-4"""
        match = re.search(r'([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([kKMG]?)', response)
        if not match:
            raise ValueError(f'Keine Zahl in Antwort: {response!r}')
        factor = {'': 1.0, 'k': 1e3, 'K': 1e3, 'M': 1e6, 'G': 1e9}[match.group(2)]
        return float(match.group(1)) * factor

    # -------------------------------------------------------- Einstellungen

    def configure_channel(self, channel: int, vdiv: float, offset: float = 0.0):
        ch = f'C{channel}'
        self.send(f'{ch}:TRA ON')
        self.send(f'{ch}:VDIV {vdiv}')
        self.send(f'{ch}:OFST {offset}V')

    def configure_timebase(self, tdiv: float, delay: float = 0.0):
        self.send(f'TDIV {tdiv}')
        self.send(f'TRDL {delay}S')

    def configure_trigger(self, channel: int, level: float, slope: str = 'POS',
                          coupling: str = 'DC'):
        ch = f'C{channel}'
        # Flankentrigger auf diesem Kanal, kein Holdoff
        self.send(f'TRSE EDGE,SR,{ch},HT,OFF')
        self.send(f'{ch}:TRCP {coupling}')
        self.send(f'{ch}:TRLV {level}V')
        self.send(f'{ch}:TRSL {slope}')

    # -------------------------------------------------------- Aufnahme

    def arm_single(self):
        """Einzelaufnahme scharf schalten und warten, bis das Scope wirklich scharf ist."""
        self.send('TRMD SINGLE')
        # sonst lesen wir evtl. noch den alten Status 'Stop'
        arm_deadline = systime.monotonic() + 1.0
        while systime.monotonic() < arm_deadline:
            if 'stop' not in self.ask('SAST?').lower():
                break
            systime.sleep(0.02)

    def wait_triggered(self, wait_s: float = 10.0, cancel=None):
        deadline = systime.monotonic() + wait_s
        while systime.monotonic() < deadline:
            if 'stop' in self.ask('SAST?').lower():
                return
            if cancel is not None and cancel():
                self.send('STOP')
                raise Cancelled()
            systime.sleep(0.05)
        self.send('STOP')
        raise TimeoutError(f'Kein Trigger innerhalb von {wait_s} s. '
                           f'Liegt das Signal wirklich über/unter dem Triggerpegel?')

    def acquire_single(self, wait_s: float = 10.0, cancel=None):
        self.arm_single()
        self.wait_triggered(wait_s, cancel)

    def read_block(self, channel: int, first_point: int, num_points: int) -> bytes:
        ch = f'C{channel}'
        # SP 1 = jeden Punkt (keine Ausdünnung)
        self.send(f'WFSU SP,1,NP,{num_points},FP,{first_point}')
        self.send(f'{ch}:WF? DAT2')

        raw = self.inst.read_raw()
        if not raw:
            raise RuntimeError('Keine Daten vom Oszilloskop erhalten.')

        # SCPI Binary Block: #9 + 9 Stellen Länge + Daten
        marker = raw.find(b'#')
        if marker == -1:
            raise RuntimeError(f'Kein SCPI-Binärblock gefunden. Erste Bytes: {raw[:50]!r}')
        if len(raw) < marker + 2:
            raise RuntimeError('Ungültiger SCPI-Binärblock.')

        num_digits = int(chr(raw[marker + 1]))
        header_start = marker + 2
        header_end = header_start + num_digits
        if len(raw) < header_end:
            raise RuntimeError('Unvollständiger SCPI-Header.')

        data_length = int(raw[header_start:header_end])
        data_start = header_end
        data_end = data_start + data_length

        # Falls nicht alles auf einmal kam: nachlesen
        chunks = [raw]
        received = len(raw)
        while received < data_end:
            chunk = self.inst.read_raw()
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
        raw = b''.join(chunks)

        data = raw[data_start:data_end]
        if len(data) != data_length:
            raise RuntimeError(f'Falsche Datenlänge: erwartet={data_length}, '
                               f'erhalten={len(data)}')
        return data

    def capture_trace(self, channel: int, chunk_points: int = 1_000_000, progress=None,
                      cancel=None):
        """Komplette Waveform (alle Punkte). progress(gelesen, gesamt), cancel() -> bool"""
        ch = f'C{channel}'

        vdiv = self.parse_number(self.ask(f'{ch}:VDIV?'))
        ofst = self.parse_number(self.ask(f'{ch}:OFST?'))
        tdiv = self.parse_number(self.ask('TDIV?'))
        trdl = self.parse_number(self.ask('TRDL?'))
        sara = self.parse_number(self.ask('SARA?'))
        sanu = int(round(self.parse_number(self.ask(f'SANU? {ch}'))))

        blocks = []
        first = 0
        while first < sanu:
            if cancel is not None and cancel():
                raise Cancelled()
            n = min(chunk_points, sanu - first)
            block = self.read_block(channel=channel, first_point=first, num_points=n)
            if len(block) == 0:
                break
            blocks.append(block)
            first += len(block)
            if progress is not None:
                progress(first, sanu)

        raw = b''.join(blocks)
        if len(raw) == 0:
            raise RuntimeError('Keine Waveform-Punkte erhalten.')

        # DAT2 = 8-bit signed
        adc = np.frombuffer(raw, dtype=np.int8).astype(np.float32)

        # U = code * VDIV / 25 - OFST
        voltage = adc * (vdiv / COUNTS_PER_DIV) - ofst

        # t = -TRDL - (TDIV * 14 / 2) + i / SARA   -> Triggerzeitpunkt bei t = 0
        t = -trdl - tdiv * HORIZONTAL_DIVS / 2.0 + np.arange(len(voltage)) / sara

        info = {'vdiv': vdiv, 'ofst': ofst, 'tdiv': tdiv, 'trdl': trdl, 'sara': sara,
                'sanu': sanu}
        return t, voltage, info
