#pragma once

#include <stdint.h>
#include <TMCStepper.h>
#include <AccelStepper.h>

enum StepperStatus {
    STEPPER_OK,
    STEPPER_NO_COMMUNICATION,
    STEPPER_OVERTEMP_WARNING,
    STEPPER_OVERTEMP,
    STEPPER_DRIVER_ERROR
};

// Alles, was noch nicht feststeht, steht hier. Pins muessen gesetzt werden.
struct StepperConfig {
    uint8_t stepPin = 255;
    uint8_t dirPin = 255;
    uint8_t enablePin = 255;
#if defined(ARDUINO_ARCH_ESP32)
    HardwareSerial *uart = &Serial2;    // UART des Treibers (mehrere Achsen koennen teilen)
#endif
    uint8_t uartRxPin = 255;
    uint8_t uartTxPin = 255;

    uint8_t driverAddress = 0;          // MS1/MS2 am Treiber
    float rSense = 0.11f;               // Ohm, Messwiderstand auf dem Modul
    uint16_t rmsCurrentMa = 400;        // bewusst niedrig, bis der Motor bekannt ist
    uint16_t microsteps = 16;           // 1, 2, 4 ... 256
    float gearRatio = 1.0f;             // Motorumdrehungen pro Ausgangsumdrehung
#if defined(ARDUINO_ARCH_ESP32)
    uint32_t uartBaud = 115200;         // Hardware-UART
#else
    uint32_t uartBaud = 19200;          // SoftwareSerial, nicht zu hoch waehlen
#endif

    float maxSpeedDegPerSec = 90.0f;    // am Ausgang
    float accelDegPerSec2 = 180.0f;     // am Ausgang
};

class StepperTMC2209 {
    public:
        StepperTMC2209(const StepperConfig &config);

        bool begin();
        // allowStatusRead = false, solange irgendeine Achse faehrt (UART-Lesen blockiert kurz)
        void update(bool allowStatusRead = true);

        void enable();
        void disable();

        void moveToDegrees(float degrees);
        void moveByDegrees(float degrees);
        void stop();
        void emergencyStop();

        bool isMoving();
        bool isEnabled() const;
        float getPositionDegrees();
        float getTargetDegrees();
        void setPositionDegrees(float degrees);

        // Nur im Stillstand moeglich, geben false zurueck wenn abgelehnt.
        bool setMicrosteps(uint16_t microsteps);
        bool setCurrentMa(uint16_t rmsCurrentMa);
        bool setGearRatio(float gearRatio);
        bool setDirectionInverted(bool inverted);
        bool setMaxSpeed(float degPerSec);
        bool setAcceleration(float degPerSec2);

        StepperStatus getStatus() const;
        void refreshStatus();

    private:
        float stepsPerDegree() const;
        void applySpeedLimits();
        void configureDriver();

        StepperConfig config;
        TMC2209Stepper driver;
        AccelStepper motor;

        StepperStatus status;
        bool enabled;
        bool dirInverted;
        unsigned long lastStatusCheck;
};
