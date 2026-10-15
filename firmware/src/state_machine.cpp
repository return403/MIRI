#include "state_machine.h"
#include "line_utils.h"
#include <Arduino.h>

static const unsigned long HOMING_TIMEOUT_MS = 90000;
static const unsigned long WHEEL_RESYNC_MS = 500;

StateMachineController::StateMachineController(Settings &s, ModuleLink *const *m, LaserRelay &las,
                                               KeySwitch &k, EncoderWheel &w)
    : settings(s), modules(m), laser(las), key(k), wheel(w) {
    state = STATE_BOOT;
    error = ERR_NONE;
    errorAxis = 0;
    stateSince = 0;
    heartbeatSeen = false;
    lastHeartbeat = 0;
    lastModuleHeartbeat = 0;
    armed = false;
    wheelMode = 0;
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        wheelPending[i] = 0.0f;
        wheelCommanded[i] = 0.0f;
        wheelHaveCommand[i] = false;
        wheelSentAt[i] = 0;
        reportSeen[i] = false;
        reportAt[i] = 0;
        reportState[i] = STATE_BOOT;
        reportError[i] = ERR_NONE;
    }
}

bool StateMachineController::isLinked(uint8_t axis) const {
    return settings.axis[axis].linked == 1;
}

bool StateMachineController::isUsed(uint8_t axis) const {
    return settings.axis[axis].linked != 2;
}

uint8_t StateMachineController::linkMode(uint8_t axis) const {
    return settings.axis[axis].linked;
}

bool StateMachineController::axisAlive(uint8_t axis) const {
    if (isLinked(axis)) {
        return modules[axis]->alive();
    }
    return reportSeen[axis] && (millis() - reportAt[axis]) <= settings.hbTimeoutMs;
}

MachineState StateMachineController::axisState(uint8_t axis) const {
    return isLinked(axis) ? modules[axis]->state() : reportState[axis];
}

ErrorCode StateMachineController::axisError(uint8_t axis) const {
    return isLinked(axis) ? modules[axis]->error() : reportError[axis];
}

bool StateMachineController::reportAxis(uint8_t axis, MachineState s, ErrorCode e) {
    if (axis >= AXIS_COUNT || isLinked(axis) || !isUsed(axis)) {
        return false;
    }
    reportSeen[axis] = true;
    reportAt[axis] = millis();
    reportState[axis] = s;
    reportError[axis] = e;
    return true;
}

bool StateMachineController::takeExternalJog(uint8_t &axis, float &degrees) {
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        if (!isLinked(i) && isUsed(i) && wheelPending[i] != 0.0f) {
            axis = i;
            degrees = wheelPending[i];
            wheelPending[i] = 0.0f;
            return true;
        }
    }
    return false;
}

void StateMachineController::begin() {
    skipUnusedWheelAxes();
    enterBoot();
}

void StateMachineController::enterBoot() {
    state = STATE_BOOT;
    error = ERR_NONE;
    errorAxis = 0;
    stateSince = millis();
    armed = false;
    laser.off();
}

void StateMachineController::enterError(ErrorCode code, uint8_t axis) {
    laser.off();                 // zuerst, vor allem anderen
    armed = false;
    state = STATE_ERROR;
    error = code;
    errorAxis = axis;
    stateSince = millis();

    // Module am UART stromlos, die nicht ohnehin schon im Fehlerzustand sind.
    // Module an der GUI stoppt die GUI, sobald sie state = ERROR sieht.
    LineBuilder cmd;
    cmd.str("ESTOP ");
    cmd.num((unsigned)code);
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        if (isLinked(i) && modules[i]->alive() && modules[i]->state() != STATE_ERROR) {
            modules[i]->send(cmd.buf);
        }
        wheelPending[i] = 0.0f;
    }
}

bool StateMachineController::heartbeatFresh() const {
    return heartbeatSeen && (millis() - lastHeartbeat) <= settings.hbTimeoutMs;
}

