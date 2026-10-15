#include "status_leds.h"
#include <Arduino.h>

LED::LED(int pin, unsigned long interval) {
    this->pin = pin;
    this->interval = interval;
    state = LED_OFF;
    previousMillis = 0;
    level = false;
}

void LED::begin() {
    pinMode(pin, OUTPUT);
    off();
}

void LED::on() {
    state = LED_ON;
    level = true;
    digitalWrite(pin, HIGH);
}

void LED::off() {
    state = LED_OFF;
    level = false;
    digitalWrite(pin, LOW);
}

void LED::blink() {
    state = LED_BLINK;
}

void LED::setInterval(unsigned long interval) {
    this->interval = interval;
}

void LED::update() {
    if (state != LED_BLINK) {
        return;
    }
    unsigned long now = millis();
    if (now - previousMillis >= interval) {
        previousMillis = now;
        level = !level;
        digitalWrite(pin, level ? HIGH : LOW);
    }
}
