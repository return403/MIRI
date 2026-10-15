#include "settings.h"
#include <Arduino.h>
#include <EEPROM.h>
#if defined(ARDUINO_ARCH_ESP32)
#include <pgmspace.h>
#else
#include <avr/pgmspace.h>
#endif
#include <stddef.h>
#include <string.h>
#include <math.h>

static const uint16_t SETTINGS_MAGIC = 0x4D52;
#if defined(ARDUINO_ARCH_ESP32)
static const uint8_t SETTINGS_VERSION = 4;     // 4: link<n> = 2 (Achse nicht benutzt)
#else
static const uint8_t SETTINGS_VERSION = 3;     // Nano unveraendert, Kalibrierung bleibt
#endif

struct StoredSettings {
    uint16_t magic;
    uint8_t version;
    Settings data;
    uint8_t crc;
};

enum ParamType : uint8_t {
    PARAM_F32,
    PARAM_U16,
    PARAM_U8
};

struct ParamDesc {
    char name[12];
    uint8_t type;
    uint8_t offset;
    float minValue;
    float maxValue;
};

#define AXIS_PARAM(n, t, field, lo, hi) { n, t, (uint8_t)offsetof(AxisSettings, field), lo, hi }
#define SYSTEM_PARAM(n, t, field, lo, hi) { n, t, (uint8_t)offsetof(Settings, field), lo, hi }

// Achsparameter: werden je Achse mit angehaengter Achsnummer angeboten (gear0, gear1, ...).
static const ParamDesc AXIS_PARAMS[] PROGMEM = {
    AXIS_PARAM("softmin",   PARAM_F32, softMinDeg,         -180.0f,   180.0f),
    AXIS_PARAM("softmax",   PARAM_F32, softMaxDeg,         -180.0f,   180.0f),
    AXIS_PARAM("vmax",      PARAM_F32, maxSpeedDeg,           0.1f,   360.0f),
    AXIS_PARAM("accel",     PARAM_F32, accelDeg,              1.0f,  3600.0f),
    AXIS_PARAM("postol",    PARAM_F32, posTolDeg,             0.1f,     5.0f),
    AXIS_PARAM("followerr", PARAM_F32, followErrDeg,          0.2f,    45.0f),
    AXIS_PARAM("settle",    PARAM_U16, settleMs,             50.0f,  5000.0f),
    AXIS_PARAM("retries",   PARAM_U8,  maxRetries,            0.0f,    10.0f),
    AXIS_PARAM("jog",       PARAM_F32, jogStepDeg,           0.01f,    45.0f),
    AXIS_PARAM("jogfine",   PARAM_F32, jogFineDeg,           0.01f,    10.0f),
    AXIS_PARAM("calsweep",  PARAM_F32, calSweepDeg,           2.0f,    90.0f),
    AXIS_PARAM("calmax",    PARAM_F32, calMaxMotorDeg,       90.0f, 36000.0f),
    AXIS_PARAM("caltol",    PARAM_F32, calTolPct,             1.0f,    50.0f),
    AXIS_PARAM("bootsweep", PARAM_F32, bootSweepDeg,          0.0f,    30.0f),
    AXIS_PARAM("calspeed",  PARAM_F32, calSpeedDeg,          10.0f,  3600.0f),
    AXIS_PARAM("gear",      PARAM_F32, gearRatio,            0.01f, 10000.0f),
    AXIS_PARAM("dirinv",    PARAM_U8,  dirInverted,           0.0f,     1.0f),
    AXIS_PARAM("calvalid",  PARAM_U8,  calValid,              0.0f,     1.0f),
    AXIS_PARAM("zero",      PARAM_F32, zeroOffsetDeg,         0.0f,   360.0f),
    AXIS_PARAM("usteps",    PARAM_U16, microsteps,            1.0f,   256.0f),
    AXIS_PARAM("current",   PARAM_U16, currentMa,           100.0f,  2000.0f),
#if defined(ARDUINO_ARCH_ESP32)
    AXIS_PARAM("link",      PARAM_U8,  linked,                0.0f,     2.0f),
#endif
};

static const ParamDesc SYSTEM_PARAMS[] PROGMEM = {
    SYSTEM_PARAM("hbtimeout",   PARAM_U16, hbTimeoutMs,       200.0f, 60000.0f),
    SYSTEM_PARAM("boottimeout", PARAM_U16, bootTimeoutMs,    1000.0f, 60000.0f),
    SYSTEM_PARAM("telemetry",   PARAM_U16, telemetryMs,        50.0f,  5000.0f),
    SYSTEM_PARAM("detent",      PARAM_U8,  wheelStepsPerDetent, 1.0f,     8.0f),
};

