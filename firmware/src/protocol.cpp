#include "protocol.h"
#include "line_utils.h"
#include <stdlib.h>
#include <string.h>

static const char FIRMWARE_VERSION[] = "1.0";
static const unsigned long FORWARD_TIMEOUT_MS = 1500;

static bool parseAxisValue(const char *text, uint8_t &axis) {
    float v;
    if (!parseFloat(text, v) || v < 0.0f || v >= AXIS_COUNT) {
        return false;
    }
    axis = (uint8_t)(v + 0.5f);
    return true;
}

static bool isDigitChar(char c) {
    return c >= '0' && c <= '9';
}

// "gear1" -> base "gear", axis 1. Namen ohne Achsnummer (Systemparameter) liefern false.
static bool splitName(const char *name, char *base, uint8_t baseSize, uint8_t &axis) {
    size_t length = strlen(name);
    size_t digits = 0;
    while (digits < length && isDigitChar(name[length - 1 - digits])) {
        digits++;
    }
    if (digits == 0 || digits >= length || digits > 2 || length - digits >= baseSize) {
        return false;
    }
    unsigned value = 0;
    for (size_t i = length - digits; i < length; i++) {
        value = value * 10 + (name[i] - '0');
    }
    if (value >= AXIS_COUNT) {
        return false;
    }
    memcpy(base, name, length - digits);
    base[length - digits] = '\0';
    axis = (uint8_t)value;
    return true;
}

// Im Master liegen nur Systemparameter und das Handrad-Jog. Alles andere gehoert dem Modul.
static bool isLocalName(const char *name) {
    char base[12];
    uint8_t axis;
    if (!splitName(name, base, sizeof(base), axis)) {
        return true;
    }
    return strcmp(base, "jog") == 0 || strcmp(base, "jogfine") == 0 || strcmp(base, "link") == 0;
}

Protocol::Protocol(Stream &p, StateMachineController &m, ModuleLink *const *mod, Settings &s)
    : port(p), machine(m), modules(mod), settings(s) {
    length = 0;
    overflow = false;
    mode = MODE_IDLE;
    jobAxis = 0;
    jobIndex = 0;
    jobSent = false;
    jobCommand[0] = '\0';
    jobName[0] = '\0';
    telemetryStep = -1;
    lastTelemetry = 0;
    lastState = STATE_BOOT;
    lastError = ERR_NONE;
}

void Protocol::update() {
    readInput();
    pumpForward();
    pumpList();
    pumpBroadcast();
    pumpWheel();
    pumpTelemetry();
}

void Protocol::readInput() {
    while (port.available() > 0) {
        char c = (char)port.read();
        if (c == '\n' || c == '\r') {
            if (overflow) {
                reply(false, "LINE");
            } else if (length > 0) {
                line[length] = '\0';
                handleLine(line);
            }
            length = 0;
            overflow = false;
        } else if (length < sizeof(line) - 1) {
            line[length++] = c;
        } else {
            overflow = true;
        }
    }
}

void Protocol::handleLine(char *text) {
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

    // gueltige Zeile = Lebenszeichen der GUI
    machine.onHeartbeat();

    char *command = strtok(text, " ");
    char *arg1 = strtok(nullptr, " ");
    char *arg2 = strtok(nullptr, " ");
    if (command == nullptr) {
        return;
    }
    dispatch(command, arg1, arg2);
}

// Anfrage an ein Modul weiterreichen. Die Antwort holt pumpForward() ab.
bool Protocol::forwardTo(uint8_t axis, const char *command, const char *displayName) {
    if (!machine.isUsed(axis)) {
        reply(false, "UNUSED");     // link<n> = 2
        return false;
    }
    if (!machine.isLinked(axis)) {
        reply(false, "EXTERN");     // Modul haengt an der GUI, dort direkt ansprechen
        return false;
    }
    if (mode != MODE_IDLE) {
        reply(false, "BUSY");
        return false;
    }
    if (!modules[axis]->alive() || !modules[axis]->acquire(ModuleLink::OWNER_GUI)) {
        reply(false, "BUSY");
        return false;
    }
    if (!modules[axis]->send(command)) {
        modules[axis]->release();
        reply(false, "BUSY");
        return false;
    }
    mode = MODE_FORWARD;
    jobAxis = axis;
    jobName[0] = '\0';
    if (displayName != nullptr) {
        strncpy(jobName, displayName, sizeof(jobName) - 1);
        jobName[sizeof(jobName) - 1] = '\0';
    }
    return true;
}

