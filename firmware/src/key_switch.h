#pragma once

class KeySwitch {
    public:
        KeySwitch(int pin);

        void begin();
        void update();
        bool isPressed() const;

    private:
        int pin;
        bool rawState;
        bool stableState;
        unsigned long lastChange;
};
