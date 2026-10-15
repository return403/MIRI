#include "module_link.h"
#include "line_utils.h"
#include <string.h>

ModuleLink::ModuleLink(HardwareSerial &p, uint8_t rx, uint8_t tx)
    : port(p), rxPin(rx), txPin(tx) {
    lineLength = 0;
    lineOverflow = false;
    hasReply = false;
    replyBuffer[0] = '\0';
    currentOwner = OWNER_NONE;
    ownerSince = 0;
    telemetrySeen = false;
    lastTelemetry = 0;
    aliveTimeout = 1500;
    moduleState = STATE_BOOT;
    moduleError = ERR_NONE;
    moduleBusy = false;
    modulePosition = 0.0f;
    moduleTarget = 0.0f;
    moduleEncoder = 1;
    moduleDriver = 1;
}

void ModuleLink::begin(uint32_t baud) {
    port.begin(baud, SERIAL_8N1, rxPin, txPin);
}

void ModuleLink::update(unsigned long aliveTimeoutMs) {
    aliveTimeout = aliveTimeoutMs;

    // Liegt eine Antwort noch beim Abholen, nichts Neues einlesen (bleibt im UART-Puffer).
    while (!hasReply && port.available() > 0) {
        char c = (char)port.read();
        if (c == '\n' || c == '\r') {
            if (!lineOverflow && lineLength > 0) {
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

void ModuleLink::handleLine(char *text) {
    char *star = strrchr(text, '*');
    if (star == nullptr || strlen(star) != 3) {
        return;
    }
    int high = hexValue(star[1]);
    int low = hexValue(star[2]);
    *star = '\0';
    if (high < 0 || low < 0 || ((high << 4) | low) != lineChecksum(text)) {
        return;
    }

    if (text[0] == 'M' && text[1] == ' ') {
        // M <state> <err> <busy> <pos> <ziel> <enc> <drv>
        char *token = strtok(text + 2, " ");
        float v[7];
        uint8_t count = 0;
        while (token != nullptr && count < 7) {
            if (!parseFloat(token, v[count])) {
                return;
            }
            count++;
            token = strtok(nullptr, " ");
        }
        if (count != 7) {
            return;
        }
        moduleState = (MachineState)(int)v[0];
        moduleError = (ErrorCode)(int)v[1];
        moduleBusy = v[2] > 0.5f;
        modulePosition = v[3];
        moduleTarget = v[4];
        moduleEncoder = (uint8_t)v[5];
        moduleDriver = (uint8_t)v[6];
        telemetrySeen = true;
        lastTelemetry = millis();
        return;
    }

    // Antwort auf einen Befehl
    if (currentOwner == OWNER_GUI) {
        strncpy(replyBuffer, text, sizeof(replyBuffer) - 1);
        replyBuffer[sizeof(replyBuffer) - 1] = '\0';
        hasReply = true;
    } else if (currentOwner == OWNER_INTERNAL) {
        currentOwner = OWNER_NONE;      // intern erledigt, Antwort wird nicht gebraucht
    }
    // OWNER_NONE: Antwort auf "abschicken und vergessen", verwerfen
}

bool ModuleLink::alive() const {
    return telemetrySeen && (millis() - lastTelemetry) <= aliveTimeout;
}

MachineState ModuleLink::state() const { return moduleState; }
ErrorCode ModuleLink::error() const { return moduleError; }
bool ModuleLink::busy() const { return moduleBusy; }
float ModuleLink::position() const { return modulePosition; }
float ModuleLink::target() const { return moduleTarget; }
uint8_t ModuleLink::encoderStatus() const { return moduleEncoder; }
uint8_t ModuleLink::driverStatus() const { return moduleDriver; }

bool ModuleLink::acquire(Owner who) {
    if (currentOwner != OWNER_NONE) {
        return false;
    }
    currentOwner = who;
    ownerSince = millis();
    hasReply = false;
    return true;
}

void ModuleLink::release() {
    currentOwner = OWNER_NONE;
    hasReply = false;
}

ModuleLink::Owner ModuleLink::owner() const {
    return currentOwner;
}

bool ModuleLink::expired(unsigned long timeoutMs) const {
    return currentOwner != OWNER_NONE && (millis() - ownerSince) > timeoutMs;
}

bool ModuleLink::send(const char *payload) {
    size_t length = strlen(payload);
    if ((size_t)port.availableForWrite() < length + 5) {
        return false;
    }
    uint8_t cs = lineChecksum(payload);
    port.print(payload);
    port.print('*');
    if (cs < 16) {
        port.print('0');
    }
    port.print(cs, HEX);
    port.print('\n');
    return true;
}

bool ModuleLink::replyReady() const {
    return hasReply;
}

const char *ModuleLink::reply() const {
    return replyBuffer;
}

void ModuleLink::consumeReply() {
    hasReply = false;
    replyBuffer[0] = '\0';
}
