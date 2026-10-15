#include "laser_relay.h"
#include <Arduino.h>

LaserRelay::LaserRelay(uint8_t pin, bool activeHigh) {
    this->pin = pin;
    this->activeHigh = activeHigh;
    state = false;
}

void LaserRelay::begin() {
    // erst den inaktiven Pegel setzen, dann Ausgang: kein Impuls beim Start
    digitalWrite(pin, activeHigh ? LOW : HIGH);
    pinMode(pin, OUTPUT);
    state = false;
}

void LaserRelay::write(bool on) {
    state = on;
    digitalWrite(pin, (on == activeHigh) ? HIGH : LOW);
}

void LaserRelay::on() {
    write(true);
}

void LaserRelay::off() {
    write(false);
}

bool LaserRelay::isOn() const {
    return state;
}