void Protocol::dispatch(char *command, char *arg1, char *arg2) {
    float value;
    uint8_t axis;

    if (strcmp(command, "HB") == 0) {
        return;
    }
    if (strcmp(command, "VER") == 0) {
        LineBuilder b;
        b.str("V MIRI-MASTER ");
        b.str(FIRMWARE_VERSION);
        send(b.buf);
        return;
    }
    if (strcmp(command, "STATUS") == 0) {
        telemetryStep = 0;
        return;
    }

    if (strcmp(command, "AXIS") == 0) {
        // AXIS <achse> <state> <err>: Zustand eines Moduls, das an der GUI haengt. Keine Antwort.
        char *arg3 = strtok(nullptr, " ");
        float st;
        float er;
        if (!parseAxisValue(arg1, axis) || !parseFloat(arg2, st) || !parseFloat(arg3, er) ||
            st < 0.0f || st > 3.0f || er < 0.0f || er > 255.0f) {
            reply(false, "ARG");
            return;
        }
        if (!machine.reportAxis(axis, (MachineState)(int)st, (ErrorCode)(int)er)) {
            reply(false, "STATE");
        }
        return;
    }

    if (strcmp(command, "GET") == 0) {
        if (arg1 == nullptr) {
            reply(false, "NAME");
            return;
        }
        if (isLocalName(arg1)) {
            float v;
            if (!settingsGet(settings, arg1, v)) {
                reply(false, "NAME");
                return;
            }
            LineBuilder b;
            b.str("P ");
            b.str(arg1);
            b.chr(' ');
            b.flt(v, 3);
            send(b.buf);
            return;
        }
        char base[12];
        if (!splitName(arg1, base, sizeof(base), axis)) {
            reply(false, "NAME");
            return;
        }
        LineBuilder cmd;
        cmd.str("GET ");
        cmd.str(base);
        cmd.chr('0');
        forwardTo(axis, cmd.buf, arg1);
        return;
    }

    if (strcmp(command, "SET") == 0) {
        if (arg1 == nullptr || !parseFloat(arg2, value)) {
            reply(false, "ARG");
            return;
        }
        if (isLocalName(arg1)) {
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
            machine.applySettings();
            reply(true, nullptr);
            return;
        }
        char base[12];
        if (!splitName(arg1, base, sizeof(base), axis)) {
            reply(false, "NAME");
            return;
        }
        LineBuilder cmd;
        cmd.str("SET ");
        cmd.str(base);
        cmd.chr('0');
        cmd.chr(' ');
        cmd.str(arg2);
        forwardTo(axis, cmd.buf, nullptr);
        return;
    }

    if (strcmp(command, "LIST") == 0) {
        if (mode != MODE_IDLE) {
            reply(false, "BUSY");
            return;
        }
        mode = MODE_LIST_LOCAL;
        jobIndex = 0;
        return;
    }

    if (strcmp(command, "SAVE") == 0 || strcmp(command, "DEFAULTS") == 0) {
        if (mode != MODE_IDLE) {
            reply(false, "BUSY");
            return;
        }
        if (command[0] == 'S') {
            settingsSave(settings);
        } else {
            Settings d = settingsDefaults();
            settings.hbTimeoutMs = d.hbTimeoutMs;
            settings.bootTimeoutMs = d.bootTimeoutMs;
            settings.telemetryMs = d.telemetryMs;
            settings.wheelStepsPerDetent = d.wheelStepsPerDetent;
            for (uint8_t i = 0; i < AXIS_COUNT; i++) {
                settings.axis[i].jogStepDeg = d.axis[i].jogStepDeg;
                settings.axis[i].jogFineDeg = d.axis[i].jogFineDeg;
            }
            machine.applySettings();
        }
        strncpy(jobCommand, command, sizeof(jobCommand) - 1);
        jobCommand[sizeof(jobCommand) - 1] = '\0';
        mode = MODE_BROADCAST;
        jobAxis = 0;
        jobSent = false;
        return;
    }

    if (strcmp(command, "MOVE") == 0 || strcmp(command, "JOG") == 0) {
        if (!parseAxisValue(arg1, axis) || !parseFloat(arg2, value)) {
            reply(false, "ARG");
            return;
        }
        if (!machine.commandsAllowed()) {
            reply(false, "STATE");
            return;
        }
        LineBuilder cmd;
        cmd.str(command);
        cmd.chr(' ');
        cmd.flt(value, 3);
        forwardTo(axis, cmd.buf, nullptr);
        return;
    }

    if (strcmp(command, "FMOVE") == 0 || strcmp(command, "FJOG") == 0) {
        // Erzwungen, ohne Encoder: auch im Fehlerzustand, aber nie mit Laserfreigabe.
        if (!parseAxisValue(arg1, axis) || !parseFloat(arg2, value)) {
            reply(false, "ARG");
            return;
        }
        if (machine.isArmed()) {
            reply(false, "STATE");
            return;
        }
        LineBuilder cmd;
        cmd.str(command);
        cmd.chr(' ');
        cmd.flt(value, 3);
        forwardTo(axis, cmd.buf, nullptr);
        return;
    }

    if (strcmp(command, "HOME") == 0 || strcmp(command, "ZERO") == 0 || strcmp(command, "CAL") == 0) {
        if (!parseAxisValue(arg1, axis)) {
            reply(false, "ARG");
            return;
        }
        bool needsDisarmed = command[0] != 'H';
        if (!machine.commandsAllowed() || (needsDisarmed && machine.isArmed())) {
            reply(false, "STATE");
            return;
        }
        forwardTo(axis, command, nullptr);
        return;
    }

    if (strcmp(command, "STOP") == 0) {
        reply(machine.requestStop(), "STATE");
        return;
    }

    if (strcmp(command, "ARM") == 0) {
        if (arg1 == nullptr || (arg1[0] != '0' && arg1[0] != '1') || arg1[1] != '\0') {
            reply(false, "ARG");
            return;
        }
        reply(machine.requestArm(arg1[0] == '1'), "STATE");
        return;
    }

    if (strcmp(command, "ACK") == 0) {
        if (!parseFloat(arg1, value) || value < 0 || value > 255) {
            reply(false, "ARG");
            return;
        }
        reply(machine.acknowledge((uint8_t)value), "STATE");
        return;
    }

    reply(false, "UNK");
}

