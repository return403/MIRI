#pragma once

#include <stdint.h>
#include "pins.h"

// Einstellungen einer Achse. Alle Winkel sind Grad des AS5600
// (Position 0 = gespeicherter Nullpunkt).
struct AxisSettings {
    // Positionsregelung
    float softMinDeg;
    float softMaxDeg;
    float maxSpeedDeg;          // Grad/s
    float accelDeg;             // Grad/s^2
    float posTolDeg;            // gilt als erreicht innerhalb dieser Toleranz
    float followErrDeg;         // groesserer Abstand Encoder <-> Sollwert = Fehler
    uint16_t settleMs;          // Beruhigungszeit vor der Pruefung
    uint8_t maxRetries;         // Korrekturversuche bevor "Position nicht erreicht"
    float jogStepDeg;           // Handrad, pro Raste
    float jogFineDeg;           // Handrad, fein

    // Kalibrierung
    float calSweepDeg;          // so weit soll sich der Encoder dabei drehen
    float calMaxMotorDeg;       // Abbruchgrenze am Motor
    float calTolPct;            // erlaubte Abweichung beim Pruefen
    float bootSweepDeg;         // Pruefbewegung beim Start, 0 = aus
    float calSpeedDeg;          // Motorgrad/s waehrend der Kalibrierung

    // Achse (Ergebnis der Kalibrierung)
    float gearRatio;            // Motorgrad pro Encodergrad
    uint8_t dirInverted;
    uint8_t calValid;
    float zeroOffsetDeg;        // AS5600-Winkel der Position 0

    // Treiber
    uint16_t microsteps;
    uint16_t currentMa;

#if defined(ARDUINO_ARCH_ESP32)
    // Nur im Master: 0 = Achsmodul per USB direkt an der GUI, 1 = am UART des ESP32,
    // 2 = Achse nicht benutzt (kein Modul, wird nicht ueberwacht)
    uint8_t linked;
#endif
};

// Aendert sich das Layout, SETTINGS_VERSION in settings.cpp erhoehen.
struct Settings {
    // System
    uint16_t hbTimeoutMs;
    uint16_t bootTimeoutMs;
    uint16_t telemetryMs;
    uint8_t wheelStepsPerDetent;

    AxisSettings axis[AXIS_COUNT];
};

enum SettingsResult {
    SETTINGS_OK,
    SETTINGS_UNKNOWN,
    SETTINGS_RANGE
};

Settings settingsDefaults();
bool settingsValid(const Settings &settings);

// false = EEPROM leer oder ungueltig, settings enthaelt dann die Defaults
bool settingsLoad(Settings &settings);
void settingsSave(const Settings &settings);

// Achsparameter heissen <name><achse>, z. B. "gear0", "gear1". Systemparameter ohne Zahl.
uint16_t settingsCount();
bool settingsNameAt(uint16_t index, char *buffer, uint8_t length);
bool settingsGet(const Settings &settings, const char *name, float &value);
bool settingsGetAt(const Settings &settings, uint16_t index, float &value);
SettingsResult settingsSet(Settings &settings, const char *name, float value);
