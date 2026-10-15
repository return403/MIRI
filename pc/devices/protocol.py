"""Zeilenprotokoll von Master (ESP32) und Achsmodul (Nano).

Zeile: <Nutzdaten>*<HH>\\n, HH = XOR aller Zeichen der Nutzdaten (siehe firmware/src/protocol.h).
"""

from dataclasses import dataclass

STATE_NAMES = {0: 'BOOT', 1: 'HOMING', 2: 'READY', 3: 'ERROR'}
STATE_BOOT, STATE_HOMING, STATE_READY, STATE_ERROR = 0, 1, 2, 3

# link<n> am Master
LINK_GUI, LINK_UART, LINK_UNUSED = 0, 1, 2

ERROR_NAMES = {
    0: 'kein Fehler',
    1: 'GUI weg',
    2: 'Encoder-Verbindung',
    3: 'Magnet',
    4: 'Treiber-Verbindung',
    5: 'Treiberfehler',
    6: 'Position nicht erreicht',
    7: 'Schleppfehler',
    8: 'Kalibrierung',
    9: 'Zeitüberschreitung',
    10: 'Modul weg',
}

ENC_NAMES = {0: 'ok', 1: 'kein Chip', 2: 'kein Magnet', 3: 'zu stark', 4: 'zu schwach'}
DRV_NAMES = {0: 'ok', 1: 'keine UART', 2: 'Übertemp.-Warnung', 3: 'Übertemp.', 4: 'Treiberfehler'}

# Antworten auf Befehle. Alles andere (S, A, W, M) ist Telemetrie.
REPLY_TAGS = ('OK', 'ERR', 'P', 'V')


def checksum(payload: str) -> int:
    cs = 0
    for b in payload.encode('ascii'):
        cs ^= b
    return cs


def encode(payload: str) -> bytes:
    return f'{payload}*{checksum(payload):02X}\n'.encode('ascii')


def decode(line: str):
    """Nutzdaten einer empfangenen Zeile, None bei fehlender/falscher Prüfsumme."""
    line = line.strip()
    star = line.rfind('*')
    if star < 0 or len(line) - star != 3:
        return None
    payload = line[:star]
    try:
        cs = int(line[star + 1:], 16)
    except ValueError:
        return None
    return payload if cs == checksum(payload) else None


def fmt_float(value: float) -> str:
    # Die Firmware versteht keine Exponenten, nur [-]ziffern[.ziffern]
    text = f'{value:.3f}'
    return '0.000' if text == '-0.000' else text


def is_reply(payload: str) -> bool:
    return payload.split(' ', 1)[0] in REPLY_TAGS


@dataclass
class MasterSummary:
    state: int = STATE_BOOT
    err: int = 0
    err_axis: int = 0
    armed: bool = False
    key: bool = False
    laser: bool = False
    fine: bool = False
    wheel_axis: int = 0


@dataclass
class AxisTelemetry:
    state: int = STATE_BOOT
    err: int = 0
    busy: bool = False
    pos: float = 0.0
    target: float = 0.0
    enc: int = 0
    drv: int = 0


def parse_summary(payload: str) -> MasterSummary:
    # S <state> <err> <fehlerachse> <arm> <key> <laser> <fein> <handradachse>
    f = payload.split()
    return MasterSummary(int(f[1]), int(f[2]), int(f[3]), f[4] == '1', f[5] == '1',
                         f[6] == '1', f[7] == '1', int(f[8]))


def parse_master_axis(payload: str):
    # A <achse> <busy> <pos> <ziel> <enc> <drv> <modulstate> <modul_da> <link>
    # link: 0 GUI, 1 UART, 2 nicht benutzt
    f = payload.split()
    axis = int(f[1])
    tel = AxisTelemetry(state=int(f[7]), err=0, busy=f[2] == '1', pos=float(f[3]),
                        target=float(f[4]), enc=int(f[5]), drv=int(f[6]))
    return axis, tel, f[8] == '1', int(f[9])


def parse_module(payload: str) -> AxisTelemetry:
    # M <state> <err> <busy> <pos> <ziel> <enc> <drv>
    f = payload.split()
    return AxisTelemetry(int(f[1]), int(f[2]), f[3] == '1', float(f[4]), float(f[5]),
                         int(f[6]), int(f[7]))


def parse_wheel(payload: str):
    # W <achse> <grad>
    f = payload.split()
    return int(f[1]), float(f[2])


def parse_param(payload: str):
    # P <name> <wert>
    f = payload.split()
    return f[1], float(f[2])