static const uint8_t AXIS_PARAM_COUNT = sizeof(AXIS_PARAMS) / sizeof(AXIS_PARAMS[0]);
static const uint8_t SYSTEM_PARAM_COUNT = sizeof(SYSTEM_PARAMS) / sizeof(SYSTEM_PARAMS[0]);
static const uint16_t TOTAL_PARAMS = (uint16_t)AXIS_PARAM_COUNT * AXIS_COUNT + SYSTEM_PARAM_COUNT;

static uint8_t crc8(const uint8_t *data, uint8_t length) {
    uint8_t crc = 0;
    for (uint8_t i = 0; i < length; i++) {
        crc ^= data[i];
        for (uint8_t bit = 0; bit < 8; bit++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

// Index -> Beschreibung. Reihenfolge: je Achse alle Achsparameter, danach die Systemparameter.
static bool locate(uint16_t index, ParamDesc &desc, uint8_t &axis, bool &isAxis) {
    uint16_t axisTotal = (uint16_t)AXIS_PARAM_COUNT * AXIS_COUNT;
    if (index < axisTotal) {
        axis = index / AXIS_PARAM_COUNT;
        isAxis = true;
        memcpy_P(&desc, &AXIS_PARAMS[index % AXIS_PARAM_COUNT], sizeof(ParamDesc));
        return true;
    }
    index -= axisTotal;
    if (index < SYSTEM_PARAM_COUNT) {
        axis = 0;
        isAxis = false;
        memcpy_P(&desc, &SYSTEM_PARAMS[index], sizeof(ParamDesc));
        return true;
    }
    return false;
}

static bool isDigitChar(char c) {
    return c >= '0' && c <= '9';
}

// Name (z. B. "gear1" oder "boottimeout") -> Index
static bool findByName(const char *name, uint16_t &index) {
    size_t length = strlen(name);
    size_t digits = 0;
    while (digits < length && isDigitChar(name[length - 1 - digits])) {
        digits++;
    }

    if (digits > 0 && digits < length && digits <= 2 && length - digits < 12) {
        unsigned axis = 0;
        for (size_t i = length - digits; i < length; i++) {
            axis = axis * 10 + (name[i] - '0');
        }
        if (axis < AXIS_COUNT) {
            char base[12];
            memcpy(base, name, length - digits);
            base[length - digits] = '\0';
            for (uint8_t p = 0; p < AXIS_PARAM_COUNT; p++) {
                ParamDesc d;
                memcpy_P(&d, &AXIS_PARAMS[p], sizeof(ParamDesc));
                if (strcmp(d.name, base) == 0) {
                    index = (uint16_t)axis * AXIS_PARAM_COUNT + p;
                    return true;
                }
            }
        }
        return false;
    }

    for (uint8_t p = 0; p < SYSTEM_PARAM_COUNT; p++) {
        ParamDesc d;
        memcpy_P(&d, &SYSTEM_PARAMS[p], sizeof(ParamDesc));
        if (strcmp(d.name, name) == 0) {
            index = (uint16_t)AXIS_PARAM_COUNT * AXIS_COUNT + p;
            return true;
        }
    }
    return false;
}

static const uint8_t *dataOf(const Settings &s, const ParamDesc &d, uint8_t axis, bool isAxis) {
    if (isAxis) {
        return (const uint8_t *)&s.axis[axis] + d.offset;
    }
    return (const uint8_t *)&s + d.offset;
}

static float readValue(const uint8_t *p, uint8_t type) {
    switch (type) {
        case PARAM_F32: {
            float v;
            memcpy(&v, p, sizeof(v));
            return v;
        }
        case PARAM_U16: {
            uint16_t v;
            memcpy(&v, p, sizeof(v));
            return v;
        }
        default:
            return *p;
    }
}

static void writeValue(uint8_t *p, uint8_t type, float value) {
    switch (type) {
        case PARAM_F32:
            memcpy(p, &value, sizeof(value));
            break;
        case PARAM_U16: {
            uint16_t v = (uint16_t)(value + 0.5f);
            memcpy(p, &v, sizeof(v));
            break;
        }
        default:
            *p = (uint8_t)(value + 0.5f);
            break;
    }
}

Settings settingsDefaults() {
    Settings s;

    AxisSettings a;
    a.softMinDeg = -90.0f;
    a.softMaxDeg = 90.0f;
    a.maxSpeedDeg = 180.0f;     // bei gear = 1: 0,5 U/s
    a.accelDeg = 720.0f;        // bei gear = 1: 2 U/s^2, die Motorgrenze in stepper_tmc2209.cpp
    a.posTolDeg = 0.3f;
    a.followErrDeg = 2.0f;
    a.settleMs = 300;
    a.maxRetries = 3;
    a.jogStepDeg = 1.0f;
    a.jogFineDeg = 0.1f;

    a.calSweepDeg = 10.0f;
    a.calMaxMotorDeg = 1440.0f;
    a.calTolPct = 10.0f;
    a.bootSweepDeg = 2.0f;
    a.calSpeedDeg = 180.0f;

    a.gearRatio = 1.0f;
    a.dirInverted = 0;
    a.calValid = 0;
    a.zeroOffsetDeg = 0.0f;

    a.microsteps = 16;
    a.currentMa = 600;
#if defined(ARDUINO_ARCH_ESP32)
    a.linked = 0;               // Standard: Nano haengt per USB an der GUI
#endif

    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        s.axis[i] = a;
#if defined(ARDUINO_ARCH_ESP32)
        // Standard: nur Achse 0 benutzt, weitere Achsen per "SET link<n> 0|1" zuschalten
        s.axis[i].linked = (i == 0) ? 0 : 2;
#endif
    }

    s.hbTimeoutMs = 1500;
    s.bootTimeoutMs = 10000;
    s.telemetryMs = 200;
    s.wheelStepsPerDetent = 4;
    return s;
}

bool settingsValid(const Settings &s) {
#if defined(ARDUINO_ARCH_ESP32)
    // mindestens eine Achse muss benutzt werden
    bool anyUsed = false;
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        anyUsed = anyUsed || s.axis[i].linked != 2;
    }
    if (!anyUsed) {
        return false;
    }
#endif
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        const AxisSettings &a = s.axis[i];
        if (!(a.softMinDeg < a.softMaxDeg)) {
            return false;
        }
        if (a.microsteps == 0 || a.microsteps > 256 || (a.microsteps & (a.microsteps - 1)) != 0) {
            return false;
        }
    }
    for (uint16_t i = 0; i < TOTAL_PARAMS; i++) {
        ParamDesc d;
        uint8_t axis;
        bool isAxis;
        locate(i, d, axis, isAxis);
        float v = readValue(dataOf(s, d, axis, isAxis), d.type);
        if (isnan(v) || v < d.minValue || v > d.maxValue) {
            return false;
        }
    }
    return true;
}

