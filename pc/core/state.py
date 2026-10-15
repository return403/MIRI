"""Zustandsabbild von Master und Achsen, wie es die GUI aus der Telemetrie kennt."""

import time
from dataclasses import dataclass, field

from ..devices.protocol import AxisTelemetry, MasterSummary

SOURCE_NONE = ''
SOURCE_USB = 'USB'          # Nano direkt an der GUI (link = 0)
SOURCE_MASTER = 'ESP32'     # Nano am UART des ESP32 (link = 1)


@dataclass
class AxisState:
    axis: int
    source: str = SOURCE_NONE
    tel: AxisTelemetry = field(default_factory=AxisTelemetry)
    alive: bool = False
    updated: float = 0.0            # time.monotonic() der letzten Telemetrie
    port: str = ''                  # bei USB-Modulen

    def age(self) -> float:
        return time.monotonic() - self.updated if self.updated else float('inf')


@dataclass
class MasterState:
    connected: bool = False
    port: str = ''
    version: str = ''
    summary: MasterSummary = field(default_factory=MasterSummary)
    updated: float = 0.0
    # pro Achse aus den A-Zeilen: link (True = Modul am UART des ESP32)
    links: dict = field(default_factory=dict)
    # pro Achse aus den A-Zeilen: modul_da (der ESP32 hört das Modul)
    axis_alive: dict = field(default_factory=dict)

    def missing_axes(self):
        """Achsen, die der ESP32 erwartet, aber nicht hört (ACK/READY unmöglich)."""
        return sorted(a for a, alive in self.axis_alive.items() if not alive)
