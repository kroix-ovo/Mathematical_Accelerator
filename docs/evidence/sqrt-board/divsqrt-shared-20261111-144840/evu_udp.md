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
| dipole_power | 32 | 10 | yes | 1.38 | 1.3e-08 | 768 | 424..27,184 | 24.0 | yes |
| loop_bfield | 64 | 11 | yes | 1.05 | 1.6e-07 | 1,099 | 810..46,386 | 17.2 | yes |
| plate_potential | 1,024 | 12 | yes | 25620.70 | 2.6e+04 | 14,215 | 12,394..655,666 | 13.9 | yes |
| plate_potential | 65,536 | 12 | yes | 0.30 | 3.7e-09 | 906,661 | 786,538..41,943,346 | 13.8 | yes |
| plate_potential | 1,048,576 | 12 | yes | 0.30 | 1.9e-09 | 14,514,945 | 12,583,018..671,088,946 | 13.8 | yes |
| fermi_half | 64 | 9 | yes | 0.13 | 3.9e-08 | 882 | 679..36,143 | 13.8 | yes |
| decay_wide | 128 | 5 | yes | 0.00 | 1.1e-08 | 911 | 738..41,258 | 7.1 | yes |
| decay_wide | 65,536 | 7 | yes | 0.00 | 0 | 520,262 | 458,852..26,214,700 | 7.9 | yes |
| rf_oscillatory | 512 | 6 | yes | 0.37 | 1.9e-09 | 3,609 | 3,171..184,619 | 7.0 | yes |
| rf_oscillatory | 65,536 | 9 | yes | 0.37 | 0 | 653,529 | 589,926..31,457,582 | 10.0 | yes |
| cube_large | 512 | 12 | yes | 1.00 | 1.9e-09 | 6,420 | 6,248..246,064 | 12.5 | yes |
| cube_large | 2,097,152 | 12 | yes | 0.00 | 1.9e-09 | 25,166,100 | 25,165,928..1,006,633,264 | 12.0 | yes |

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.003 | 1.05 | 0.28 | 0.050 | 0.003 | 0.003 | 0.00 | 86.3x slower |
| sin | 16,384 | 0.657 | 165.72 | 1.00 | 0.254 | 0.090 | 0.026 | 0.00 | 11.2x slower |
| poly_box | 64 | 0.009 | 4.95 | 0.28 | 4.434 | 0.009 | 0.009 | 0.33 | 30.0x slower |
| gaussian_2d | 1,024 | 0.127 | 3.46 | 0.42 | 0.050 | 0.011 | 0.007 | 0.48 | 38.1x slower |
| gaussian_2d | 65,536 | 7.918 | 13.60 | 8.28 | 1.450 | 0.522 | 0.101 | 0.48 | 15.9x slower |
| gaussian_2d | 1,048,576 | 126.786 | 172.33 | 127.30 | 21.665 | 16.275 | 6.368 | 0.52 | 7.8x slower |
| charge_sphere | 4,096 | 0.663 | 1.99 | 0.99 | 0.160 | 0.102 | 0.048 | 0.76 | 9.8x slower |
| charge_sphere | 262,144 | 42.328 | 45.69 | 42.84 | 12.714 | 9.402 | 4.055 | 0.24 | 4.6x slower |
| circulation | 32 | 0.002 | 1.00 | 0.27 | 0.018 | 0.003 | 0.003 | 0.63 | 88.6x slower |
| flux_sphere | 1,024 | 0.250 | 1.90 | 0.53 | 0.135 | 0.114 | 0.061 | 0.44 | 4.7x slower |
| dipole_power | 32 | 0.008 | 1.43 | 0.30 | 0.045 | 0.006 | 0.005 | 1.38 | 54.2x slower |
| loop_bfield | 64 | 0.011 | 1.68 | 0.28 | 0.033 | 0.008 | 0.009 | 1.05 | 36.3x slower |
| plate_potential | 1,024 | 0.142 | 1.62 | 0.42 | 0.029 | 0.009 | 0.008 | 25620.70 | 48.8x slower |
| plate_potential | 65,536 | 9.067 | 20.13 | 9.62 | 0.921 | 0.463 | 0.107 | 0.30 | 20.8x slower |
| plate_potential | 1,048,576 | 145.149 | 177.01 | 145.66 | 20.534 | 15.043 | 7.066 | 0.30 | 9.7x slower |
| fermi_half | 64 | 0.009 | 1.63 | 0.31 | 0.030 | 0.007 | 0.007 | 0.87 | 45.4x slower |
| decay_wide | 128 | 0.009 | 2.13 | 0.29 | 0.015 | 0.004 | 0.003 | 1.00 | 71.3x slower |
| decay_wide | 65,536 | 5.203 | 25.80 | 5.58 | 0.458 | 0.366 | 0.078 | 0.00 | 15.3x slower |
| rf_oscillatory | 512 | 0.036 | 5.98 | 0.32 | 0.028 | 0.009 | 0.006 | 0.37 | 36.0x slower |
| rf_oscillatory | 65,536 | 6.535 | 25.71 | 6.91 | 0.598 | 0.456 | 0.100 | 0.63 | 15.2x slower |
| cube_large | 512 | 0.064 | 1.08 | 0.33 | 0.018 | 0.005 | 0.005 | 0.00 | 66.8x slower |
| cube_large | 2,097,152 | 251.661 | 261.45 | 252.24 | 30.913 | 19.094 | 9.213 | 0.00 | 13.2x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 109,895 | 0 | 0 | 136.5 |
| div, 2,000 points | 2,000 | 1 | 20,613 | 0 | 0 | 35.0 |
| curl, 2,000 points | 6,000 | 3 | 65,882 | 0 | 0 | 134.7 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 32,459 | 0 | 0 | 91.5 |
| split across runs, 20,000 points | 20,000 | 2 | 195,105 | 0 | 0 | 451.8 |
| tensor grid, 8,000 points | 24,000 | 3 | 436,229 | 0 | 0 | 218.8 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 13.3 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 6.1 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 14.3 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 7.3 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 7.4 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.098 | 16.8 |

Long mixed run: 65,536 points, 19 instructions, 1,351,048 cycles (20.6 per point), bounds 1,245,297..83,886,393, matches the model, in bounds.

| large run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 165,630 | 147,559..9,175,343 | 10.1 | yes | 1.656 | 2.01 |
| lorentz_large | 16,384 | yes | 115,472 | 98,403..5,243,179 | 7.0 | yes | 1.155 | 1.52 |
| two_divides | 16,384 | yes | 172,585 | 147,559..8,519,983 | 10.5 | yes | 1.726 | 2.07 |
| root_large | 16,384 | yes | 115,472 | 98,403..5,243,179 | 7.0 | yes | 1.155 | 1.52 |
| two_roots | 16,384 | yes | 134,755 | 114,788..7,209,260 | 8.2 | yes | 1.348 | 1.66 |
| root_divide | 16,384 | yes | 131,473 | 114,788..7,209,260 | 8.0 | yes | 1.315 | 1.63 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
