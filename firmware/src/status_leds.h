#pragma once

enum LedState {
    LED_OFF,
    LED_ON,
    LED_BLINK
};

class LED {
    public:
        LED(int pin, unsigned long interval);

        void begin();
        void on();
        void off();
        void blink();
        void update();
        void setInterval(unsigned long interval);

    private:
        int pin;
        unsigned long interval;
        unsigned long previousMillis;
        LedState state;
        bool level;
};