void Protocol::send(const char *payload) {
    uint8_t cs = lineChecksum(payload);
    port.print(payload);
    port.print('*');
    if (cs < 16) {
        port.print('0');
    }
    port.print(cs, HEX);
    port.print('\n');
}

void Protocol::reply(bool ok, const char *reason) {
    if (ok) {
        send("OK");
        return;
    }
    LineBuilder b;
    b.str("ERR ");
    b.str(reason != nullptr ? reason : "?");
    send(b.buf);
}

// "P gear0 1.000" vom Modul -> "P gear1 1.000" zur GUI (oder mit vorgegebenem Namen)
void Protocol::sendRenamedParam(const char *moduleLine, uint8_t axis, const char *displayName) {
    char copy[64];
    strncpy(copy, moduleLine, sizeof(copy) - 1);
    copy[sizeof(copy) - 1] = '\0';

    char *tag = strtok(copy, " ");
    char *name = strtok(nullptr, " ");
    char *value = strtok(nullptr, " ");
    if (tag == nullptr || name == nullptr || value == nullptr) {
        reply(false, "MODULE");
        return;
    }

    LineBuilder b;
    b.str("P ");
    if (displayName != nullptr && displayName[0] != '\0') {
        b.str(displayName);
    } else {
        // Achsnummer 0 des Moduls durch die Achse des Masters ersetzen
        size_t n = strlen(name);
        if (n > 0 && name[n - 1] == '0') {
            name[n - 1] = '\0';
        }
        b.str(name);
        b.num(axis);
    }
    b.chr(' ');
    b.str(value);
    send(b.buf);
}

