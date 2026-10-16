#include "stepper_tmc2209.h"
#include <Arduino.h>

static const float FULL_STEPS_PER_REV = 200.0f;
#if defined(ARDUINO_ARCH_ESP32)
static const float MAX_STEP_RATE = 10000.0f;    // vorsichtige Grenze fuer AccelStepper auf dem ESP32
#else
static const float MAX_STEP_RATE = 4000.0f;     // Grenze von AccelStepper auf dem 16-MHz-AVR
#endif
// Grenzen des Motors selbst, in Vollschritten (unabhaengig von Uebersetzung und Mikroschritten).
// vmax/accel sind Achsgrad und werden mit der Uebersetzung multipliziert, ohne diese Grenze
// ergaebe eine grosse Uebersetzung einen Anlauf ohne Rampe und der Motor bliebe stehen.
static const float MOTOR_MAX_FULLSTEPS_PER_SEC = 200.0f;     // 1 U/s
static const float MOTOR_MAX_FULLSTEPS_PER_SEC2 = 400.0f;    // 2 U/s^2, Rampe bis 1 U/s in 0,5 s
static const uint16_t MAX_CURRENT_MA = 2000;
static const unsigned long STATUS_INTERVAL_MS = 500;

static bool validMicrosteps(uint16_t ms) {
    return ms >= 1 && ms <= 256 && (ms & (ms - 1)) == 0;
}

StepperTMC2209::StepperTMC2209(const StepperConfig &cfg)
    : config(cfg),
#if defined(ARDUINO_ARCH_ESP32)
      driver(cfg.uart, cfg.rSense, cfg.driverAddress),
#else
      driver(cfg.uartRxPin, cfg.uartTxPin, cfg.rSense, cfg.driverAddress),
#endif
      motor(AccelStepper::DRIVER, cfg.stepPin, cfg.dirPin) {
    status = STEPPER_NO_COMMUNICATION;
    enabled = false;
    dirInverted = false;
    lastStatusCheck = 0;
}

bool StepperTMC2209::begin() {
    if (config.stepPin == 255 || config.dirPin == 255 || config.enablePin == 255 ||
        config.uartRxPin == 255 || config.uartTxPin == 255) {
        status = STEPPER_NO_COMMUNICATION;
        return false;
    }
    if (!validMicrosteps(config.microsteps) || config.rmsCurrentMa > MAX_CURRENT_MA) {
        status = STEPPER_DRIVER_ERROR;
        return false;
    }

    motor.setEnablePin(config.enablePin);
    motor.setPinsInverted(dirInverted, false, true);   // EN ist aktiv low
    motor.disableOutputs();
    enabled = false;

#if defined(ARDUINO_ARCH_ESP32)
    // Achsen am selben UART teilen sich die Schnittstelle, jeder Port wird nur einmal gestartet
    static HardwareSerial *started[4];
    static uint8_t startedCount = 0;
    bool known = false;
    for (uint8_t i = 0; i < startedCount; i++) {
        if (started[i] == config.uart) {
            known = true;
        }
    }
    if (!known && startedCount < 4) {
        config.uart->begin(config.uartBaud, SERIAL_8N1, config.uartRxPin, config.uartTxPin);
        started[startedCount++] = config.uart;
    }
#else
    driver.beginSerial(config.uartBaud);
#endif
    configureDriver();

    applySpeedLimits();
    refreshStatus();
    return status != STEPPER_NO_COMMUNICATION;
}

// Alle Register schreiben. Der TMC2209 vergisst sie, sobald VM fehlt oder einbricht
// (z. B. 12 V erst nach dem Nano eingeschaltet), dann laeuft er mit Werkseinstellung:
// Strom vom Poti, Mikroschritte von MS1/MS2, StealthChop. refreshStatus() erkennt das am
// Reset-Bit in GSTAT und ruft diese Funktion erneut auf.
void StepperTMC2209::configureDriver() {
    driver.pdn_disable(true);
    driver.mstep_reg_select(true);
    driver.I_scale_analog(false);
    // Chopper wie Klipper/Marlin: TOFF 3, HSTRT 5, HEND 0 (Rohwerte), Blank-Zeit TBL 2.
    // Mit TBL 0 (16 Takte, Werkswert) haelt SpreadCycle Schaltspitzen fuer "Strom erreicht",
    // der Strom baut sich nie auf (Open Load auf beiden Spulen), der Motor pfeift und steht.
    driver.toff(3);
    driver.hstrt(5);
    driver.hend(0);
    driver.tbl(2);
    // StealthChop fuer ruhigen Lauf, bei jeder Geschwindigkeit (TPWMTHRS 0, kein Wechsel
    // zu SpreadCycle). Die Selbstabstimmung braucht nach dem Enable kurz Stillstand unter
    // Strom und lernt bei den ersten Fahrten nach. Bleibt der Motor beim Anfahren stehen:
    // Strom erhoehen, Beschleunigung senken oder zurueck auf SpreadCycle.
    driver.en_spreadCycle(false);
    driver.pwm_autoscale(true);
    driver.pwm_autograd(true);
    driver.TPWMTHRS(0);
    driver.rms_current(config.rmsCurrentMa);
    driver.microsteps(config.microsteps);
    driver.GSTAT(0x07);         // reset, drv_err, uv_cp quittieren
}

float StepperTMC2209::stepsPerDegree() const {
    return FULL_STEPS_PER_REV * config.microsteps * config.gearRatio / 360.0f;
}

