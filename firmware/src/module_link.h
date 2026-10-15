#pragma once

#include <Arduino.h>
#include <stdint.h>
#include "machine_types.h"

// Verbindung des Masters zu einem Achsmodul (Nano) ueber einen Hardware-UART.
// Zeilenformat wie in slave_main.cpp: <Nutzdaten>*<HH>\n, HH = XOR der Nutzdaten.
//
// Telemetrie des Moduls ("M ...") wird laufend ausgewertet. Alle anderen Zeilen sind
// Antworten auf Befehle. Es ist immer nur ein Antwort-Besitzer moeglich:
//   OWNER_GUI      : eine von der GUI weitergeleitete Anfrage, Antwort bleibt zum Abholen liegen
//   OWNER_INTERNAL : Befehl der Master-Firmware selbst, Antwort wird verworfen
// Befehle ohne acquire() (HB, ESTOP, STOP) sind "abschicken und vergessen".
class ModuleLink {
    public:
        enum Owner : uint8_t {
            OWNER_NONE,
            OWNER_INTERNAL,
            OWNER_GUI
        };

        ModuleLink(HardwareSerial &port, uint8_t rxPin, uint8_t txPin);

        void begin(uint32_t baud);
        // aliveTimeoutMs: so lange darf die letzte Telemetrie hoechstens her sein
        void update(unsigned long aliveTimeoutMs);

        bool alive() const;
        MachineState state() const;
        ErrorCode error() const;
        bool busy() const;
        float position() const;
        float target() const;
        uint8_t encoderStatus() const;
        uint8_t driverStatus() const;

        bool acquire(Owner who);
        void release();
        Owner owner() const;
        bool expired(unsigned long timeoutMs) const;

        // haengt die Pruefsumme an und sendet, false wenn der Sendepuffer voll ist
        bool send(const char *payload);

        bool replyReady() const;
        const char *reply() const;
        void consumeReply();

    private:
        void handleLine(char *text);

        HardwareSerial &port;
        uint8_t rxPin;
        uint8_t txPin;

        char line[64];
        uint8_t lineLength;
        bool lineOverflow;

        char replyBuffer[64];
        bool hasReply;

        Owner currentOwner;
        unsigned long ownerSince;

        bool telemetrySeen;
        unsigned long lastTelemetry;
        unsigned long aliveTimeout;

        MachineState moduleState;
        ErrorCode moduleError;
        bool moduleBusy;
        float modulePosition;
        float moduleTarget;
        uint8_t moduleEncoder;
        uint8_t moduleDriver;
};
