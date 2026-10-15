#include "encoder_wheel.h"
#include <Arduino.h>

static const unsigned long BUTTON_DEBOUNCE_MS = 25;

// Index: (alter Zustand << 2) | neuer Zustand, Zustand = (A << 1) | B.
// Ungueltige Spruenge (Prellen, ueberlesene Schritte) zaehlen 0.
static const int8_t TRANSITIONS[16] = {
     0, -1,  1,  0,
     1,  0,  0, -1,
    -1,  0,  0,  1,
     0,  1, -1,  0
};

EncoderWheel::EncoderWheel(uint8_t pinA, uint8_t pinB, uint8_t pinButton) {
    this->pinA = pinA;
    this->pinB = pinB;
    this->pinButton = pinButton;
    lastState = 0;
    quarterSteps = 0;
    stepsPerDetent = 4;
    detents = 0;
    buttonRaw = false;
    buttonStable = false;
    buttonEvent = false;
    buttonChanged = 0;
}

void EncoderWheel::begin() {
    pinMode(pinA, INPUT_PULLUP);
    pinMode(pinB, INPUT_PULLUP);
    pinMode(pinButton, INPUT_PULLUP);
    lastState = (digitalRead(pinA) << 1) | digitalRead(pinB);
    quarterSteps = 0;
    detents = 0;
    buttonRaw = false;
    buttonStable = false;
    buttonEvent = false;
    buttonChanged = millis();
}

void EncoderWheel::setStepsPerDetent(uint8_t steps) {
    if (steps >= 1) {
        stepsPerDetent = steps;
    }
}

void EncoderWheel::update() {
    uint8_t state = (digitalRead(pinA) << 1) | digitalRead(pinB);
    if (state != lastState) {
        quarterSteps += TRANSITIONS[(lastState << 2) | state];
        lastState = state;

        if (quarterSteps >= (int8_t)stepsPerDetent) {
            detents++;
            quarterSteps = 0;
        } else if (quarterSteps <= -(int8_t)stepsPerDetent) {
            detents--;
            quarterSteps = 0;
        }
    }

    unsigned long now = millis();
    bool reading = digitalRead(pinButton) == LOW;
    if (reading != buttonRaw) {
        buttonRaw = reading;
        buttonChanged = now;
    }
    if (buttonRaw != buttonStable && now - buttonChanged >= BUTTON_DEBOUNCE_MS) {
        buttonStable = buttonRaw;
        if (buttonStable) {
            buttonEvent = true;
        }
    }
}

int16_t EncoderWheel::takeDelta() {
    int16_t d = detents;
    detents = 0;
    return d;
}

bool EncoderWheel::takeButtonPress() {
    bool e = buttonEvent;
    buttonEvent = false;
    return e;
}
