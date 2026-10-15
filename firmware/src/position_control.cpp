#include "position_control.h"
#include <Arduino.h>
#include <math.h>

static const unsigned long SAMPLE_MS = 20;
static const float MIN_DETECT_DEG = 0.5f;       // weniger Encoderbewegung = Kopplung/Magnet defekt
static const float MIN_RATIO = 0.01f;
static const float MAX_RATIO = 10000.0f;

static float wrap180(float a) {
    while (a > 180.0f) {
        a -= 360.0f;
    }
    while (a <= -180.0f) {
        a += 360.0f;
    }
    return a;
}

PositionControl::PositionControl(AS5600 &enc, StepperTMC2209 &stp, Settings &s, uint8_t axis)
    : encoder(enc), stepper(stp), all(s), cfg(s.axis[axis]) {
    mode = MODE_IDLE;
    error = POS_ERR_NONE;
    homing = HOMING_IDLE;
    referenced = false;
    measuredValid = false;
    measured = 0.0f;
    target = 0.0f;
    retries = 0;
    lastSample = 0;
    modeSince = 0;
    sweepFull = false;
    stopIssued = false;
    sweepStart = 0.0f;
    sweepOffset = 0.0f;
    calRatio = 1.0f;
    calInverted = false;
}

bool PositionControl::applySettings() {
    if (mode != MODE_IDLE) {
        return false;
    }
    bool ok = stepper.setMicrosteps(cfg.microsteps);
    ok = stepper.setCurrentMa(cfg.currentMa) && ok;
    ok = stepper.setGearRatio(cfg.gearRatio) && ok;
    ok = stepper.setDirectionInverted(cfg.dirInverted != 0) && ok;
    ok = stepper.setMaxSpeed(cfg.maxSpeedDeg) && ok;
    ok = stepper.setAcceleration(cfg.accelDeg) && ok;
    return ok;
}

void PositionControl::update() {
    unsigned long now = millis();
    bool sampled = false;

    if (now - lastSample >= SAMPLE_MS) {
        lastSample = now;
        encoder.update();
        if (encoder.getStatus() == AS5600_OK) {
            measured = wrap180(encoder.getDegrees() - cfg.zeroOffsetDeg);
            measuredValid = true;
        } else {
            measuredValid = false;
        }
        sampled = true;
    }

    // Erzwungene Fahrt laeuft ohne Encoder, auch wenn dieser gerade nichts liefert.
    if (mode == MODE_FORCED) {
        if (!stepper.isMoving()) {
            mode = MODE_IDLE;
        }
        return;
    }

    // Ohne gueltige Messung wird nichts weiter getan, der Fehler kommt vom Encoder-Status.
    if (!measuredValid || error != POS_ERR_NONE) {
        return;
    }

    // Motor und Encoder muessen zusammenpassen (nicht waehrend der Kalibrierung).
    if (referenced && sampled &&
        fabsf(measured - stepper.getPositionDegrees()) > cfg.followErrDeg) {
        fail(POS_ERR_FOLLOWING);
        return;
    }

    switch (mode) {
        case MODE_IDLE:
            break;

        case MODE_MOVING:
            if (!stepper.isMoving()) {
                mode = MODE_SETTLING;
                modeSince = now;
            }
            break;

        case MODE_SETTLING:
            if (now - modeSince >= cfg.settleMs) {
                float err = target - measured;
                if (fabsf(err) <= cfg.posTolDeg) {
                    stepper.setPositionDegrees(measured);
                    mode = MODE_IDLE;
                } else if (retries < cfg.maxRetries) {
                    retries++;
                    stepper.moveByDegrees(err);
                    mode = MODE_MOVING;
                } else {
                    fail(POS_ERR_NOT_REACHED);
                }
            }
            break;

        case MODE_HOME_INIT:
            startSweep(sweepFull);
            break;

        case MODE_SWEEP_OUT:
        case MODE_SWEEP_OUT_SETTLE:
        case MODE_SWEEP_BACK:
        case MODE_SWEEP_BACK_SETTLE:
            updateSweep(now, sampled);
            break;

        case MODE_FORCED:
            break;
    }
}

void PositionControl::fail(PosError e) {
    error = e;
    stepper.emergencyStop();
    mode = MODE_IDLE;
    referenced = false;
    if (homing == HOMING_RUNNING) {
        homing = HOMING_FAILED;
    }
}

void PositionControl::finishHoming() {
    stepper.setPositionDegrees(measured);
    target = measured;
    retries = 0;
    referenced = true;
    homing = HOMING_DONE;
    mode = MODE_IDLE;
}

void PositionControl::startHoming(bool forceCalibration) {
    error = POS_ERR_NONE;
    homing = HOMING_RUNNING;
    referenced = false;
    sweepFull = forceCalibration || cfg.calValid == 0;
    mode = MODE_HOME_INIT;
}

HomingResult PositionControl::getHoming() const {
    return homing;
}

void PositionControl::startSweep(bool full) {
    sweepStart = measured;
    stopIssued = false;
    stepper.enable();

    if (full) {
        // Motorgrad = Encodergrad annehmen, gemessen wird die tatsaechliche Beziehung.
        stepper.setGearRatio(1.0f);
        stepper.setDirectionInverted(false);
        stepper.setPositionDegrees(0.0f);
        stepper.setMaxSpeed(cfg.calSpeedDeg);
        stepper.setAcceleration(cfg.calSpeedDeg * 4.0f);
        stepper.moveByDegrees(cfg.calMaxMotorDeg);
    } else {
        stepper.setPositionDegrees(measured);
        if (cfg.bootSweepDeg <= 0.0f) {
            finishHoming();
            return;
        }
        sweepOffset = cfg.bootSweepDeg;
        if (measured + sweepOffset > cfg.softMaxDeg) {
            sweepOffset = -sweepOffset;
        }
        stepper.moveToDegrees(measured + sweepOffset);
    }
    mode = MODE_SWEEP_OUT;
}

