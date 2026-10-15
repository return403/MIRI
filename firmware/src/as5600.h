#pragma once

#include <stdint.h>
#include <Wire.h>

enum As5600Status {
    AS5600_OK,
    AS5600_NO_DEVICE,
    AS5600_NO_MAGNET,
    AS5600_MAGNET_TOO_STRONG,
    AS5600_MAGNET_TOO_WEAK
};

class AS5600 {
    public:
        AS5600(TwoWire &bus, uint8_t sdaPin, uint8_t sclPin, uint8_t address = 0x36);

        bool begin();
        bool readAngle(uint16_t &angle);
        void update();

        As5600Status getStatus() const;
        uint16_t getAngle() const;
        float getDegrees() const;

    private:
        bool readRegisters(uint8_t reg, uint8_t *buffer, uint8_t length);

        TwoWire &bus;
        uint8_t sdaPin;
        uint8_t sclPin;
        uint8_t address;

        As5600Status status;
        uint16_t angle;
};
