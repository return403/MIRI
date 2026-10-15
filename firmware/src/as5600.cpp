#include "as5600.h"
#include <Arduino.h>

static const uint8_t REG_STATUS = 0x0B;
static const uint8_t REG_RAW_ANGLE_HIGH = 0x0C;

static const uint8_t STATUS_MH = 0x08;
static const uint8_t STATUS_ML = 0x10;
static const uint8_t STATUS_MD = 0x20;

AS5600::AS5600(TwoWire &b, uint8_t sda, uint8_t scl, uint8_t addr)
    : bus(b), sdaPin(sda), sclPin(scl), address(addr) {
    status = AS5600_NO_DEVICE;
    angle = 0;
}

bool AS5600::begin() {
#if defined(ARDUINO_ARCH_ESP32)
    bus.begin(sdaPin, sclPin);
    bus.setClock(400000);
    bus.setTimeOut(50);
#else
    bus.begin();                    // AVR: feste Pins A4/A5
    bus.setClock(400000);
#ifdef WIRE_HAS_TIMEOUT
    bus.setWireTimeout(3000, true);
#endif
#endif
    update();
    return status != AS5600_NO_DEVICE;
}

bool AS5600::readRegisters(uint8_t reg, uint8_t *buffer, uint8_t length) {
    bus.beginTransmission(address);
    bus.write(reg);
    if (bus.endTransmission(false) != 0) {
        return false;
    }
    if (bus.requestFrom(address, length) != length) {
        return false;
    }
    for (uint8_t i = 0; i < length; i++) {
        buffer[i] = bus.read();
    }
    return true;
}

bool AS5600::readAngle(uint16_t &value) {
    uint8_t data[2];
    if (!readRegisters(REG_RAW_ANGLE_HIGH, data, 2)) {
        return false;
    }
    value = ((uint16_t)(data[0] & 0x0F) << 8) | data[1];
    return true;
}

void AS5600::update() {
    uint8_t statusRegister;
    if (!readRegisters(REG_STATUS, &statusRegister, 1)) {
        status = AS5600_NO_DEVICE;
        return;
    }

    if (!(statusRegister & STATUS_MD)) {
        status = AS5600_NO_MAGNET;
    } else if (statusRegister & STATUS_MH) {
        status = AS5600_MAGNET_TOO_STRONG;
    } else if (statusRegister & STATUS_ML) {
        status = AS5600_MAGNET_TOO_WEAK;
    } else {
        status = AS5600_OK;
    }

    uint16_t value;
    if (readAngle(value)) {
        angle = value;
    } else {
        status = AS5600_NO_DEVICE;
    }
}

As5600Status AS5600::getStatus() const {
    return status;
}

uint16_t AS5600::getAngle() const {
    return angle;
}

float AS5600::getDegrees() const {
    return angle * 360.0f / 4096.0f;
}
