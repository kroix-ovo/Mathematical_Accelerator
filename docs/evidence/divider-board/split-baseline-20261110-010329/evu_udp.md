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
| dipole_power | 32 | 10 | yes | 1.38 | 1.3e-08 | 1,467 | 1,160..27,184 | 45.8 | yes |
| loop_bfield | 64 | 11 | yes | 1.05 | 1.6e-07 | 2,701 | 2,218..46,386 | 42.2 | yes |
| plate_potential | 1,024 | 12 | yes | 25620.70 | 2.6e+04 | 35,129 | 33,898..655,666 | 34.3 | yes |
| plate_potential | 65,536 | 12 | yes | 0.30 | 3.7e-09 | 2,228,537 | 2,162,794..41,943,346 | 34.0 | yes |
| plate_potential | 1,048,576 | 12 | yes | 0.30 | 1.9e-09 | 35,651,897 | 34,603,114..671,088,946 | 34.0 | yes |
| fermi_half | 64 | 9 | yes | 0.13 | 3.9e-08 | 2,427 | 2,215..36,143 | 37.9 | yes |
| decay_wide | 128 | 5 | yes | 0.00 | 1.1e-08 | 911 | 738..41,258 | 7.1 | yes |
| decay_wide | 65,536 | 7 | yes | 0.00 | 0 | 520,262 | 458,852..26,214,700 | 7.9 | yes |
| rf_oscillatory | 512 | 6 | yes | 0.37 | 1.9e-09 | 3,609 | 3,171..184,619 | 7.0 | yes |
| rf_oscillatory | 65,536 | 9 | yes | 0.37 | 0 | 653,529 | 589,926..31,457,582 | 10.0 | yes |
| cube_large | 512 | 12 | yes | 1.00 | 1.9e-09 | 6,420 | 6,248..246,064 | 12.5 | yes |
| cube_large | 2,097,152 | 12 | yes | 0.00 | 1.9e-09 | 25,166,100 | 25,165,928..1,006,633,264 | 12.0 | yes |

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.003 | 4.13 | 1.33 | 0.044 | 0.003 | 0.003 | 0.00 | 379.1x slower |
| sin | 16,384 | 0.657 | 218.97 | 2.76 | 0.239 | 0.086 | 0.026 | 0.00 | 31.9x slower |
| poly_box | 64 | 0.009 | 4.70 | 1.46 | 0.075 | 0.007 | 0.005 | 0.33 | 208.8x slower |
| gaussian_2d | 1,024 | 0.127 | 5.42 | 1.70 | 0.066 | 0.012 | 0.008 | 0.48 | 140.3x slower |
| gaussian_2d | 65,536 | 7.918 | 13.54 | 8.30 | 1.619 | 0.598 | 0.112 | 0.48 | 13.9x slower |
| gaussian_2d | 1,048,576 | 126.786 | 147.16 | 127.36 | 25.055 | 17.992 | 7.681 | 0.52 | 7.1x slower |
| charge_sphere | 4,096 | 0.663 | 2.66 | 0.97 | 0.143 | 0.102 | 0.050 | 0.76 | 9.6x slower |
| charge_sphere | 262,144 | 42.328 | 45.99 | 42.82 | 11.212 | 9.144 | 4.241 | 0.24 | 4.7x slower |
| circulation | 32 | 0.002 | 1.08 | 0.27 | 0.017 | 0.003 | 0.003 | 0.63 | 84.9x slower |
| flux_sphere | 1,024 | 0.250 | 2.15 | 0.55 | 0.134 | 0.109 | 0.062 | 0.44 | 5.1x slower |
| dipole_power | 32 | 0.015 | 1.50 | 0.29 | 0.024 | 0.005 | 0.005 | 1.38 | 51.9x slower |
| loop_bfield | 64 | 0.027 | 1.65 | 0.29 | 0.056 | 0.008 | 0.007 | 1.05 | 38.0x slower |
| plate_potential | 1,024 | 0.351 | 2.18 | 0.66 | 0.040 | 0.009 | 0.009 | 25620.70 | 74.7x slower |
| plate_potential | 65,536 | 22.285 | 27.80 | 22.84 | 1.002 | 0.432 | 0.097 | 0.30 | 52.9x slower |
| plate_potential | 1,048,576 | 356.519 | 385.92 | 357.06 | 21.646 | 17.107 | 8.061 | 0.30 | 20.9x slower |
| fermi_half | 64 | 0.024 | 1.93 | 0.30 | 0.035 | 0.007 | 0.007 | 0.87 | 41.7x slower |
| decay_wide | 128 | 0.009 | 1.81 | 0.30 | 0.018 | 0.004 | 0.003 | 1.00 | 73.8x slower |
| decay_wide | 65,536 | 5.203 | 25.28 | 5.66 | 0.640 | 0.358 | 0.080 | 0.00 | 15.8x slower |
| rf_oscillatory | 512 | 0.036 | 6.14 | 0.32 | 0.036 | 0.010 | 0.006 | 0.37 | 32.6x slower |
| rf_oscillatory | 65,536 | 6.535 | 25.87 | 7.00 | 0.754 | 0.401 | 0.113 | 0.63 | 17.4x slower |
| cube_large | 512 | 0.064 | 1.42 | 0.36 | 0.028 | 0.006 | 0.005 | 0.00 | 62.1x slower |
| cube_large | 2,097,152 | 251.661 | 262.55 | 252.21 | 32.691 | 20.092 | 10.439 | 0.00 | 12.6x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 271,842 | 0 | 0 | 137.0 |
| div, 2,000 points | 2,000 | 1 | 68,307 | 0 | 0 | 36.6 |
| curl, 2,000 points | 6,000 | 3 | 202,422 | 0 | 0 | 133.2 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 134,583 | 0 | 0 | 93.1 |
| split across runs, 20,000 points | 20,000 | 2 | 660,652 | 0 | 0 | 465.6 |
| tensor grid, 8,000 points | 24,000 | 3 | 1,083,029 | 0 | 0 | 231.2 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 11.5 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 6.4 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 13.9 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 8.8 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 7.1 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.102 | 12.5 |

Long mixed run: 65,536 points, 19 instructions, 2,317,646 cycles (35.4 per point), bounds 2,162,801..83,886,393, matches the model, in bounds.

| divide run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 540,987 | 540,775..9,175,343 | 33.0 | yes | 5.410 | 5.85 |
| lorentz_large | 16,384 | yes | 540,874 | 540,771..5,243,179 | 33.0 | yes | 5.409 | 5.82 |
| two_divides | 16,384 | yes | 1,081,552 | 1,081,447..8,519,983 | 66.0 | yes | 10.816 | 11.26 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
