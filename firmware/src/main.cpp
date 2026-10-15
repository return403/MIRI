#include <Arduino.h>
#include <esp_task_wdt.h>

#include "pins.h"
#include "settings.h"
#include "status_leds.h"
#include "key_switch.h"
#include "laser_relay.h"
#include "encoder_wheel.h"
#include "module_link.h"
#include "state_machine.h"
#include "protocol.h"

// Master: spricht ueber je einen UART mit den Nano-Achsmodulen (src/slave) und mit der GUI
// ueber USB. Laser, Schluessel und Handrad haengen hier, nicht an den Modulen.

static Settings settings;

static LED statusLed(PIN_STATUS_LED, 500);
static KeySwitch key(PIN_KEY_SWITCH);
static LaserRelay laser(PIN_LASER_RELAY, LASER_ACTIVE_HIGH);
static EncoderWheel wheel(PIN_WHEEL_A, PIN_WHEEL_B, PIN_WHEEL_BUTTON);

// Je Achse eine Verbindung zu einem Modul, in setup() anhand der Tabellen in pins.h angelegt.
static ModuleLink *links[AXIS_COUNT];

static StateMachineController machine(settings, links, laser, key, wheel);
static Protocol protocol(Serial, machine, links, settings);

static HardwareSerial &portFor(uint8_t index) {
    return index == 1 ? Serial1 : Serial2;
}

// LED: BOOT langsam blinken, HOMING mittel, READY an, Laser an schnell, ERROR sehr schnell
static void updateStatusLed() {
    switch (machine.getState()) {
        case STATE_BOOT:
            statusLed.setInterval(700);
            statusLed.blink();
            break;
        case STATE_HOMING:
            statusLed.setInterval(300);
            statusLed.blink();
            break;
        case STATE_READY:
            if (machine.isLaserOn()) {
                statusLed.setInterval(150);
                statusLed.blink();
            } else {
                statusLed.on();
            }
            break;
        case STATE_ERROR:
            statusLed.setInterval(60);
            statusLed.blink();
            break;
    }
    statusLed.update();
}

void setup() {
    laser.begin();                  // zuerst: Laser sicher aus
    Serial.begin(115200);

    statusLed.begin();
    key.begin();
    wheel.begin();

    settingsLoad(settings);
    wheel.setStepsPerDetent(settings.wheelStepsPerDetent);

    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        links[i] = new ModuleLink(portFor(MODULE_UART_PORT[i]), PIN_MODULE_RX[i], PIN_MODULE_TX[i]);
        links[i]->begin(115200);
    }

    machine.begin();

    // haengt die Firmware, setzt der Watchdog zurueck (Relais faellt ab)
    esp_task_wdt_init(3, true);
    esp_task_wdt_add(NULL);
}

void loop() {
    esp_task_wdt_reset();
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        links[i]->update(settings.hbTimeoutMs);
    }
    protocol.update();
    machine.update();
    updateStatusLed();
}
