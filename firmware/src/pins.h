#pragma once

#include <stdint.h>

// Platzhalter, bitte an die tatsaechliche Verdrahtung anpassen.
//
// ESP32 (NodeMCU-32S). Fest vorgegeben: GPIO1/3 = USB-Serial zur GUI.
// Strapping-Pins 0, 2, 5, 12, 15 und die Flash-Pins 6..11 nicht fuer Sicherheitsrelevantes
// verwenden. GPIO34..39 sind nur Eingaenge.

#include <Arduino.h>

#if defined(ARDUINO_ARCH_ESP32)

// ===== ESP32 (NodeMCU-32S) als Master der Achsmodule =====
// Fest vorgegeben: GPIO1/3 = USB-Serial zur GUI.
// Strapping-Pins 0, 2, 5, 12, 15 und die Flash-Pins 6..11 nicht fuer Sicherheitsrelevantes
// verwenden. GPIO34..39 sind nur Eingaenge.

static const uint8_t PIN_STATUS_LED = 27;       // externe LED (Anode ueber ca. 330 Ohm, Kathode GND)

static const uint8_t PIN_KEY_SWITCH = 26;       // Schluesselschalter gegen GND
static const uint8_t PIN_LASER_RELAY = 25;      // zusaetzlich Pull-down (ca. 10 kOhm) gegen GND
static const bool LASER_ACTIVE_HIGH = true;

// EC11 (Handrad), alle drei Kontakte gegen GND. Der Taster schaltet durch:
// Achse 0 grob, Achse 0 fein, Achse 1 grob, ...
static const uint8_t PIN_WHEEL_A = 32;
static const uint8_t PIN_WHEEL_B = 33;
static const uint8_t PIN_WHEEL_BUTTON = 13;

// Achsmodule (Nano): je Modul ein eigener UART am ESP32 (1 = Serial1, 2 = Serial2).
// Der Nano sendet mit 5 V -> ESP32-RX nur ueber Spannungsteiler (z. B. 1 kOhm / 2 kOhm).
// Je Achse EINE Spalte in JEDER Tabelle, die Achsenzahl folgt aus den Tabellen.
//
//   Achse                      0      1
static const uint8_t MODULE_UART_PORT[] = { 2,     1};
static const uint8_t PIN_MODULE_RX[]    = {16,    34};   // 34: nur Eingang, fuer RX ok
static const uint8_t PIN_MODULE_TX[]    = {17,     4};

static const uint8_t AXIS_COUNT = sizeof(MODULE_UART_PORT) / sizeof(MODULE_UART_PORT[0]);

static_assert(AXIS_COUNT >= 1 && AXIS_COUNT <= 2, "ESP32: zwei freie Hardware-UARTs (Serial1, Serial2)");
static_assert(sizeof(PIN_MODULE_RX) == AXIS_COUNT, "PIN_MODULE_RX: Eintrag je Achse");
static_assert(sizeof(PIN_MODULE_TX) == AXIS_COUNT, "PIN_MODULE_TX: Eintrag je Achse");

#else

// ===== Arduino Nano als Achsmodul (nur AS5600 + TMC2209, eine Achse) =====
// Fest vorgegeben: D0/D1 = USB-Serial bzw. RX/TX zur NodeMCU, A4/A5 = I2C (AS5600).
static const uint8_t PIN_STATUS_LED = 13;

// Vertauscht gegenueber der ersten Annahme: die Schritt-Probe im Motortest hat gezeigt,
// dass D9 am STEP-Eingang des Treibers haengt und D8 an DIR.
static const uint8_t PIN_STEP[]            = {9};
static const uint8_t PIN_DIR[]             = {8};
static const uint8_t PIN_STEPPER_EN[]      = {7};
static const uint8_t TMC_UART_PORT[]       = {0};      // SoftwareSerial
static const uint8_t PIN_TMC_RX[]          = {10};     // RX und TX am Modul zusammen auf PDN_UART,
static const uint8_t PIN_TMC_TX[]          = {11};     // TX ueber ca. 1 kOhm
static const uint8_t TMC_DRIVER_ADDRESS[]  = {0};
static const uint8_t I2C_BUS[]             = {0};
static const uint8_t PIN_I2C_SDA[]         = {A4};
static const uint8_t PIN_I2C_SCL[]         = {A5};
static const uint8_t ENCODER_I2C_ADDRESS[] = {0x36};

static const uint8_t AXIS_COUNT = sizeof(PIN_STEP) / sizeof(PIN_STEP[0]);

static_assert(AXIS_COUNT == 1, "Achsmodul hat genau eine Achse");

#endif

static const float TMC_RSENSE = 0.11f;          // Ohm, Messwiderstand auf dem Modul pruefen