void StepperTMC2209::applySpeedLimits() {
    float perDegree = stepsPerDegree();
    float maxSteps = min(config.maxSpeedDegPerSec * perDegree,
                         MOTOR_MAX_FULLSTEPS_PER_SEC * config.microsteps);
    if (maxSteps > MAX_STEP_RATE) {
        maxSteps = MAX_STEP_RATE;
    }
    motor.setMaxSpeed(maxSteps);
    motor.setAcceleration(min(config.accelDegPerSec2 * perDegree,
                              MOTOR_MAX_FULLSTEPS_PER_SEC2 * config.microsteps));
}

void StepperTMC2209::update(bool allowStatusRead) {
    if (enabled) {
        motor.run();
    }

    // UART nur im Stillstand, ein blockierender Lesezugriff stoert sonst das Schritt-Timing.
    if (allowStatusRead && !isMoving() && millis() - lastStatusCheck >= STATUS_INTERVAL_MS) {
        lastStatusCheck = millis();
        refreshStatus();
    }
}

void StepperTMC2209::refreshStatus() {
    // Ein UART-Zugriff fuer DRV_STATUS (blockiert kurz, so wenig wie moeglich).
    // Bits: 0 otpw, 1 ot, 2..5 Kurzschluss gegen GND/Versorgung.
    uint32_t drv = driver.DRV_STATUS();
    if (drv == 0xFFFFFFFFUL || drv == 0) {
        status = STEPPER_NO_COMMUNICATION;
        return;
    }
    uint8_t gstat = driver.GSTAT();
    if (gstat & 0x01) {
        // Treiber hat neu gestartet und seine Einstellungen verloren
        configureDriver();
        gstat = driver.GSTAT();
    }
    if (drv & 0x02) {
        status = STEPPER_OVERTEMP;
    } else if ((drv & 0x3C) || (gstat & 0x06)) {   // Kurzschluss, drv_err, uv_cp
        status = STEPPER_DRIVER_ERROR;
    } else if (drv & 0x01) {
        status = STEPPER_OVERTEMP_WARNING;
    } else {
        status = STEPPER_OK;
    }
}

void StepperTMC2209::enable() {
    motor.enableOutputs();
    enabled = true;
}

void StepperTMC2209::disable() {
    motor.disableOutputs();
    enabled = false;
}

void StepperTMC2209::moveToDegrees(float degrees) {
    motor.moveTo((long)(degrees * stepsPerDegree()));
}

void StepperTMC2209::moveByDegrees(float degrees) {
    motor.move((long)(degrees * stepsPerDegree()));
}

void StepperTMC2209::stop() {
    motor.stop();
}

void StepperTMC2209::emergencyStop() {
    disable();
    motor.setCurrentPosition(motor.currentPosition());   // Ziel verwerfen, Geschwindigkeit 0
}

bool StepperTMC2209::isMoving() {
    return motor.distanceToGo() != 0;
}

bool StepperTMC2209::isEnabled() const {
    return enabled;
}

float StepperTMC2209::getPositionDegrees() {
    return motor.currentPosition() / stepsPerDegree();
}

float StepperTMC2209::getTargetDegrees() {
    return motor.targetPosition() / stepsPerDegree();
}

void StepperTMC2209::setPositionDegrees(float degrees) {
    motor.setCurrentPosition((long)(degrees * stepsPerDegree()));
}

bool StepperTMC2209::setMicrosteps(uint16_t microsteps) {
    if (microsteps == config.microsteps) {
        return true;
    }
    if (isMoving() || !validMicrosteps(microsteps)) {
        return false;
    }
    float position = getPositionDegrees();
    config.microsteps = microsteps;
    driver.microsteps(microsteps);
    motor.setCurrentPosition((long)(position * stepsPerDegree()));
    applySpeedLimits();
    return true;
}

bool StepperTMC2209::setCurrentMa(uint16_t rmsCurrentMa) {
    if (rmsCurrentMa == config.rmsCurrentMa) {
        return true;
    }
    if (isMoving() || rmsCurrentMa > MAX_CURRENT_MA) {
        return false;
    }
    config.rmsCurrentMa = rmsCurrentMa;
    driver.rms_current(rmsCurrentMa);
    return true;
}

bool StepperTMC2209::setGearRatio(float gearRatio) {
    if (gearRatio == config.gearRatio) {
        return true;
    }
    if (isMoving() || gearRatio <= 0.0f) {
        return false;
    }
    float position = getPositionDegrees();
    config.gearRatio = gearRatio;
    motor.setCurrentPosition((long)(position * stepsPerDegree()));
    applySpeedLimits();
    return true;
}

bool StepperTMC2209::setDirectionInverted(bool inverted) {
    if (inverted == dirInverted) {
        return true;
    }
    if (isMoving()) {
        return false;
    }
    dirInverted = inverted;
    motor.setPinsInverted(dirInverted, false, true);
    return true;
}

bool StepperTMC2209::setMaxSpeed(float degPerSec) {
    if (degPerSec <= 0.0f) {
        return false;
    }
    config.maxSpeedDegPerSec = degPerSec;
    applySpeedLimits();
    return true;
}

bool StepperTMC2209::setAcceleration(float degPerSec2) {
    if (degPerSec2 <= 0.0f) {
        return false;
    }
    config.accelDegPerSec2 = degPerSec2;
    applySpeedLimits();
    return true;
}

StepperStatus StepperTMC2209::getStatus() const {
    return status;
}
