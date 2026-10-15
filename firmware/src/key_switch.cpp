#include "key_switch.h"
#include <Arduino.h>

static const unsigned long DEBOUNCE_MS = 20;

KeySwitch::KeySwitch(int pin) {
    this->pin = pin;
    rawState = false;
    stableState = false;
    lastChange = 0;
}

void KeySwitch::begin() {
    pinMode(pin, INPUT_PULLUP);
    // fail-safe: gilt als "nicht gedrueckt", bis es entprellt gelesen wurde
    rawState = false;
    stableState = false;
    lastChange = millis();
}

void KeySwitch::update() {
    bool reading = digitalRead(pin) == LOW;
    unsigned long now = millis();

    if (reading != rawState) {
        rawState = reading;
        lastChange = now;
    }
    if (rawState != stableState && now - lastChange >= DEBOUNCE_MS) {
        stableState = rawState;
    }
}

bool KeySwitch::isPressed() const {
    return stableState;
}
