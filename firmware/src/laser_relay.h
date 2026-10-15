#pragma once

#include <stdint.h>

class LaserRelay {
    public:
        LaserRelay(uint8_t pin, bool activeHigh);

        void begin();
        void on();
        void off();
        bool isOn() const;

    private:
        void write(bool state);

        uint8_t pin;
        bool activeHigh;
        bool state;
};