void PositionControl::updateSweep(unsigned long now, bool sampled) {
    switch (mode) {
        case MODE_SWEEP_OUT:
            if (sweepFull && sampled && !stopIssued &&
                fabsf(wrap180(measured - sweepStart)) >= cfg.calSweepDeg) {
                stepper.stop();
                stopIssued = true;
            }
            if (!stepper.isMoving()) {
                mode = MODE_SWEEP_OUT_SETTLE;
                modeSince = now;
            }
            break;

        case MODE_SWEEP_OUT_SETTLE:
            if (now - modeSince < cfg.settleMs) {
                break;
            }
            if (sweepFull) {
                float motorDeg = stepper.getPositionDegrees();
                float encoderDelta = wrap180(measured - sweepStart);
                if (fabsf(encoderDelta) < MIN_DETECT_DEG || motorDeg < 1.0f) {
                    fail(POS_ERR_CALIBRATION);
                    break;
                }
                float ratio = fabsf(motorDeg / encoderDelta);
                if (ratio < MIN_RATIO || ratio > MAX_RATIO) {
                    fail(POS_ERR_CALIBRATION);
                    break;
                }
                calRatio = ratio;
                calInverted = encoderDelta < 0.0f;
                stepper.moveToDegrees(0.0f);
            } else {
                float actual = wrap180(measured - sweepStart);
                float tolerance = fabsf(sweepOffset) * cfg.calTolPct / 100.0f + cfg.posTolDeg;
                if (fabsf(actual - sweepOffset) > tolerance) {
                    fail(POS_ERR_CALIBRATION);
                    break;
                }
                stepper.moveToDegrees(sweepStart);
            }
            mode = MODE_SWEEP_BACK;
            break;

        case MODE_SWEEP_BACK:
            if (!stepper.isMoving()) {
                mode = MODE_SWEEP_BACK_SETTLE;
                modeSince = now;
            }
            break;

        case MODE_SWEEP_BACK_SETTLE:
            if (now - modeSince < cfg.settleMs) {
                break;
            }
            if (sweepFull) {
                cfg.gearRatio = calRatio;
                cfg.dirInverted = calInverted ? 1 : 0;
                cfg.calValid = 1;
                stepper.setGearRatio(calRatio);
                stepper.setDirectionInverted(calInverted);
                stepper.setMaxSpeed(cfg.maxSpeedDeg);
                stepper.setAcceleration(cfg.accelDeg);
                settingsSave(all);
            }
            finishHoming();
            break;

        default:
            break;
    }
}

bool PositionControl::moveTo(float degrees) {
    if (!referenced || error != POS_ERR_NONE || homing == HOMING_RUNNING) {
        return false;
    }
    if (degrees < cfg.softMinDeg || degrees > cfg.softMaxDeg) {
        return false;
    }
    target = degrees;
    retries = 0;
    stepper.moveToDegrees(degrees);
    mode = MODE_MOVING;
    return true;
}

bool PositionControl::moveBy(float degrees) {
    return moveTo(target + degrees);
}

bool PositionControl::forceMove(float degrees, bool relative) {
    if (mode != MODE_IDLE && mode != MODE_FORCED) {
        return false;
    }
    if (mode == MODE_IDLE) {
        // nach einer abgebrochenen Kalibrierung stehen sonst noch deren Werte im Treiber
        applySettings();
    }
    float goal = relative ? stepper.getTargetDegrees() + degrees : degrees;
    referenced = false;
    homing = HOMING_IDLE;
    target = goal;
    stepper.enable();
    stepper.moveToDegrees(goal);
    mode = MODE_FORCED;
    return true;
}

void PositionControl::stop() {
    if (mode == MODE_MOVING || mode == MODE_SETTLING) {
        stepper.stop();
        target = stepper.getTargetDegrees();
        retries = cfg.maxRetries;
        mode = MODE_MOVING;
    } else if (mode == MODE_FORCED) {
        stepper.stop();
        target = stepper.getTargetDegrees();
    }
}

void PositionControl::abort() {
    stepper.emergencyStop();
    mode = MODE_IDLE;
    referenced = false;
    homing = HOMING_IDLE;
    error = POS_ERR_NONE;
}

bool PositionControl::setZero() {
    if (mode != MODE_IDLE || !measuredValid || !referenced) {
        return false;
    }
    cfg.zeroOffsetDeg = encoder.getDegrees();
    measured = 0.0f;
    target = 0.0f;
    stepper.setPositionDegrees(0.0f);
    return true;
}

bool PositionControl::isBusy() const {
    return mode != MODE_IDLE;
}

bool PositionControl::isMoving() const {
    return mode == MODE_MOVING || mode == MODE_SETTLING;
}

bool PositionControl::isForced() const {
    return mode == MODE_FORCED;
}

float PositionControl::getPosition() const {
    return measured;
}

float PositionControl::getTarget() const {
    return target;
}

PosError PositionControl::getError() const {
    return error;
}
