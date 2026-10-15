// Achsmodul: Arduino Nano mit genau einem AS5600 und einem TMC2209.
// Kein Laser, kein Schluessel, kein Handrad. Gesteuert ueber die serielle Schnittstelle:
// entweder USB (GUI direkt) oder D0/D1 (RX/TX) an der NodeMCU. Es ist derselbe UART,
// also immer nur eines von beiden gleichzeitig.
//
// Zeilenprotokoll wie am Master (115200 Baud, <Nutzdaten>*<HH>\n, HH = XOR der Nutzdaten),
// nur ohne Achsnummer, das Modul hat genau eine Achse.
//
// Befehle (Antwort OK | ERR <grund>)
//   HB                 Lebenszeichen, keine Antwort. Jede gueltige Zeile zaehlt auch dazu.
//   VER                V MIRI-AXIS <version>
//   STATUS             M-Zeile sofort
//   GET <name>         P <name> <wert>       (Namen wie am Master, Achse immer 0: gear0 ...)
//   SET <name> <wert>
//   LIST               P ... je Parameter, dann OK
//   SAVE | DEFAULTS
//   MOVE <grad> | JOG <grad> | HOME | STOP | ZERO | CAL
//   FMOVE <grad> | FJOG <grad>   erzwungene Fahrt nach Schrittzaehler, ohne Encoder und
//                      ohne Softlimits. Nur in READY oder ERROR. Aus READY referenziert das
//                      Modul danach neu (BOOT -> HOMING), in ERROR bleibt es im Fehler und
//                      der Motor geht nach der Fahrt wieder stromlos. STOP haelt sie an.
//   ACK <fehlercode>
//   ESTOP [fehlercode]  Motor sofort stromlos, Zustand ERROR (vom Master bei Fehler gesendet)
//
// Meldung alle <telemetry> ms und bei Zustandsaenderung:
//   M <state> <err> <busy> <pos> <ziel> <enc> <drv>
//     state 0 BOOT, 1 HOMING, 2 READY, 3 ERROR   (Codes siehe machine_types.h)
//
// Sicherheit im Modul: Bleibt die Gegenseite laenger als hbtimeout stumm, oder meldet
// Encoder/Treiber/Positionsregelung einen Fehler, geht der Motor stromlos (Zustand ERROR),
// verriegelt bis ACK. Den Laser kennt das Modul nicht, der Master muss bei Zustand != READY
// oder Fehler selbst abschalten.

#include <Arduino.h>
#include <avr/wdt.h>
#include <string.h>

#include "pins.h"
#include "line_utils.h"
#include "machine_types.h"
#include "settings.h"
#include "status_leds.h"
#include "as5600.h"
#include "stepper_tmc2209.h"
#include "position_control.h"

static_assert(AXIS_COUNT == 1, "Achsmodul hat genau eine Achse");

static const char FIRMWARE_VERSION[] = "1.0";
static const unsigned long HOMING_TIMEOUT_MS = 60000;
static const uint16_t LIST_DONE = 0xFFFF;

static StepperConfig makeStepperConfig() {
    Settings d = settingsDefaults();
    const AxisSettings &a = d.axis[0];

    StepperConfig c;
    c.stepPin = PIN_STEP[0];
    c.dirPin = PIN_DIR[0];
    c.enablePin = PIN_STEPPER_EN[0];
    c.uartRxPin = PIN_TMC_RX[0];
    c.uartTxPin = PIN_TMC_TX[0];
    c.driverAddress = TMC_DRIVER_ADDRESS[0];
    c.rSense = TMC_RSENSE;
    c.rmsCurrentMa = a.currentMa;
    c.microsteps = a.microsteps;
    c.gearRatio = a.gearRatio;
    c.maxSpeedDegPerSec = a.maxSpeedDeg;
    c.accelDegPerSec2 = a.accelDeg;
    return c;
}

static Settings settings;
static LED statusLed(PIN_STATUS_LED, 700);
static AS5600 encoder(Wire, PIN_I2C_SDA[0], PIN_I2C_SCL[0], ENCODER_I2C_ADDRESS[0]);
static StepperTMC2209 stepper(makeStepperConfig());
static PositionControl axis(encoder, stepper, settings, 0);