void StateMachineController::onHeartbeat() {
    heartbeatSeen = true;
    lastHeartbeat = millis();
}

bool StateMachineController::allAlive() const {
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        if (isUsed(i) && !axisAlive(i)) {
            return false;
        }
    }
    return true;
}

bool StateMachineController::allReady() const {
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        if (!isUsed(i)) {
            continue;
        }
        if (!axisAlive(i) || axisState(i) != STATE_READY) {
            return false;
        }
    }
    return true;
}

// Ein verlorenes Modul oder ein Modul im Fehlerzustand.
ErrorCode StateMachineController::moduleFault(uint8_t &axis) const {
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        if (!isUsed(i)) {
            continue;
        }
        axis = i;
        if (!axisAlive(i)) {
            return ERR_MODULE_LOST;
        }
        if (axisState(i) == STATE_ERROR) {
            ErrorCode e = axisError(i);
            return e != ERR_NONE ? e : ERR_MODULE_LOST;
        }
    }
    axis = 0;
    return ERR_NONE;
}

// Die Module brauchen ein regelmaessiges Lebenszeichen, sonst gehen sie selbst in ERROR.
void StateMachineController::sendModuleHeartbeats(unsigned long now) {
    unsigned long interval = settings.hbTimeoutMs / 3;
    if (now - lastModuleHeartbeat < interval) {
        return;
    }
    lastModuleHeartbeat = now;
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        if (isLinked(i)) {
            modules[i]->send("HB");
        }
    }
}

void StateMachineController::handleWheel(unsigned long now) {
    int16_t detents = wheel.takeDelta();
    if (wheel.takeButtonPress()) {
        wheelMode = (wheelMode + 1) % (2 * AXIS_COUNT);
        skipUnusedWheelAxes();
    }

    uint8_t axis = getSelectedAxis();
    const AxisSettings &a = settings.axis[axis];
    ModuleLink *link = modules[axis];

    if (detents != 0) {
        float step = isFineMode() ? a.jogFineDeg : a.jogStepDeg;
        wheelPending[axis] += detents * step;
    }

    if (!isLinked(axis)) {
        return;     // die GUI holt den Weg per takeExternalJog() ab
    }

    // Steht das Modul wieder, vom echten Ziel neu aufsetzen.
    if (!link->busy() && now - wheelSentAt[axis] > WHEEL_RESYNC_MS) {
        wheelHaveCommand[axis] = false;
    }

    if (wheelPending[axis] == 0.0f || !link->acquire(ModuleLink::OWNER_INTERNAL)) {
        return;
    }

    float base = wheelHaveCommand[axis] ? wheelCommanded[axis] : link->target();
    float next = base + wheelPending[axis];
    LineBuilder cmd;
    cmd.str("MOVE ");
    cmd.flt(next, 2);
    if (link->send(cmd.buf)) {
        wheelCommanded[axis] = next;
        wheelHaveCommand[axis] = true;
        wheelPending[axis] = 0.0f;
        wheelSentAt[axis] = now;
    } else {
        link->release();
    }
}

void StateMachineController::applyLaser() {
    bool allowed = state == STATE_READY && armed && key.isPressed();
    if (allowed) {
        laser.on();
    } else {
        laser.off();
    }
}

