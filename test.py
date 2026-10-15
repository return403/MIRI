import math
import re
import time as systime

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt

import numpy as np
import pyvisa


# ============================================================
# KONFIGURATION
# ============================================================

RESOURCE = 'USB0::0xF4EC::0xEE38::SDSMMFCC6R2573::INSTR'

# Kanal
CHANNEL = 1

# ------------------------------------------------------------
# OSCILLOSCOPE-EINSTELLUNGEN
# ------------------------------------------------------------

# Vertikale Skalierung (V/div)
V_DIV = 1.5

# Horizontale Skalierung (s/div)
T_DIV = 2e-4

# ------------------------------------------------------------
# TRIGGER
# ------------------------------------------------------------

# Triggerpegel in Volt
TRIGGER_LEVEL = 1.5

# 'POS' = steigende Flanke, 'NEG' = fallende Flanke
TRIGGER_SLOPE = 'POS'

# 'DC', 'AC', 'LFREJ', 'HFREJ'
TRIGGER_COUPLING = 'DC'

# Wie lange (Sekunden) auf ein Triggerereignis gewartet wird
TRIGGER_WAIT = 10.0

# ------------------------------------------------------------
# DISPLAY / ADC
# ------------------------------------------------------------

# SDS1104X-E Display:
HORIZONTAL_DIVS = 14
VERTICAL_DIVS = 8

# SDS1000X-E: 25 ADC-Codes pro vertikaler Division
# (laut Siglent Programming Guide: U = code * VDIV/25 - OFST)
COUNTS_PER_DIV = 25.0

# ------------------------------------------------------------
# WAVEFORM
# ------------------------------------------------------------

# Es werden IMMER alle Punkte aus dem Speicher geholt
# (keine Ausdünnung). Übertragen wird in Blöcken dieser Größe,
# damit große Speicher (bis 14 Mpts) stabil über USB gehen.
CHUNK_POINTS = 1_000_000

# ------------------------------------------------------------
# PLOT
# ------------------------------------------------------------

SHOW_PLOT = True

# Plot zusätzlich als Bild speichern (None = nicht speichern)
PLOT_FILE = 'messung.png'
PLOT_DPI = 300

# Rohdaten als CSV speichern (None = nicht speichern)
CSV_FILE = None   # z.B. 'messung.csv'

# ------------------------------------------------------------
# SONSTIGES
# ------------------------------------------------------------

TIMEOUT = 30000