void Protocol::abortJob(const char *reason) {
    if ((mode == MODE_FORWARD || mode == MODE_LIST_MODULE || mode == MODE_BROADCAST) &&
        jobAxis < AXIS_COUNT) {
        modules[jobAxis]->release();
    }
    mode = MODE_IDLE;
    reply(false, reason);
}

// Einzelne Anfrage: Antwort des Moduls an die GUI zurueckgeben.
void Protocol::pumpForward() {
    if (mode != MODE_FORWARD) {
        return;
    }
    ModuleLink *link = modules[jobAxis];

    if (link->replyReady()) {
        const char *text = link->reply();
        if (text[0] == 'P' && text[1] == ' ') {
            sendRenamedParam(text, jobAxis, jobName);
        } else {
            send(text);
        }
        link->consumeReply();
        link->release();
        mode = MODE_IDLE;
    } else if (link->expired(FORWARD_TIMEOUT_MS)) {
        abortJob("TIMEOUT");
    }
}

// LIST: erst die lokalen Parameter, dann je Modul dessen Liste.
void Protocol::pumpList() {
    if (mode == MODE_LIST_LOCAL) {
        if (port.availableForWrite() < 40) {
            return;
        }
        // naechsten lokalen Parameter suchen
        while (jobIndex < settingsCount()) {
            char name[12];
            if (settingsNameAt(jobIndex, name, sizeof(name)) && isLocalName(name)) {
                float v;
                settingsGetAt(settings, jobIndex, v);
                LineBuilder b;
                b.str("P ");
                b.str(name);
                b.chr(' ');
                b.flt(v, 3);
                send(b.buf);
                jobIndex++;
                return;
            }
            jobIndex++;
        }
        mode = MODE_LIST_MODULE;
        jobAxis = 0;
        jobSent = false;
        return;
    }

    if (mode != MODE_LIST_MODULE) {
        return;
    }

    // Module an der GUI gehoeren nicht in die Liste des Masters
    while (!jobSent && jobAxis < AXIS_COUNT && !machine.isLinked(jobAxis)) {
        jobAxis++;
    }
    if (jobAxis >= AXIS_COUNT) {
        mode = MODE_IDLE;
        reply(true, nullptr);
        return;
    }

    ModuleLink *link = modules[jobAxis];

    if (!jobSent) {
        if (!link->alive()) {
            abortJob("MODULE");
            return;
        }
        if (link->owner() == ModuleLink::OWNER_NONE && link->acquire(ModuleLink::OWNER_GUI)) {
            if (link->send("LIST")) {
                jobSent = true;
            } else {
                link->release();
            }
        }
        return;
    }

    if (link->replyReady()) {
        const char *text = link->reply();
        if (text[0] == 'P' && text[1] == ' ') {
            if (port.availableForWrite() < 40) {
                return;     // Antwort bleibt liegen, naechster Durchlauf
            }
            sendRenamedParam(text, jobAxis, nullptr);
            link->consumeReply();
            return;
        }
        bool ok = text[0] == 'O' && text[1] == 'K';
        link->consumeReply();
        link->release();
        if (!ok) {
            mode = MODE_IDLE;
            reply(false, "MODULE");
            return;
        }
        jobAxis++;
        jobSent = false;
        if (jobAxis >= AXIS_COUNT) {
            mode = MODE_IDLE;
            reply(true, nullptr);
        }
    } else if (link->expired(FORWARD_TIMEOUT_MS)) {
        abortJob("TIMEOUT");
    }
}