static MachineState state = STATE_BOOT;
static ErrorCode error = ERR_NONE;
static unsigned long stateSince = 0;
static bool heartbeatSeen = false;
static unsigned long lastHeartbeat = 0;

static char line[48];
static uint8_t lineLength = 0;
static bool lineOverflow = false;

static uint16_t listIndex = LIST_DONE;
static unsigned long lastTelemetry = 0;
static bool telemetryDue = true;
static MachineState reportedState = STATE_BOOT;
static ErrorCode reportedError = ERR_NONE;

// ---------------------------------------------------------------- Ausgabe

static void sendLine(const char *payload) {
    uint8_t cs = lineChecksum(payload);
    Serial.print(payload);
    Serial.print('*');
    if (cs < 16) {
        Serial.print('0');
    }
    Serial.print(cs, HEX);
    Serial.print('\n');
}

static void reply(bool ok, const char *reason) {
    if (ok) {
        sendLine("OK");
        return;
    }
    LineBuilder b;
    b.str("ERR ");
    b.str(reason);
    sendLine(b.buf);
}

static void sendTelemetry() {
    LineBuilder b;
    b.str("M ");
    b.num(state);
    b.chr(' ');
    b.num(error);
    b.chr(' ');
    b.num(axis.isBusy());
    b.chr(' ');
    b.flt(axis.getPosition(), 2);
    b.chr(' ');
    b.flt(axis.getTarget(), 2);
    b.chr(' ');
    b.num(encoder.getStatus());
    b.chr(' ');
    b.num(stepper.getStatus());
    sendLine(b.buf);
}

static void sendParam(uint16_t index) {
    char name[12];
    float v;
    if (!settingsNameAt(index, name, sizeof(name)) || !settingsGetAt(settings, index, v)) {
        return;
    }
    LineBuilder b;
    b.str("P ");
    b.str(name);
    b.chr(' ');
    b.flt(v, 3);
    sendLine(b.buf);
}

// ---------------------------------------------------------------- Zustand

static bool heartbeatFresh() {
    return heartbeatSeen && (millis() - lastHeartbeat) <= settings.hbTimeoutMs;
}

static ErrorCode hardwareFault() {
    switch (encoder.getStatus()) {
        case AS5600_OK:
            break;
        case AS5600_NO_DEVICE:
            return ERR_ENCODER_COMM;
        default:
            return ERR_ENCODER_MAGNET;
    }
    switch (stepper.getStatus()) {
        case STEPPER_NO_COMMUNICATION:
            return ERR_DRIVER_COMM;
        case STEPPER_OVERTEMP:
        case STEPPER_DRIVER_ERROR:
            return ERR_DRIVER_FAULT;
        default:
            break;
    }
    return ERR_NONE;
}

static ErrorCode runtimeFault() {
    ErrorCode hw = hardwareFault();
    if (hw != ERR_NONE) {
        return hw;
    }
    switch (axis.getError()) {
        case POS_ERR_NOT_REACHED:
            return ERR_POS_NOT_REACHED;
        case POS_ERR_FOLLOWING:
            return ERR_POS_FOLLOWING;
        case POS_ERR_CALIBRATION:
            return ERR_CALIBRATION;
        default:
            break;
    }
    return heartbeatFresh() ? ERR_NONE : ERR_GUI_LOST;
}

static void enterBoot() {
    state = STATE_BOOT;
    error = ERR_NONE;
    stateSince = millis();
    axis.abort();
    axis.applySettings();
}

static void enterHoming(bool forceCalibration) {
    state = STATE_HOMING;
    stateSince = millis();
    axis.startHoming(forceCalibration);
}

static void enterError(ErrorCode code) {
    state = STATE_ERROR;
    error = code;
    stateSince = millis();
    axis.abort();                   // Motor stoppen und stromlos
}

