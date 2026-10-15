#pragma once

#include <stdint.h>
#include "machine_types.h"
#include "settings.h"
#include "module_link.h"
#include "laser_relay.h"
#include "key_switch.h"
#include "encoder_wheel.h"

// Master ueber Nano-Achsmodule. Die Module referenzieren und regeln sich selbst, der Master
// ueberwacht sie und haelt Laser, Schluessel, Handrad und die GUI-Anbindung.
//
// Jede Achse haengt entweder
//   - am UART des ESP32 (Parameter link<n> = 1): der Master liest und steuert das Modul selbst
//   - per USB direkt an der GUI (link<n> = 0, Standard): die GUI steuert das Modul und meldet
//     dem Master dessen Zustand mit "AXIS <achse> <state> <err>". Bleibt die Meldung laenger
//     als hbtimeout aus, gilt die Achse als verloren (Fehler 10).
//
// BOOT   : wartet, bis alle Module antworten und die GUI da ist. Laser aus.
// HOMING : mindestens ein Modul ist noch nicht READY (Referenzfahrt/Kalibrierung laeuft).
// READY  : alle Module READY. Laser nur mit Schluessel UND GUI-Freigabe.
// ERROR  : verriegelt, Laser aus, alle Module per ESTOP stromlos. Raus nur per ACK.
//
// Ein Modul in ERROR, ein verlorenes Modul oder eine verlorene GUI fuehren sofort nach ERROR.

class StateMachineController {
    public:
        StateMachineController(Settings &settings, ModuleLink *const *modules, LaserRelay &laser,
                               KeySwitch &key, EncoderWheel &wheel);

        void begin();
        void update();

        MachineState getState() const;
        ErrorCode getError() const;
        uint8_t getErrorAxis() const;

        // vom Protokoll bei jeder gueltigen Zeile der GUI aufgerufen
        void onHeartbeat();

        // Achse haengt am UART des ESP32 (true) oder an der GUI bzw. ist unbenutzt (false)
        bool isLinked(uint8_t axis) const;
        // false = Achse nicht benutzt (link<n> = 2): kein Modul, keine Ueberwachung
        bool isUsed(uint8_t axis) const;
        // link<n>: 0 GUI, 1 UART, 2 nicht benutzt
        uint8_t linkMode(uint8_t axis) const;
        bool axisAlive(uint8_t axis) const;
        MachineState axisState(uint8_t axis) const;
        ErrorCode axisError(uint8_t axis) const;

        // Zustandsmeldung der GUI fuer eine Achse, die an der GUI haengt
        bool reportAxis(uint8_t axis, MachineState axisState, ErrorCode axisError);
        // Handrad-Weg fuer eine Achse an der GUI abholen (die GUI schickt ihn an den Nano)
        bool takeExternalJog(uint8_t &axis, float &degrees);

        bool requestArm(bool arm);
        bool acknowledge(uint8_t code);
        bool requestStop();
        bool applySettings();

        // Bewegungsbefehle der GUI sind nur in READY erlaubt
        bool commandsAllowed() const;

        bool isArmed() const;
        bool isLaserOn() const;
        bool isKeyOn() const;
        bool isFineMode() const;
        uint8_t getSelectedAxis() const;

    private:
        void skipUnusedWheelAxes();
        void enterBoot();
        void enterError(ErrorCode code, uint8_t axis);
        bool heartbeatFresh() const;
        bool allAlive() const;
        bool allReady() const;
        ErrorCode moduleFault(uint8_t &axis) const;
        void sendModuleHeartbeats(unsigned long now);
        void handleWheel(unsigned long now);
        void applyLaser();

        Settings &settings;
        ModuleLink *const *modules;
        LaserRelay &laser;
        KeySwitch &key;
        EncoderWheel &wheel;

        MachineState state;
        ErrorCode error;
        uint8_t errorAxis;
        unsigned long stateSince;

        bool heartbeatSeen;
        unsigned long lastHeartbeat;
        unsigned long lastModuleHeartbeat;

        bool armed;
        uint8_t wheelMode;      // 0 = Achse 0 grob, 1 = Achse 0 fein, 2 = Achse 1 grob, ...

        // Zustand der Achsen an der GUI, von der GUI gemeldet
        bool reportSeen[AXIS_COUNT];
        unsigned long reportAt[AXIS_COUNT];
        MachineState reportState[AXIS_COUNT];
        ErrorCode reportError[AXIS_COUNT];

        float wheelPending[AXIS_COUNT];     // noch nicht gesendete Handrad-Wegstrecke
        float wheelCommanded[AXIS_COUNT];   // zuletzt gesendetes Ziel
        bool wheelHaveCommand[AXIS_COUNT];
        unsigned long wheelSentAt[AXIS_COUNT];
};
