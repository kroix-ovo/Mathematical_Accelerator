#include "codec.h"
#include <string.h>

/* Generated at build time from the same polynomial as Python and Rust. */
#include "crc_table.h"

uint16_t ma_crc16(const uint8_t *data, size_t n, uint16_t crc) {
    size_t i;
    for (i = 0; i < n; ++i)
        crc = (uint16_t)(((uint32_t)crc << 8) ^ ma_crc_table[(crc >> 8) ^ data[i]]);
    return crc;
}

int ma_encode(const uint8_t *data, size_t n, uint8_t *out, size_t cap, size_t *used) {
    size_t i, marker = 0, j = 1;
    uint8_t code = 1;
    if (n > SIZE_MAX - n / 254 - 1 || cap < n + n / 254 + 1) return 4;
    out[0] = 0;
    for (i = 0; i < n; ++i) {
        if (data[i] == 0) {
            out[marker] = code;
            marker = j++;
            out[marker] = 0;
            code = 1;
        } else {
            out[j++] = data[i];
            ++code;
            if (code == 255) {
                out[marker] = code;
                marker = j++;
                out[marker] = 0;
                code = 1;
            }
        }
    }
    out[marker] = code;
    *used = j;
    return 0;
}

int ma_decode(const uint8_t *data, size_t n, uint8_t *out, size_t cap, size_t *used) {
    size_t i = 0, j = 0, count;
    uint8_t code;
    while (i < n) {
        code = data[i++];
        if (code == 0) return 1;
        count = (size_t)code - 1;
        if (count > n - i) return 2;
        if (memchr(data + i, 0, count) != NULL) return 3;
        if (count > cap - j) return 4;
        if (count) memcpy(out + j, data + i, count);
        j += count;
        i += count;
        if (code != 255 && i < n) {
            if (j >= cap) return 4;
            out[j++] = 0;
        }
    }
    *used = j;
    return 0;
}
