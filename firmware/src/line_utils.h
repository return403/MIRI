#pragma once

#include <Arduino.h>
#include <stdint.h>

// Kleine Helfer fuer das Zeilenprotokoll. Ersatz fuer snprintf/strtod (spart Flash).

static inline uint8_t lineChecksum(const char *text) {
    uint8_t x = 0;
    while (*text) {
        x ^= (uint8_t)*text++;
    }
    return x;
}

struct LineBuilder {
    char buf[64];
    uint8_t n;

    LineBuilder() : n(0) { buf[0] = '\0'; }

    void chr(char c) {
        if (n < sizeof(buf) - 1) {
            buf[n++] = c;
            buf[n] = '\0';
        }
    }
    void str(const char *s) {
        while (*s) {
            chr(*s++);
        }
    }
    void num(unsigned v) {
        char t[8];
        utoa(v, t, 10);
        str(t);
    }
    void flt(float v, uint8_t decimals) {
        char t[16];
        dtostrf(v, 1, decimals, t);
        str(t);
    }
};

static inline bool parseFloat(const char *s, float &out) {
    if (s == nullptr || *s == '\0') {
        return false;
    }
    bool negative = false;
    if (*s == '-') {
        negative = true;
        s++;
    } else if (*s == '+') {
        s++;
    }
    float v = 0.0f;
    bool digits = false;
    while (*s >= '0' && *s <= '9') {
        v = v * 10.0f + (*s - '0');
        s++;
        digits = true;
    }
    if (*s == '.') {
        s++;
        float scale = 0.1f;
        while (*s >= '0' && *s <= '9') {
            v += (*s - '0') * scale;
            scale *= 0.1f;
            s++;
            digits = true;
        }
    }
    if (!digits || *s != '\0') {
        return false;
    }
    out = negative ? -v : v;
    return true;
}

static inline int hexValue(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    return -1;
}