static void updateModule() {
    unsigned long now = millis();
    axis.update();

    switch (state) {
        case STATE_BOOT:
            if (axis.isBusy()) {
                // erzwungene Fahrt aus READY: erst danach neu referenzieren
                if (!heartbeatFresh()) {
                    enterError(ERR_GUI_LOST);
                } else {
                    stateSince = now;
                }
            } else if (hardwareFault() == ERR_NONE && heartbeatFresh()) {
                enterHoming(false);
            } else if (now - stateSince >= settings.bootTimeoutMs) {
                ErrorCode cause = hardwareFault();
                enterError(cause != ERR_NONE ? cause : ERR_GUI_LOST);
            }
            break;

        case STATE_HOMING: {
            ErrorCode fault = runtimeFault();
            if (fault != ERR_NONE) {
                enterError(fault);
            } else if (axis.getHoming() == HOMING_DONE) {
                state = STATE_READY;
                stateSince = now;
            } else if (now - stateSince >= HOMING_TIMEOUT_MS) {
                enterError(ERR_TIMEOUT);
            }
            break;
        }

        case STATE_READY: {
            ErrorCode fault = runtimeFault();
            if (fault != ERR_NONE) {
                enterError(fault);
            }
            break;
        }

        case STATE_ERROR:
            // nur eine erzwungene Fahrt darf den Motor hier bestromen
            if (axis.isBusy() && !heartbeatFresh()) {
                axis.abort();
            } else if (!axis.isBusy() && stepper.isEnabled()) {
                stepper.disable();
            }
            break;
    }
}

// ---------------------------------------------------------------- Befehle

static void dispatch(char *command, char *arg1, char *arg2) {
    float value;

    if (strcmp(command, "HB") == 0) {
        return;
    }
    if (strcmp(command, "VER") == 0) {
        LineBuilder b;
        b.str("V MIRI-AXIS ");
        b.str(FIRMWARE_VERSION);
        sendLine(b.buf);
        return;
    }
    if (strcmp(command, "STATUS") == 0) {
        telemetryDue = true;
        return;
    }
    if (strcmp(command, "GET") == 0) {
        float v;
        if (arg1 == nullptr || !settingsGet(settings, arg1, v)) {
            reply(false, "NAME");
            return;
        }
        LineBuilder b;
        b.str("P ");
        b.str(arg1);
        b.chr(' ');
        b.flt(v, 3);
        sendLine(b.buf);
        return;
    }
    if (strcmp(command, "SET") == 0) {
        if (arg1 == nullptr || !parseFloat(arg2, value)) {
            reply(false, "ARG");
            return;
        }
        Settings backup = settings;
        SettingsResult r = settingsSet(settings, arg1, value);
        if (r == SETTINGS_UNKNOWN) {
            reply(false, "NAME");
            return;
        }
        if (r == SETTINGS_RANGE || !settingsValid(settings)) {
            settings = backup;
            reply(false, "RANGE");
            return;
        }
        if (!axis.applySettings()) {
            settings = backup;
            axis.applySettings();
            reply(false, "BUSY");
            return;
        }
        reply(true, nullptr);
        return;
    }
    if (strcmp(command, "LIST") == 0) {
        listIndex = 0;
        return;
    }
    if (strcmp(command, "SAVE") == 0) {
        settingsSave(settings);
        reply(true, nullptr);
        return;
    }
    if (strcmp(command, "DEFAULTS") == 0) {
        Settings backup = settings;
        settings = settingsDefaults();
        if (!axis.applySettings()) {
            settings = backup;
            reply(false, "BUSY");
            return;
        }
        reply(true, nullptr);
        return;
    }
    if (strcmp(command, "MOVE") == 0 || strcmp(command, "JOG") == 0) {
        if (!parseFloat(arg1, value)) {
            reply(false, "ARG");
            return;
        }
        bool ok = false;
        if (state == STATE_READY) {
            ok = command[0] == 'M' ? axis.moveTo(value) : axis.moveBy(value);
        }
        reply(ok, "STATE");
        return;
    }
    if (strcmp(command, "FMOVE") == 0 || strcmp(command, "FJOG") == 0) {
        if (!parseFloat(arg1, value)) {
            reply(false, "ARG");
            return;
        }
        bool ok = (state == STATE_READY || state == STATE_ERROR || axis.isForced()) &&
                  axis.forceMove(value, command[1] == 'J');
        if (ok && state == STATE_READY) {
            state = STATE_BOOT;     // Position danach unbekannt, nach der Fahrt neu referenzieren
            stateSince = millis();
        }
        reply(ok, "STATE");
        return;
    }
    if (strcmp(command, "HOME") == 0) {
        reply(state == STATE_READY && axis.moveTo(0.0f), "STATE");
        return;
    }
    if (strcmp(command, "STOP") == 0) {
        bool ok = state == STATE_READY || axis.isForced();
        if (ok) {
            axis.stop();
        }
        reply(ok, "STATE");
        return;
    }
    if (strcmp(command, "ZERO") == 0) {
        reply(state == STATE_READY && axis.setZero(), "STATE");
        return;
    }
    if (strcmp(command, "CAL") == 0) {
        bool ok = state == STATE_READY && !axis.isBusy();
        if (ok) {
            enterHoming(true);
        }
        reply(ok, "STATE");
        return;
    }
    if (strcmp(command, "ESTOP") == 0) {
        float code = ERR_GUI_LOST;
        if (arg1 != nullptr && !parseFloat(arg1, code)) {
            reply(false, "ARG");
            return;
        }
        if (state != STATE_ERROR) {
            enterError((ErrorCode)(int)code);
        } else {
            axis.abort();           // auch eine erzwungene Fahrt im Fehlerzustand
        }
        reply(true, nullptr);
        return;
    }
    if (strcmp(command, "ACK") == 0) {
        if (!parseFloat(arg1, value) || value < 0 || value > 255) {
            reply(false, "ARG");
            return;
        }
        bool ok = state == STATE_ERROR && (uint8_t)value == (uint8_t)error &&
                  hardwareFault() == ERR_NONE && heartbeatFresh();
        if (ok) {
            enterBoot();
        }
        reply(ok, "STATE");
        return;
    }
    reply(false, "UNK");
}

