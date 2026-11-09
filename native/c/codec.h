#ifndef MATHACCEL_CODEC_H
#define MATHACCEL_CODEC_H
#include <stddef.h>
#include <stdint.h>

/* Caller owns both buffers; output must have the capacities documented below.
   No pointers are retained. Zero length permits NULL input. No FP arithmetic. */
uint16_t ma_crc16(const uint8_t *data, size_t n, uint16_t crc);
/* Encode needs n + n/254 + 1 bytes, decode needs n bytes. Return 0 on success. */
int ma_encode(const uint8_t *data, size_t n, uint8_t *out, size_t cap, size_t *used);
/* Error codes: 1 zero code, 2 overrun, 3 zero in block, 4 output capacity. */
int ma_decode(const uint8_t *data, size_t n, uint8_t *out, size_t cap, size_t *used);
#endif