# ============================================================
# SIGLENT SDS1104X-E
# ============================================================

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

    # --------------------------------------------------------
    # SCPI QUERY / WRITE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Zahl aus SCPI-Antwort extrahieren
    #
    # Versteht auch Einheiten-Präfixe, z.B.
    #   '1.00GSa/s'  -> 1e9
    #   '14.0Mpts'   -> 14e6
    #   '5.00E-04S'  -> 5e-4
    # --------------------------------------------------------

    @staticmethod
    def parse_number(response: str) -> float:

        match = re.search(
            r'([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([kKMG]?)',
            response
        )

        if not match:
            raise ValueError(
                f'Keine Zahl in Antwort: {response!r}'
            )

        factor = {
            '': 1.0,
            'k': 1e3,
            'K': 1e3,
            'M': 1e6,
            'G': 1e9,
        }[match.group(2)]

        return float(match.group(1)) * factor

    # --------------------------------------------------------
    # Kanal einstellen
    # --------------------------------------------------------

    def configure_channel(self, channel: int, vdiv: float):

        ch = f'C{channel}'

        self.send(f'{ch}:TRA ON')
        self.send(f'{ch}:VDIV {vdiv}')

    # --------------------------------------------------------
    # Zeitbasis einstellen
    # --------------------------------------------------------

    def configure_timebase(self, tdiv: float):

        self.send(f'TDIV {tdiv}')

    # --------------------------------------------------------
    # Trigger einstellen (Edge-Trigger)
    # --------------------------------------------------------

    def configure_trigger(
        self,
        channel: int,
        level: float,
        slope: str = 'POS',
        coupling: str = 'DC'
    ):

        ch = f'C{channel}'

        # Flankentrigger auf diesem Kanal, kein Holdoff
        self.send(f'TRSE EDGE,SR,{ch},HT,OFF')

        self.send(f'{ch}:TRCP {coupling}')
        self.send(f'{ch}:TRLV {level}V')
        self.send(f'{ch}:TRSL {slope}')

    # --------------------------------------------------------
    # Eine getriggerte Aufnahme machen und warten
    # --------------------------------------------------------

    def acquire_single(self, wait_s: float = 10.0):

        self.send('TRMD SINGLE')

        # Erst warten, bis das Scope "scharf" ist
        # (sonst lesen wir evtl. noch den alten Status 'Stop').
        arm_deadline = systime.monotonic() + 1.0

        while systime.monotonic() < arm_deadline:

            if 'stop' not in self.ask('SAST?').lower():
                break

            systime.sleep(0.02)

        # Jetzt auf das Triggerereignis warten
        deadline = systime.monotonic() + wait_s

        while systime.monotonic() < deadline:

            if 'stop' in self.ask('SAST?').lower():
                return

            systime.sleep(0.05)

        self.send('STOP')

        raise TimeoutError(
            f'Kein Trigger innerhalb von {wait_s} s. '
            f'Liegt das Signal wirklich über/unter dem Triggerpegel?'
        )

    # --------------------------------------------------------
    # Einen Block Rohbytes auslesen
    # --------------------------------------------------------

    def read_block(
        self,
        channel: int,
        first_point: int,
        num_points: int
    ) -> bytes:

        ch = f'C{channel}'

        # SP 1 = jeden Punkt (keine Ausdünnung)
        self.send(
            f'WFSU SP,1,NP,{num_points},FP,{first_point}'
        )

        self.send(f'{ch}:WF? DAT2')

        raw = self.inst.read_raw()

        if not raw:
            raise RuntimeError(
                'Keine Daten vom Oszilloskop erhalten.'
            )

        # SCPI Binary Block: #9 + 9 Stellen Länge + Daten
        marker = raw.find(b'#')

        if marker == -1:
            raise RuntimeError(
                f'Kein SCPI-Binärblock gefunden. '
                f'Erste Bytes: {raw[:50]!r}'
            )

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
            raise RuntimeError(
                f'Falsche Datenlänge: '
                f'erwartet={data_length}, '
                f'erhalten={len(data)}'
            )

        return data

    # --------------------------------------------------------
    # Komplette Waveform holen (alle Punkte, blockweise)
    # --------------------------------------------------------

    def capture_trace(self, channel: int, chunk_points: int = 1_000_000):

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

            n = min(chunk_points, sanu - first)

            block = self.read_block(
                channel=channel,
                first_point=first,
                num_points=n
            )

            if len(block) == 0:
                break

            blocks.append(block)
            first += len(block)

            print(
                f'\r  {first:>10,} / {sanu:,} Punkte '
                f'({100.0 * first / sanu:5.1f} %)',
                end='',
                flush=True
            )

        print()

        raw = b''.join(blocks)

        if len(raw) == 0:
            raise RuntimeError('Keine Waveform-Punkte erhalten.')

        # DAT2 = 8-bit signed
        adc = np.frombuffer(raw, dtype=np.int8).astype(np.float32)

        # ----------------------------------------------------
        # SPANNUNG
        #   U = code * VDIV / 25 - OFST
        # ----------------------------------------------------

        voltage = adc * (vdiv / COUNTS_PER_DIV) - ofst

        # ----------------------------------------------------
        # ZEIT
        #   t = -TRDL - (TDIV * 14 / 2) + i / SARA
        #
        # Der Triggerzeitpunkt liegt damit bei t = 0.
        # ----------------------------------------------------

        t = (
            -trdl
            - tdiv * HORIZONTAL_DIVS / 2.0
            + np.arange(len(voltage)) / sara
        )

        info = {
            'vdiv': vdiv,
            'ofst': ofst,
            'tdiv': tdiv,
            'trdl': trdl,
            'sara': sara,
            'sanu': sanu,
        }

        return t, voltage, info


# ============================================================
# HAUPTPROGRAMM
# ============================================================

