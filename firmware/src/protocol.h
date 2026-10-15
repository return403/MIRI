#pragma once

#include <Arduino.h>
#include <stdint.h>
#include "settings.h"
#include "module_link.h"
#include "state_machine.h"

// Textprotokoll ueber USB-Serial, 115200 Baud, 8N1, eine Zeile pro Nachricht.
//
//   Zeile    :  <Nutzdaten>*<HH>\n
//   HH       :  XOR aller Zeichen der Nutzdaten, zwei Hexziffern (gross oder klein).
//   Zeilen ohne oder mit falscher Pruefsumme werden mit "ERR CHK" abgewiesen.
//   Jede gueltige Zeile zaehlt als Lebenszeichen. Sonst alle <= hbtimeout/2 ein "HB" senden.
//   Bleibt die GUI laenger als hbtimeout stumm: Fehler 1, Laser aus, alle Module stromlos.
//
// Jedes Achsmodul (Nano) haengt entweder am UART des ESP32 (link<n> = 1) oder per USB
// direkt an der GUI (link<n> = 0). link<n> = 2: Achse nicht benutzt (kein Modul, wird nicht
// ueberwacht, Achsbefehle ergeben ERR UNUSED). Standard: Achse 0 = 0, alle weiteren = 2,
// d. h. ein einzelner Nano funktioniert ohne Einstellung.
//
// link = 1: Der Master leitet Achsbefehle weiter und gibt die Antwort des Moduls zurueck
//   (max. 1,5 s, sonst ERR TIMEOUT; solange eine Anfrage offen ist: ERR BUSY).
//
// link = 0: Die GUI spricht das Modul selbst an (Protokoll siehe slave/slave_main.cpp).
//   Achsbefehle an den Master ergeben "ERR EXTERN". Die GUI MUSS dann:
//     - dem Master den Zustand des Moduls melden: AXIS <achse> <state> <err>
//       (aus der M-Zeile des Moduls, mindestens alle hbtimeout/2). Bleibt das aus: Fehler 10.
//     - dem Modul ESTOP senden, sobald der Master state = ERROR meldet
//     - nach ACK am Master auch das Modul quittieren (ACK <code> an das Modul)
//     - Handrad: der Master meldet "W <achse> <grad>", die GUI schickt "JOG <grad>" ans Modul
//
// GUI -> Master                          Antwort
//   HB                                   (keine)
//   VER                                  V MIRI-MASTER <version>
//   STATUS                               S ... und A ... (sofort)
//   GET <name>                           P <name> <wert>
//   SET <name> <wert>                    OK | ERR RANGE | ERR NAME | ERR BUSY
//   LIST                                 P <name> <wert> je Parameter, dann OK
//   SAVE                                 OK    (Master und alle Module ins EEPROM)
//   DEFAULTS                             OK    (Master und alle Module, nicht gespeichert)
//   MOVE <achse> <grad>                  OK | ERR STATE   (absolut, Softlimits im Modul)
//   JOG <achse> <grad>                   OK | ERR STATE   (relativ zum Ziel)
//   FMOVE <achse> <grad>                 OK | ERR STATE   (erzwungen ohne Encoder, absolut)
//   FJOG <achse> <grad>                  OK | ERR STATE   (erzwungen ohne Encoder, relativ)
//                                        auch im Fehlerzustand, nie mit Laserfreigabe,
//                                        Details siehe slave/slave_main.cpp
//   HOME <achse>                         OK | ERR STATE   (faehrt auf 0)
//   STOP                                 OK               (alle Achsen, in jedem Zustand)
//   ZERO <achse>                         OK | ERR STATE   (aktuelle Position = 0)
//   CAL <achse>                          OK | ERR STATE   (Kalibrierung, nur ohne Freigabe)
//   ARM <0|1>                            OK | ERR STATE   (Laserfreigabe, braucht Schluessel)
//   ACK <fehlercode>                     OK | ERR STATE   (Fehler quittieren)
//   AXIS <achse> <state> <err>           (keine)          (Zustand eines Moduls an der GUI)
//
// Master -> GUI, alle <telemetry> ms und bei jeder Zustandsaenderung:
//   S <state> <err> <fehlerachse> <arm> <key> <laser> <fein> <handradachse>
//   A <achse> <busy> <pos> <ziel> <enc> <drv> <modulstate> <modul_da> <link>   (je Achse)
//       link: 0 GUI, 1 UART, 2 nicht benutzt
//       busy/pos/ziel/enc/drv sind nur bei link = 1 gueltig, sonst 0
//   W <achse> <grad>                     Handrad gedreht, Achse haengt an der GUI
//     state : 0 BOOT, 1 HOMING, 2 READY, 3 ERROR
//     err   : 0 keiner, 1 GUI weg, 2 Encoder-Verbindung, 3 Magnet, 4 Treiber-Verbindung,
//             5 Treiberfehler, 6 Position nicht erreicht, 7 Schleppfehler,
//             8 Kalibrierung, 9 Zeitueberschreitung, 10 Modul weg
//     enc   : 0 ok, 1 kein Chip, 2 kein Magnet, 3 zu stark, 4 zu schwach
//     drv   : 0 ok, 1 keine UART, 2 Uebertemp.-Warnung, 3 Uebertemp., 4 Treiberfehler
//     modul_da: 1 = Modul sendet Telemetrie
//
// Parameter: Achsparameter tragen die Achsnummer (gear0, gear1, ...) und liegen im
// jeweiligen Modul (siehe settings.cpp). Im Master selbst liegen nur: hbtimeout,
// boottimeout, telemetry, detent sowie jog<n>, jogfine<n> (Handrad) und link<n>.
class Protocol {
    public:
        Protocol(Stream &port, StateMachineController &machine, ModuleLink *const *modules,
                 Settings &settings);

        void update();

    private:
        enum Mode : uint8_t {
            MODE_IDLE,
            MODE_FORWARD,
            MODE_LIST_LOCAL,
            MODE_LIST_MODULE,
            MODE_BROADCAST
        };

        void readInput();
        void handleLine(char *line);
        void dispatch(char *command, char *arg1, char *arg2);
        void send(const char *payload);
        void reply(bool ok, const char *reason);

        bool forwardTo(uint8_t axis, const char *command, const char *displayName);
        void forwardRemote(uint8_t axis, const char *text);
        void sendRenamedParam(const char *moduleLine, uint8_t axis, const char *displayName);

        void pumpForward();
        void pumpList();
        void pumpBroadcast();
        void abortJob(const char *reason);

        void sendSummary();
        void sendAxis(uint8_t axis);
        void pumpWheel();
        void pumpTelemetry();

        Stream &port;
        StateMachineController &machine;
        ModuleLink *const *modules;
        Settings &settings;

        char line[64];
        uint8_t length;
        bool overflow;

        // laufender Weiterleitungsauftrag (immer nur einer)
        Mode mode;
        uint8_t jobAxis;
        uint16_t jobIndex;
        bool jobSent;
        char jobCommand[24];
        char jobName[12];

        int16_t telemetryStep;     // -1 = nichts offen, 0 = Zusammenfassung, n = Achse n-1
        unsigned long lastTelemetry;
        MachineState lastState;
        ErrorCode lastError;
};
