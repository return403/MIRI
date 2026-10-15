"""Speichern der Messungen: je Lauf ein Ordner mit Übersicht, Einstellungen und Kurven."""

import csv
import datetime
import json
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure

from ..devices.siglent import HORIZONTAL_DIVS, VERTICAL_DIVS


def envelope(t, y, bins=4000):
    """Min/Max je Abschnitt -> wenige Punkte, sieht geplottet aus wie das Original."""
    n = len(y)
    if n <= 2 * bins:
        return t, y
    per_bin = n // bins
    m = per_bin * bins
    yb = y[:m].reshape(bins, per_bin)
    tb = t[:m].reshape(bins, per_bin)
    t_env = np.repeat(tb[:, per_bin // 2], 2)
    y_env = np.empty(2 * bins, dtype=y.dtype)
    y_env[0::2] = yb.min(axis=1)
    y_env[1::2] = yb.max(axis=1)
    return t_env, y_env


def plot_trace(ax, t, y, info, trig_level=None, trig_slope='', title=''):
    """Darstellung wie test.py: Achsen wie am Scope-Bildschirm, Trigger rot."""
    ax.plot(t * 1000.0, y, linewidth=0.5)
    if trig_level is not None:
        ax.axhline(trig_level, color='red', linestyle='--', linewidth=1,
                   label=f'Trigger {trig_level} V ({trig_slope})')
    ax.axvline(0.0, color='red', linestyle=':', linewidth=1)

    center = -info['ofst']
    half_v = VERTICAL_DIVS / 2.0 * info['vdiv']
    half_t = HORIZONTAL_DIVS / 2.0 * info['tdiv']
    ax.set_ylim(center - half_v, center + half_v)
    ax.set_xlim((-info['trdl'] - half_t) * 1000.0, (-info['trdl'] + half_t) * 1000.0)

    ax.set_title(title)
    ax.set_xlabel('Zeit [ms]')
    ax.set_ylabel('Spannung [V]')
    ax.grid(True, which='both')
    if trig_level is not None:
        ax.legend(loc='upper right')


class Recorder:

    def __init__(self, base_dir, prefix, axes, settings: dict, save_csv=False, save_npz=True,
                 save_png=True, png_dpi=150):
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        self.run_dir = Path(base_dir).expanduser() / f'{prefix}_{stamp}'
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.axes = sorted(axes)
        self.save_csv = save_csv
        self.save_npz = save_npz
        self.save_png = save_png
        self.png_dpi = png_dpi

        with open(self.run_dir / 'einstellungen.json', 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False, default=str)

        self._index_path = self.run_dir / 'messungen.csv'
        header = ['nr', 'zeit', 'wiederholung']
        for a in self.axes:
            header += [f'soll_A{a} [grad]', f'ist_A{a} [grad]']
        header += ['punkte', 'abtastrate [Sa/s]', 'vdiv [V]', 'tdiv [s]', 'u_min [V]',
                   'u_max [V]', 'u_mittel [V]', 'datei']
        with open(self._index_path, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(header)

    def save(self, nr, repeat, positions, t, y, info, channel, trig_level, trig_slope):
        """positions: achse -> (soll, ist). Liefert den Dateinamen ohne Endung."""
        name = f'm{nr:04d}_w{repeat + 1}'
        for a in self.axes:
            if a in positions:
                name += f'_A{a}_{positions[a][0]:+.2f}'.replace('.', 'p')
        base = self.run_dir / name

        if self.save_npz:
            np.savez_compressed(base.with_suffix('.npz'), t=t, u=y, channel=channel,
                                **{k: v for k, v in info.items()},
                                **{f'soll_A{a}': p[0] for a, p in positions.items()},
                                **{f'ist_A{a}': p[1] for a, p in positions.items()})
        if self.save_csv:
            np.savetxt(base.with_suffix('.csv'), np.column_stack((t, y)), delimiter=',',
                       header='t [s],U [V]', comments='', fmt=['%.10e', '%.5f'])
        if self.save_png:
            fig = Figure(figsize=(16, 8))
            ax = fig.add_subplot(111)
            te, ye = envelope(t, y, 8000)
            pos_text = ', '.join(f'A{a}={p[1]:.2f}°' for a, p in sorted(positions.items()))
            plot_trace(ax, te, ye, info, trig_level, trig_slope,
                       f'{name} – CH{channel} – {len(y):,} Punkte @ {info["sara"]:.3g} Sa/s'
                       + (f' – {pos_text}' if pos_text else ''))
            fig.tight_layout()
            fig.savefig(base.with_suffix('.png'), dpi=self.png_dpi)

        row = [nr, datetime.datetime.now().isoformat(timespec='seconds'), repeat + 1]
        for a in self.axes:
            soll, ist = positions.get(a, ('', ''))
            row += [soll, ist]
        row += [len(y), info['sara'], info['vdiv'], info['tdiv'], float(np.min(y)),
                float(np.max(y)), float(np.mean(y)), name]
        with open(self._index_path, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(row)
        return name