if __name__ == '__main__':

    scope = None

    try:

        print('Verbinde mit Oszilloskop...')

        scope = SiglentSDS1104XE(RESOURCE, timeout=TIMEOUT)

        print()
        print('Oszilloskop:')
        print(scope.ask('*IDN?'))

        # ----------------------------------------------------
        # SCOPE KONFIGURIEREN
        # ----------------------------------------------------

        scope.configure_channel(channel=CHANNEL, vdiv=V_DIV)
        scope.configure_timebase(T_DIV)

        scope.configure_trigger(
            channel=CHANNEL,
            level=TRIGGER_LEVEL,
            slope=TRIGGER_SLOPE,
            coupling=TRIGGER_COUPLING
        )

        print()
        print('Konfiguration:')
        print(f'CH{CHANNEL}:      {V_DIV} V/div')
        print(f'Zeitbasis:    {T_DIV} s/div')
        print(
            f'Trigger:      {scope.ask(f"C{CHANNEL}:TRLV?")}, '
            f'{TRIGGER_SLOPE}, {TRIGGER_COUPLING}'
        )

        # ----------------------------------------------------
        # AUF TRIGGER WARTEN
        # ----------------------------------------------------

        print()
        print('Warte auf Trigger...')

        scope.acquire_single(wait_s=TRIGGER_WAIT)

        print('Getriggert.')

        # ----------------------------------------------------
        # WAVEFORM AUSLESEN (alle Punkte)
        # ----------------------------------------------------

        print()
        print('Lese Waveform (alle Punkte)...')

        t_start = systime.monotonic()

        t, y, info = scope.capture_trace(
            channel=CHANNEL,
            chunk_points=CHUNK_POINTS
        )

        t_read = systime.monotonic() - t_start

        print()
        print('Ergebnis:')
        print(f'Kanal:        CH{CHANNEL}')
        print(f'Abtastrate:   {info["sara"]:.6g} Sa/s')
        print(f'Punkte:       {len(y):,}')
        print(f'Übertragung:  {t_read:.1f} s')
        print(f'Zeitbereich:  {t[0]:.6g} ... {t[-1]:.6g} s')
        print(f'Spannung:     {np.min(y):.6g} ... {np.max(y):.6g} V')

        # ----------------------------------------------------
        # CSV
        # ----------------------------------------------------

        if CSV_FILE:

            print()
            print(f'Speichere CSV: {CSV_FILE} ...')

            np.savetxt(
                CSV_FILE,
                np.column_stack((t, y)),
                delimiter=',',
                header='t [s],U [V]',
                comments='',
                fmt=['%.10e', '%.5f']
            )

        # ----------------------------------------------------
        # PLOT
        # ----------------------------------------------------

        if SHOW_PLOT or PLOT_FILE:

            plt.figure(figsize=(16, 8))

            plt.plot(t * 1000.0, y, linewidth=0.5)

            # Triggerpegel
            plt.axhline(
                TRIGGER_LEVEL,
                color='red',
                linestyle='--',
                linewidth=1,
                label=f'Trigger {TRIGGER_LEVEL} V ({TRIGGER_SLOPE})'
            )

            # Triggerzeitpunkt
            plt.axvline(
                0.0,
                color='red',
                linestyle=':',
                linewidth=1
            )

            # Achsen wie am Scope-Bildschirm
            center = -info['ofst']
            half_v = VERTICAL_DIVS / 2.0 * info['vdiv']
            half_t = HORIZONTAL_DIVS / 2.0 * info['tdiv']

            plt.ylim(center - half_v, center + half_v)
            plt.xlim(
                (-info['trdl'] - half_t) * 1000.0,
                (-info['trdl'] + half_t) * 1000.0
            )

            plt.title(
                f'Siglent SDS1104X-E – CH{CHANNEL} – '
                f'{len(y):,} Punkte @ {info["sara"]:.3g} Sa/s'
            )
            plt.xlabel('Zeit [ms]')
            plt.ylabel('Spannung [V]')
            plt.grid(True, which='both')
            plt.legend(loc='upper right')

            plt.tight_layout()

            if PLOT_FILE:
                plt.savefig(PLOT_FILE, dpi=PLOT_DPI)
                print()
                print(f'Plot gespeichert: {PLOT_FILE}')

            if SHOW_PLOT:
                plt.show()

    except Exception as exc:

        print()
        print('FEHLER:')
        print(exc)

    finally:

        if scope is not None:
            scope.close()

        print()
        print('Verbindung geschlossen.')