"""Kleine gemeinsame Widgets."""

import math
import re

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QDoubleSpinBox, QLineEdit

_PREFIX = {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'm': 1e-3, '': 1.0, 'k': 1e3,
           'M': 1e6, 'G': 1e9}


def parse_si(text: str) -> float:
    """'200us', '2e-4', '0,0002', '1.5 V', '5ms' -> float"""
    t = text.strip().replace(',', '.').replace(' ', '')
    m = re.fullmatch(r'([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)([pnuµmkMG]?)[a-zA-Z/]*', t)
    if not m:
        raise ValueError(f'keine Zahl: {text!r}')
    return float(m.group(1)) * _PREFIX[m.group(2)]


def format_si(value: float, unit: str = '') -> str:
    if value == 0:
        return f'0 {unit}'.strip()
    exp = int(math.floor(math.log10(abs(value)) / 3) * 3)
    exp = max(-12, min(9, exp))
    prefix = {-12: 'p', -9: 'n', -6: 'u', -3: 'm', 0: '', 3: 'k', 6: 'M', 9: 'G'}[exp]
    return f'{value / 10 ** exp:.6g} {prefix}{unit}'.strip()


class _SIValidator(QValidator):
    def validate(self, text, pos):
        try:
            parse_si(text)
            return QValidator.Acceptable, text, pos
        except ValueError:
            return QValidator.Intermediate, text, pos


class SIEdit(QLineEdit):
    """Zahl mit SI-Vorsatz und Einheit, z. B. '200 us' oder '1.5 V'."""

    def __init__(self, value=0.0, unit='', parent=None):
        super().__init__(parent)
        self.unit = unit
        self.setValidator(_SIValidator(self))
        self.setValue(value)
        self.editingFinished.connect(lambda: self.setValue(self.value()))

    def value(self) -> float:
        return parse_si(self.text())

    def setValue(self, value: float):
        self.setText(format_si(value, self.unit))


def spin(minimum, maximum, value, decimals=3, step=1.0, suffix=''):
    s = QDoubleSpinBox()
    s.setRange(minimum, maximum)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


STATE_COLORS = {0: '#8a8a8a', 1: '#d18f00', 2: '#2e8b3a', 3: '#c62828'}


def badge_style(color: str) -> str:
    return (f'QLabel {{ background: {color}; color: white; border-radius: 4px; '
            f'padding: 2px 8px; font-weight: bold; }}')