static void handleLine(char *text) {
    char *star = strrchr(text, '*');
    if (star == nullptr || strlen(star) != 3) {
        reply(false, "CHK");
        return;
    }
    int high = hexValue(star[1]);
    int low = hexValue(star[2]);
    *star = '\0';
    if (high < 0 || low < 0 || ((high << 4) | low) != lineChecksum(text)) {
        reply(false, "CHK");
        return;
    }

    heartbeatSeen = true;           // jede gueltige Zeile ist ein Lebenszeichen
    lastHeartbeat = millis();

    char *command = strtok(text, " ");
    char *arg1 = strtok(nullptr, " ");
    char *arg2 = strtok(nullptr, " ");
    if (command != nullptr) {
        dispatch(command, arg1, arg2);
    }
}

static void readSerial() {
    while (Serial.available() > 0) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (lineOverflow) {
                reply(false, "LINE");
            } else if (lineLength > 0) {
                line[lineLength] = '\0';
                handleLine(line);
            }
            lineLength = 0;
            lineOverflow = false;
        } else if (lineLength < sizeof(line) - 1) {
            line[lineLength++] = c;
        } else {
            lineOverflow = true;
        }
    }
}

static void pumpOutput() {
    if (listIndex != LIST_DONE && Serial.availableForWrite() >= 40) {
        if (listIndex < settingsCount()) {
            sendParam(listIndex++);
        } else {
            reply(true, nullptr);
            listIndex = LIST_DONE;
        }
        return;
    }

    unsigned long now = millis();
    bool changed = state != reportedState || error != reportedError;
    if ((telemetryDue || changed || now - lastTelemetry >= settings.telemetryMs) &&
        Serial.availableForWrite() >= 48) {
        telemetryDue = false;
        lastTelemetry = now;
        reportedState = state;
        reportedError = error;
        sendTelemetry();
    }
}

static void updateStatusLed() {
    switch (state) {
        case STATE_BOOT:
            statusLed.setInterval(700);
            statusLed.blink();
            break;
        case STATE_HOMING:
            statusLed.setInterval(300);
            statusLed.blink();
            break;
        case STATE_READY:
            statusLed.on();
            break;
        case STATE_ERROR:
            statusLed.setInterval(60);
            statusLed.blink();
            break;
    }
    statusLed.update();
}

void setup() {
    Serial.begin(115200);
    statusLed.begin();

    settingsLoad(settings);

    encoder.begin();
    stepper.begin();
    enterBoot();

    wdt_enable(WDTO_2S);            // haengt die Firmware, setzt der Watchdog zurueck
}

void loop() {
    wdt_reset();
    stepper.update();
    readSerial();
    updateModule();
    pumpOutput();
    stepper.update();
    updateStatusLed();
}
