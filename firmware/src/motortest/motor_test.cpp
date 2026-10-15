// Motortest fuer das Achsmodul (Nano + TMC2209), unabhaengig von der eigentlichen Firmware.
//
// Stellt den Treiber per UART ein, liest die Register zur Kontrolle zurueck und faehrt dann
// mit festem Takt aus einer Warteschleife (kein AccelStepper, kein Encoder, kein Protokoll):
// je Geschwindigkeit eine Umdrehung vor und zurueck. Ausgabe im seriellen Monitor, 115200 Baud.
//
//   pio run -e motor_test -t upload && pio device monitor
//
// Laeuft der Motor hier sauber, liegt ein Ruckeln an der Firmware. Ruckelt er auch hier,
// liegt es an Verdrahtung, Netzteil oder Treiber.

#include <Arduino.h>
#include <TMCStepper.h>
#include "../pins.h"

static const uint16_t CURRENT_MA = 600;
static const uint16_t MICROSTEPS = 16;
static const uint32_t STEPS_PER_REV = 200UL * MICROSTEPS;
static const float SPEEDS_REV_PER_SEC[] = {0.25f, 0.5f, 1.0f};

static TMC2209Stepper driver(PIN_TMC_RX[0], PIN_TMC_TX[0], TMC_RSENSE, TMC_DRIVER_ADDRESS[0]);

static void printHex(const char *name, uint32_t value) {
    Serial.print(name);
    Serial.print(F(" = 0x"));
    Serial.println(value, HEX);
}

static void configureDriver() {
    driver.beginSerial(19200);
    driver.pdn_disable(true);
    driver.mstep_reg_select(true);
    driver.I_scale_analog(false);
    driver.toff(5);
    driver.en_spreadCycle(true);
    driver.rms_current(CURRENT_MA);
    driver.microsteps(MICROSTEPS);
    driver.GSTAT(0x07);         // Reset-Bit quittieren, taucht es wieder auf: Treiber neu gestartet
}

static void reportDriver() {
    uint8_t connection = driver.test_connection();
    Serial.print(F("UART-Verbindung: "));
    if (connection == 0) {
        Serial.println(F("ok"));
    } else {
        Serial.print(F("FEHLER "));
        Serial.println(connection);     // 1: Leitung offen (0xFFFFFFFF), 2: nur Nullen
    }
    printHex("Chipversion (soll 0x21)", driver.version());
    Serial.print(F("Mikroschritte gelesen: "));
    Serial.println(driver.microsteps());
    Serial.print(F("SpreadCycle gelesen: "));
    Serial.println(driver.en_spreadCycle() ? F("ja") : F("nein"));
    Serial.print(F("Strom gesetzt [mA]: "));
    Serial.println(driver.rms_current());
    Serial.print(F("Stromskala CS: "));
    Serial.println(driver.cs_actual());
    printHex("GSTAT", driver.GSTAT());
    printHex("DRV_STATUS", driver.DRV_STATUS());
}

// Feste Schrittfolge, Takt nur durch delayMicroseconds bestimmt.
static void turn(bool forward, uint32_t steps, uint32_t intervalUs) {
    digitalWrite(PIN_DIR[0], forward ? HIGH : LOW);
    delayMicroseconds(20);
    for (uint32_t i = 0; i < steps; i++) {
        digitalWrite(PIN_STEP[0], HIGH);
        delayMicroseconds(4);
        digitalWrite(PIN_STEP[0], LOW);
        if (intervalUs > 16383) {
            delay(intervalUs / 1000);
        } else {
            delayMicroseconds(intervalUs - 4);
        }
    }
}

void setup() {
    pinMode(PIN_STEPPER_EN[0], OUTPUT);
    digitalWrite(PIN_STEPPER_EN[0], HIGH);     // Treiber aus, bis er eingestellt ist
    pinMode(PIN_STEP[0], OUTPUT);
    pinMode(PIN_DIR[0], OUTPUT);
    digitalWrite(PIN_STEP[0], LOW);

    Serial.begin(115200);
    delay(500);
    Serial.println(F("\n=== MIRI Motortest ==="));

    configureDriver();
    reportDriver();

    digitalWrite(PIN_STEPPER_EN[0], LOW);
    delay(200);
}

void loop() {
    for (uint8_t i = 0; i < sizeof(SPEEDS_REV_PER_SEC) / sizeof(SPEEDS_REV_PER_SEC[0]); i++) {
        float rps = SPEEDS_REV_PER_SEC[i];
        uint32_t intervalUs = (uint32_t)(1000000.0f / (rps * STEPS_PER_REV));
        Serial.print(F("\n"));
        Serial.print(rps);
        Serial.println(F(" U/s: eine Umdrehung vor, eine zurueck"));
        turn(true, STEPS_PER_REV, intervalUs);
        delay(500);
        turn(false, STEPS_PER_REV, intervalUs);
        delay(500);
        printHex("DRV_STATUS", driver.DRV_STATUS());
        uint8_t gstat = driver.GSTAT();
        if (gstat & 0x01) {
            Serial.println(F("!!! Treiber hat neu gestartet (Spannung an VM eingebrochen?), "
                             "Einstellungen verloren -> neu schreiben"));
            configureDriver();
            reportDriver();
        } else if (gstat & 0x06) {
            printHex("!!! GSTAT Fehler (2 = drv_err, 4 = Unterspannung)", gstat);
            driver.GSTAT(0x07);
        }
    }
    delay(1500);
}
