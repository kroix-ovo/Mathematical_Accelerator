#include "codec.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    uint8_t data[2048], wire[2060], decoded[2060];
    size_t n, used, restored, i;
    uint32_t state = 20261109;
    assert(ma_crc16((const uint8_t *)"123456789", 9, 0xffff) == 0x29b1);
    for (n = 0; n <= sizeof(data); ++n) {
        for (i = 0; i < n; ++i) {
            state = state * UINT32_C(1664525) + UINT32_C(1013904223);
            data[i] = (uint8_t)(state >> 24);
        }
        assert(ma_encode(data, n, wire, sizeof(wire), &used) == 0);
        assert(ma_decode(wire, used, decoded, sizeof(decoded), &restored) == 0);
        assert(restored == n && memcmp(data, decoded, n) == 0);
        /* Malformed arbitrary inputs stress bounds under ASan/UBSan. */
        (void)ma_decode(data, n, decoded, sizeof(decoded), &restored);
    }
    memset(data, 85, 254);
    assert(ma_encode(data, 254, wire, sizeof(wire), &used) == 0);
    assert(used == 256 && wire[0] == 255 && wire[255] == 1);
    assert(ma_decode((const uint8_t *)"\0", 1, decoded, sizeof(decoded), &used) == 1);
    assert(ma_decode((const uint8_t *)"\5\21", 2, decoded, sizeof(decoded), &used) == 2);
    assert(ma_decode((const uint8_t *)"\2\0", 2, decoded, sizeof(decoded), &used) == 3);
    assert(ma_encode(data, 254, wire, 1, &used) == 4);
    assert(ma_decode((const uint8_t *)"\2x", 2, decoded, 0, &used) == 4);
    puts("C codec known vectors, 2049 seeded lengths and bounds passed");
    return 0;
}
