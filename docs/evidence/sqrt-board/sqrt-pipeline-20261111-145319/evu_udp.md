Board: v0.4, 100 MHz, port udp:192.168.10.2

| problem | points | instrs | match | error (ULP) | double rule | cycles | low..high | clocks/pt | in bounds |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 4 | yes | 0.00 | 3.7e-09 | 263 | 160..4,776 | 16.4 | yes |
| sin | 16,384 | 4 | yes | 0.00 | 0 | 65,749 | 65,632..4,587,816 | 4.0 | yes |
| poly_box | 64 | 11 | yes | 0.33 | 5.6e-09 | 946 | 807..28,463 | 14.8 | yes |
| gaussian_2d | 1,024 | 11 | yes | 0.52 | 3.2e-08 | 12,691 | 11,368..573,744 | 12.4 | yes |
| gaussian_2d | 65,536 | 11 | yes | 0.48 | 1.9e-09 | 791,777 | 721,000..36,700,464 | 12.1 | yes |
| gaussian_2d | 1,048,576 | 11 | yes | 0.48 | 1.9e-09 | 12,678,603 | 11,534,440..587,202,864 | 12.1 | yes |
| charge_sphere | 4,096 | 15 | yes | 0.24 | 3.7e-09 | 66,314 | 61,547..3,440,947 | 16.2 | yes |
| charge_sphere | 262,144 | 15 | yes | 0.24 | 1.9e-09 | 4,232,819 | 3,932,267..220,201,267 | 16.1 | yes |
| circulation | 32 | 2 | yes | 0.37 | 1.9e-09 | 204 | 159..2,855 | 6.4 | yes |
| flux_sphere | 1,024 | 23 | yes | 0.44 | 1.5e-08 | 24,994 | 23,667..1,433,915 | 24.4 | yes |
| dipole_power | 32 | 10 | yes | 1.38 | 1.3e-08 | 762 | 424..27,184 | 23.8 | yes |
| loop_bfield | 64 | 11 | yes | 1.05 | 1.6e-07 | 1,127 | 810..46,386 | 17.6 | yes |
| plate_potential | 1,024 | 12 | yes | 25620.70 | 2.6e+04 | 14,754 | 12,394..655,666 | 14.4 | yes |
| plate_potential | 65,536 | 12 | yes | 0.30 | 3.7e-09 | 947,846 | 786,538..41,943,346 | 14.5 | yes |
| plate_potential | 1,048,576 | 12 | yes | 0.30 | 1.9e-09 | 15,158,786 | 12,583,018..671,088,946 | 14.5 | yes |
| fermi_half | 64 | 9 | yes | 0.13 | 3.9e-08 | 911 | 679..36,143 | 14.2 | yes |
| decay_wide | 128 | 5 | yes | 0.00 | 1.1e-08 | 911 | 738..41,258 | 7.1 | yes |
| decay_wide | 65,536 | 7 | yes | 0.00 | 0 | 520,262 | 458,852..26,214,700 | 7.9 | yes |
| rf_oscillatory | 512 | 6 | yes | 0.37 | 1.9e-09 | 3,609 | 3,171..184,619 | 7.0 | yes |
| rf_oscillatory | 65,536 | 9 | yes | 0.37 | 0 | 653,529 | 589,926..31,457,582 | 10.0 | yes |
| cube_large | 512 | 12 | yes | 1.00 | 1.9e-09 | 6,420 | 6,248..246,064 | 12.5 | yes |
| cube_large | 2,097,152 | 12 | yes | 0.00 | 1.9e-09 | 25,166,100 | 25,165,928..1,006,633,264 | 12.0 | yes |

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.003 | 1.46 | 0.26 | 0.037 | 0.003 | 0.003 | 0.00 | 80.1x slower |
| sin | 16,384 | 0.657 | 139.28 | 0.96 | 0.147 | 0.087 | 0.025 | 0.00 | 11.0x slower |
| poly_box | 64 | 0.009 | 0.96 | 0.28 | 0.213 | 0.010 | 0.005 | 0.33 | 28.8x slower |
| gaussian_2d | 1,024 | 0.127 | 1.26 | 0.39 | 0.026 | 0.010 | 0.007 | 0.48 | 37.1x slower |
| gaussian_2d | 65,536 | 7.918 | 14.42 | 8.34 | 1.069 | 0.528 | 0.099 | 0.48 | 15.8x slower |
| gaussian_2d | 1,048,576 | 126.786 | 152.46 | 127.34 | 19.456 | 14.803 | 6.236 | 0.52 | 8.6x slower |
| charge_sphere | 4,096 | 0.663 | 2.98 | 1.08 | 0.221 | 0.159 | 0.081 | 0.76 | 6.8x slower |
| charge_sphere | 262,144 | 42.328 | 46.69 | 42.87 | 9.758 | 8.345 | 3.880 | 0.24 | 5.1x slower |
| circulation | 32 | 0.002 | 1.68 | 0.26 | 0.016 | 0.003 | 0.003 | 0.63 | 86.3x slower |
| flux_sphere | 1,024 | 0.250 | 1.67 | 0.51 | 0.127 | 0.106 | 0.059 | 0.44 | 4.8x slower |
| dipole_power | 32 | 0.008 | 4.54 | 0.29 | 0.024 | 0.005 | 0.005 | 1.38 | 52.0x slower |
| loop_bfield | 64 | 0.011 | 2.17 | 0.26 | 0.066 | 0.008 | 0.007 | 1.05 | 34.3x slower |
| plate_potential | 1,024 | 0.148 | 1.86 | 0.43 | 0.029 | 0.009 | 0.008 | 25620.70 | 48.5x slower |
| plate_potential | 65,536 | 9.478 | 15.26 | 9.89 | 0.650 | 0.191 | 0.077 | 0.30 | 51.7x slower |
| plate_potential | 1,048,576 | 151.588 | 192.43 | 152.15 | 25.929 | 20.333 | 7.312 | 0.30 | 7.5x slower |
| fermi_half | 64 | 0.009 | 2.27 | 0.29 | 0.034 | 0.007 | 0.007 | 0.87 | 40.9x slower |
| decay_wide | 128 | 0.009 | 1.91 | 0.28 | 0.020 | 0.004 | 0.003 | 1.00 | 69.5x slower |
| decay_wide | 65,536 | 5.203 | 30.99 | 5.53 | 0.547 | 0.314 | 0.074 | 0.00 | 17.6x slower |
| rf_oscillatory | 512 | 0.036 | 5.47 | 0.31 | 0.021 | 0.008 | 0.006 | 0.37 | 36.4x slower |
| rf_oscillatory | 65,536 | 6.535 | 25.31 | 6.86 | 0.817 | 0.467 | 0.100 | 0.63 | 14.7x slower |
| cube_large | 512 | 0.064 | 1.03 | 0.33 | 0.017 | 0.005 | 0.005 | 0.00 | 64.0x slower |
| cube_large | 2,097,152 | 251.661 | 261.16 | 252.19 | 33.384 | 23.781 | 9.250 | 0.00 | 10.6x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 109,770 | 0 | 0 | 143.2 |
| div, 2,000 points | 2,000 | 1 | 20,760 | 0 | 0 | 35.3 |
| curl, 2,000 points | 6,000 | 3 | 66,186 | 0 | 0 | 132.3 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 33,290 | 0 | 0 | 91.0 |
| split across runs, 20,000 points | 20,000 | 2 | 194,695 | 0 | 0 | 422.7 |
| tensor grid, 8,000 points | 24,000 | 3 | 436,995 | 0 | 0 | 215.5 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 12.4 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 6.5 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 13.3 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 7.5 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 6.8 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.097 | 12.1 |

Long mixed run: 65,536 points, 19 instructions, 1,360,293 cycles (20.8 per point), bounds 1,245,297..83,886,393, matches the model, in bounds.

| large run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 165,468 | 147,559..9,175,343 | 10.1 | yes | 1.655 | 2.04 |
| lorentz_large | 16,384 | yes | 117,556 | 98,403..5,243,179 | 7.2 | yes | 1.176 | 1.46 |
| two_divides | 16,384 | yes | 172,385 | 147,559..8,519,983 | 10.5 | yes | 1.724 | 2.01 |
| root_large | 16,384 | yes | 117,129 | 98,403..5,243,179 | 7.1 | yes | 1.171 | 1.47 |
| two_roots | 16,384 | yes | 133,462 | 114,788..7,209,260 | 8.1 | yes | 1.335 | 1.62 |
| root_divide | 16,384 | yes | 139,923 | 114,788..7,209,260 | 8.5 | yes | 1.399 | 1.72 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