// SAVE / DEFAULTS: der Reihe nach an jedes Modul, am Ende ein OK.
void Protocol::pumpBroadcast() {
    if (mode != MODE_BROADCAST) {
        return;
    }

    while (!jobSent && jobAxis < AXIS_COUNT && !machine.isLinked(jobAxis)) {
        jobAxis++;
    }
    if (jobAxis >= AXIS_COUNT) {
        mode = MODE_IDLE;
        reply(true, nullptr);
        return;
    }

    ModuleLink *link = modules[jobAxis];

    if (!jobSent) {
        if (!link->alive()) {
            abortJob("MODULE");
            return;
        }
        if (link->acquire(ModuleLink::OWNER_GUI)) {
            if (link->send(jobCommand)) {
                jobSent = true;
            } else {
                link->release();
            }
        }
        return;
    }

    if (link->replyReady()) {
        bool ok = link->reply()[0] == 'O' && link->reply()[1] == 'K';
        link->consumeReply();
        link->release();
        if (!ok) {
            mode = MODE_IDLE;
            reply(false, "MODULE");
            return;
        }
        jobAxis++;
        jobSent = false;
        if (jobAxis >= AXIS_COUNT) {
            mode = MODE_IDLE;
            reply(true, nullptr);
        }
    } else if (link->expired(FORWARD_TIMEOUT_MS)) {
        abortJob("TIMEOUT");
    }
}

void Protocol::sendSummary() {
    LineBuilder b;
    b.str("S ");
    b.num(machine.getState());
    b.chr(' ');
    b.num(machine.getError());
    b.chr(' ');
    b.num(machine.getErrorAxis());
    b.chr(' ');
    b.num(machine.isArmed());
    b.chr(' ');
    b.num(machine.isKeyOn());
    b.chr(' ');
    b.num(machine.isLaserOn());
    b.chr(' ');
    b.num(machine.isFineMode());
    b.chr(' ');
    b.num(machine.getSelectedAxis());
    send(b.buf);
}

void Protocol::sendAxis(uint8_t axis) {
    bool linked = machine.isLinked(axis);
    const ModuleLink &m = *modules[axis];
    LineBuilder a;
    a.str("A ");
    a.num(axis);
    a.chr(' ');
    // Position, Ziel, Encoder und Treiber kennt der Master nur bei Modulen am UART
    a.num(linked ? m.busy() : 0);
    a.chr(' ');
    a.flt(linked ? m.position() : 0.0f, 2);
    a.chr(' ');
    a.flt(linked ? m.target() : 0.0f, 2);
    a.chr(' ');
    a.num(linked ? m.encoderStatus() : 0);
    a.chr(' ');
    a.num(linked ? m.driverStatus() : 0);
    a.chr(' ');
    a.num(machine.axisState(axis));
    a.chr(' ');
    a.num(machine.axisAlive(axis));
    a.chr(' ');
    a.num(machine.linkMode(axis));
    send(a.buf);
}

// Handrad-Weg fuer Achsen an der GUI: "W <achse> <grad>", die GUI schickt JOG an den Nano.
void Protocol::pumpWheel() {
    if (port.availableForWrite() < 32) {
        return;
    }
    uint8_t axis;
    float degrees;
    if (machine.takeExternalJog(axis, degrees)) {
        LineBuilder b;
        b.str("W ");
        b.num(axis);
        b.chr(' ');
        b.flt(degrees, 3);
        send(b.buf);
    }
}

// Telemetrie in Haeppchen (eine Zeile je Aufruf), damit der Sendepuffer nie blockiert.
void Protocol::pumpTelemetry() {
    unsigned long now = millis();

    if (telemetryStep < 0) {
        bool changed = machine.getState() != lastState || machine.getError() != lastError;
        if (!changed && now - lastTelemetry < settings.telemetryMs) {
            return;
        }
        telemetryStep = 0;
        lastTelemetry = now;
        lastState = machine.getState();
        lastError = machine.getError();
    }

    if (port.availableForWrite() < 56) {
        return;
    }

    if (telemetryStep == 0) {
        sendSummary();
        telemetryStep = 1;
    } else {
        sendAxis((uint8_t)(telemetryStep - 1));
        telemetryStep++;
    }
    if (telemetryStep > (int16_t)AXIS_COUNT) {
        telemetryStep = -1;
    }
}
