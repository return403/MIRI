#pragma once

#include <stdint.h>

// EC11 Drehgeber mit Taster, alle Kontakte gegen GND (INPUT_PULLUP).
// Dreht das Rad in die falsche Richtung, A und B vertauschen.
class EncoderWheel {
    public:
        EncoderWheel(uint8_t pinA, uint8_t pinB, uint8_t pinButton);

        void begin();
        void update();

        void setStepsPerDetent(uint8_t steps);

        // Rasten seit dem letzten Aufruf (rechts +, links -), setzt zurueck
        int16_t takeDelta();
        // true, wenn der Taster seit dem letzten Aufruf gedrueckt wurde
        bool takeButtonPress();

    private:
        uint8_t pinA;
        uint8_t pinB;
        uint8_t pinButton;

        uint8_t lastState;
        int8_t quarterSteps;
        uint8_t stepsPerDetent;
        int16_t detents;

        bool buttonRaw;
        bool buttonStable;
        bool buttonEvent;
        unsigned long buttonChanged;
};
