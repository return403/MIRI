#pragma once

// Gemeinsam fuer Master (state_machine) und Achsmodul (slave). Die Zahlen sind Teil des
// Protokolls zur GUI und duerfen nicht umnummeriert werden.

enum MachineState {
    STATE_BOOT = 0,
    STATE_HOMING = 1,
    STATE_READY = 2,
    STATE_ERROR = 3
};

enum ErrorCode {
    ERR_NONE = 0,
    ERR_GUI_LOST = 1,
    ERR_ENCODER_COMM = 2,
    ERR_ENCODER_MAGNET = 3,
    ERR_DRIVER_COMM = 4,
    ERR_DRIVER_FAULT = 5,
    ERR_POS_NOT_REACHED = 6,
    ERR_POS_FOLLOWING = 7,
    ERR_CALIBRATION = 8,
    ERR_TIMEOUT = 9,
    ERR_MODULE_LOST = 10
};
