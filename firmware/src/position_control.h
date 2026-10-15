#pragma once

#include <stdint.h>
#include "as5600.h"
#include "stepper_tmc2209.h"
#include "settings.h"

// Alle Positionen sind Grad des AS5600, bezogen auf den gespeicherten Nullpunkt.
// Der AS5600 ist absolut, ein Bereich von +-180 Grad um den Nullpunkt ist eindeutig.
//
// Referenzfahrt (startHoming):
//   - Position kommt direkt aus dem AS5600, es muss kein Anschlag angefahren werden.
//   - Ohne gueltige Kalibrierung: der Motor dreht, bis sich der Encoder um calSweepDeg
//     bewegt hat. Daraus folgen Uebersetzung und Drehrichtung, danach faehrt er zurueck.
//   - Mit gueltiger Kalibrierung: kurze Pruefbewegung (bootSweepDeg) gegen die
//     gespeicherten Werte, Abweichung = Fehler.
//
// Regelung im Betrieb: nach jeder Fahrt wird mit dem Encoder geprueft, bei Abweichung
// folgen bis zu maxRetries Korrekturen. Zusaetzlich wird laufend Encoder gegen
// Motorposition verglichen (verlorene Schritte, Fremdeinwirkung).
//
// Erzwungene Fahrt (forceMove): reine Steuerung nach Schrittzaehler, ohne Encoder, ohne
// Schleppfehler-Pruefung und ohne Softlimits. Danach ist die Achse nicht mehr referenziert.

enum PosError {
    POS_ERR_NONE,
    POS_ERR_NOT_REACHED,
    POS_ERR_FOLLOWING,
    POS_ERR_CALIBRATION
};

enum HomingResult {
    HOMING_IDLE,
    HOMING_RUNNING,
    HOMING_DONE,
    HOMING_FAILED
};

class PositionControl {
    public:
        PositionControl(AS5600 &encoder, StepperTMC2209 &stepper, Settings &settings, uint8_t axis);

        void update();

        // Settings in den Treiber uebernehmen, false wenn gerade nicht moeglich
        bool applySettings();

        void startHoming(bool forceCalibration);
        HomingResult getHoming() const;

        bool moveTo(float degrees);
        bool moveBy(float degrees);
        bool forceMove(float degrees, bool relative);
        void stop();
        void abort();
        bool setZero();

        bool isBusy() const;
        bool isMoving() const;
        bool isForced() const;
        float getPosition() const;
        float getTarget() const;

        PosError getError() const;

    private:
        enum Mode {
            MODE_IDLE,
            MODE_MOVING,
            MODE_SETTLING,
            MODE_HOME_INIT,
            MODE_SWEEP_OUT,
            MODE_SWEEP_OUT_SETTLE,
            MODE_SWEEP_BACK,
            MODE_SWEEP_BACK_SETTLE,
            MODE_FORCED
        };

        void fail(PosError error);
        void finishHoming();
        void startSweep(bool full);
        void updateSweep(unsigned long now, bool sampled);

        AS5600 &encoder;
        StepperTMC2209 &stepper;
        Settings &all;
        AxisSettings &cfg;

        Mode mode;
        PosError error;
        HomingResult homing;

        bool referenced;
        bool measuredValid;
        float measured;
        float target;
        uint8_t retries;

        unsigned long lastSample;
        unsigned long modeSince;

        bool sweepFull;
        bool stopIssued;
        float sweepStart;
        float sweepOffset;
        float calRatio;
        bool calInverted;
};