bool settingsLoad(Settings &settings) {
    StoredSettings stored;
#if defined(ARDUINO_ARCH_ESP32)
    EEPROM.begin(sizeof(StoredSettings));
#endif
    EEPROM.get(0, stored);

    bool ok = stored.magic == SETTINGS_MAGIC &&
              stored.version == SETTINGS_VERSION &&
              stored.crc == crc8((const uint8_t *)&stored.data, sizeof(Settings)) &&
              settingsValid(stored.data);
    if (ok) {
        settings = stored.data;
    } else {
        settings = settingsDefaults();
    }
    return ok;
}

void settingsSave(const Settings &settings) {
    StoredSettings stored;
    stored.magic = SETTINGS_MAGIC;
    stored.version = SETTINGS_VERSION;
    stored.data = settings;
    stored.crc = crc8((const uint8_t *)&settings, sizeof(Settings));
#if defined(ARDUINO_ARCH_ESP32)
    EEPROM.begin(sizeof(StoredSettings));
#endif
    EEPROM.put(0, stored);
#if defined(ARDUINO_ARCH_ESP32)
    EEPROM.commit();
#endif
}

uint16_t settingsCount() {
    return TOTAL_PARAMS;
}

bool settingsNameAt(uint16_t index, char *buffer, uint8_t length) {
    ParamDesc d;
    uint8_t axis;
    bool isAxis;
    if (!locate(index, d, axis, isAxis) || length == 0) {
        return false;
    }
    strncpy(buffer, d.name, length - 1);
    buffer[length - 1] = '\0';
    if (isAxis) {
        size_t used = strlen(buffer);
        char number[4];
        utoa(axis, number, 10);
        if (used + strlen(number) + 1 > length) {
            return false;
        }
        strcat(buffer, number);
    }
    return true;
}

bool settingsGetAt(const Settings &settings, uint16_t index, float &value) {
    ParamDesc d;
    uint8_t axis;
    bool isAxis;
    if (!locate(index, d, axis, isAxis)) {
        return false;
    }
    value = readValue(dataOf(settings, d, axis, isAxis), d.type);
    return true;
}

bool settingsGet(const Settings &settings, const char *name, float &value) {
    uint16_t index;
    return findByName(name, index) && settingsGetAt(settings, index, value);
}

SettingsResult settingsSet(Settings &settings, const char *name, float value) {
    uint16_t index;
    if (!findByName(name, index)) {
        return SETTINGS_UNKNOWN;
    }
    ParamDesc d;
    uint8_t axis;
    bool isAxis;
    locate(index, d, axis, isAxis);
    if (isnan(value) || value < d.minValue || value > d.maxValue) {
        return SETTINGS_RANGE;
    }
    uint8_t *p = isAxis ? (uint8_t *)&settings.axis[axis] + d.offset : (uint8_t *)&settings + d.offset;
    writeValue(p, d.type, value);
    return SETTINGS_OK;
}
