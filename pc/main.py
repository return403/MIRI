"""Start der MIRI-Oberfläche.

    python UI.py            echte Hardware
    python UI.py --sim      Simulation (ESP32, Nanos und Oszilloskop nachgebildet)
"""

import argparse
import os
import sys
from pathlib import Path

# Auch direkt startbar (python pc/main.py, Run-Button in VS Code): Projektordner in den Pfad
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _fix_desktop_environment():
    """Muss vor dem ersten Laden von Qt laufen.

    - Qts GTK3-Theme liest den GSettings-Schlüssel 'antialiasing', den neuere GNOME-Versionen
      nicht mehr haben -> GLib bricht das Programm ab. 'xdgdesktopportal' lädt GTK3 intern
      ebenfalls, daher das Theme 'generic' (ganz ohne GTK).
    - VS Code als Snap vererbt Pfade auf seine eigenen (alten) GTK/GLib-Dateien.
    """
    if os.environ.get('QT_QPA_PLATFORMTHEME', '') in ('', 'gtk3', 'xdgdesktopportal'):
        os.environ['QT_QPA_PLATFORMTHEME'] = 'generic'
    if 'SNAP' in os.environ:
        for name in ('GSETTINGS_SCHEMA_DIR', 'GTK_PATH', 'GTK_EXE_PREFIX', 'GTK_IM_MODULE_FILE',
                     'GIO_MODULE_DIR', 'GDK_PIXBUF_MODULE_FILE', 'GDK_PIXBUF_MODULEDIR',
                     'LOCPATH'):
            os.environ.pop(name, None)
        for name in ('XDG_DATA_DIRS', 'XDG_CONFIG_DIRS'):
            original = os.environ.get(f'{name}_VSCODE_SNAP_ORIG')
            if original:
                os.environ[name] = original


_fix_desktop_environment()

from PySide6.QtSerialPort import QSerialPortInfo
from PySide6.QtWidgets import QApplication

from pc.core.controller import Controller
from pc.devices.serial_device import open_serial_port
from pc.devices.siglent import SiglentSDS1104XE, list_resources
from pc.gui.main_window import MainWindow

# Dauer-Ports, die nie ein Arduino/ESP32 sind
_IGNORED_PREFIXES = ('ttyS',)


def list_serial_ports():
    ports = []
    for info in QSerialPortInfo.availablePorts():
        if info.portName().startswith(_IGNORED_PREFIXES):
            continue
        ports.append((info.systemLocation(), info.description()))
    return sorted(ports)


def main(argv=None):
    parser = argparse.ArgumentParser(description='MIRI – Achsen steuern und Oszilloskop auslesen')
    parser.add_argument('--sim', action='store_true', help='ohne Hardware (Simulation)')
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])

    if args.sim:
        from tests.mocks.mock_arduino import SimBus
        from tests.mocks.mock_oszi import MockScope
        bus = SimBus()
        controller = Controller(transport_factory=bus.open)
        window = MainWindow(controller, bus.list_ports, lambda: [MockScope.RESOURCE],
                            MockScope, title='MIRI (Simulation)')
    else:
        controller = Controller(transport_factory=open_serial_port)
        window = MainWindow(controller, list_serial_ports, list_resources, SiglentSDS1104XE)

    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