void StateMachineController::update() {
    unsigned long now = millis();

    key.update();
    wheel.update();
    sendModuleHeartbeats(now);

    // Schluessel weg = Freigabe weg, auch wenn er wieder eingeschaltet wird.
    if (!key.isPressed()) {
        armed = false;
    }

    uint8_t axis = 0;

    switch (state) {
        case STATE_BOOT:
            if (allAlive() && moduleFault(axis) == ERR_NONE && heartbeatFresh()) {
                state = STATE_HOMING;
                stateSince = now;
            } else if (now - stateSince >= settings.bootTimeoutMs) {
                ErrorCode cause = moduleFault(axis);
                if (cause == ERR_NONE) {
                    cause = ERR_GUI_LOST;
                }
                enterError(cause, axis);
            }
            break;

        case STATE_HOMING: {
            ErrorCode fault = moduleFault(axis);
            if (fault == ERR_NONE && !heartbeatFresh()) {
                fault = ERR_GUI_LOST;
                axis = 0;
            }
            if (fault != ERR_NONE) {
                enterError(fault, axis);
            } else if (allReady()) {
                state = STATE_READY;
                stateSince = now;
            } else if (now - stateSince >= HOMING_TIMEOUT_MS) {
                enterError(ERR_TIMEOUT, 0);
            }
            wheel.takeDelta();
            wheel.takeButtonPress();
            break;
        }

        case STATE_READY: {
            ErrorCode fault = moduleFault(axis);
            if (fault == ERR_NONE && !heartbeatFresh()) {
                fault = ERR_GUI_LOST;
                axis = 0;
            }
            if (fault != ERR_NONE) {
                enterError(fault, axis);
            } else if (!allReady()) {
                // z. B. Kalibrierung eines Moduls laeuft, Laser bleibt aus
                state = STATE_HOMING;
                stateSince = now;
                armed = false;
            } else {
                handleWheel(now);
            }
            break;
        }

        case STATE_ERROR:
            wheel.takeDelta();
            wheel.takeButtonPress();
            break;
    }

    applyLaser();
}

MachineState StateMachineController::getState() const {
    return state;
}

ErrorCode StateMachineController::getError() const {
    return error;
}

uint8_t StateMachineController::getErrorAxis() const {
    return errorAxis;
}

bool StateMachineController::commandsAllowed() const {
    return state == STATE_READY;
}

bool StateMachineController::requestArm(bool arm) {
    if (!arm) {
        armed = false;
        return true;
    }
    if (state != STATE_READY || !key.isPressed()) {
        return false;
    }
    armed = true;
    return true;
}

bool StateMachineController::acknowledge(uint8_t code) {
    if (state != STATE_ERROR || code != (uint8_t)error) {
        return false;
    }
    // Die GUI und alle Module muessen wieder da sein.
    if (!heartbeatFresh() || !allAlive()) {
        return false;
    }

    // Module, die selbst im Fehlerzustand sind, mit ihrem eigenen Code quittieren.
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        if (isLinked(i) && modules[i]->state() == STATE_ERROR) {
            LineBuilder cmd;
            cmd.str("ACK ");
            cmd.num((unsigned)modules[i]->error());
            modules[i]->send(cmd.buf);
        }
    }
    enterBoot();
    return true;
}

// In jedem Zustand: auch eine erzwungene Fahrt im Fehlerzustand muss anhaltbar sein.
bool StateMachineController::requestStop() {
    for (uint8_t i = 0; i < AXIS_COUNT; i++) {
        if (isLinked(i)) {
            modules[i]->send("STOP");
        }
        wheelPending[i] = 0.0f;
        wheelHaveCommand[i] = false;
    }
    return true;
}

bool StateMachineController::applySettings() {
    wheel.setStepsPerDetent(settings.wheelStepsPerDetent);
    skipUnusedWheelAxes();
    return true;
}

// Das Handrad waehlt nur benutzte Achsen (settingsValid garantiert mindestens eine).
void StateMachineController::skipUnusedWheelAxes() {
    for (uint8_t n = 0; n < 2 * AXIS_COUNT && !isUsed(wheelMode / 2); n++) {
        wheelMode = (wheelMode + 2) % (2 * AXIS_COUNT);
    }
}

bool StateMachineController::isArmed() const {
    return armed;
}

bool StateMachineController::isLaserOn() const {
    return laser.isOn();
}

bool StateMachineController::isKeyOn() const {
    return key.isPressed();
}

bool StateMachineController::isFineMode() const {
    return (wheelMode % 2) == 1;
}

uint8_t StateMachineController::getSelectedAxis() const {
    return wheelMode / 2;
}
