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
| loop_bfield | 64 | 11 | yes | 1.05 | 1.6e-07 | 4,513 | 4,330..46,386 | 70.5 | yes |
| plate_potential | 1,024 | 12 | yes | 25620.70 | 2.6e+04 | 68,434 | 67,690..655,666 | 66.8 | yes |
| plate_potential | 65,536 | 12 | yes | 0.30 | 3.7e-09 | 4,361,809 | 4,325,482..41,943,346 | 66.6 | yes |
| plate_potential | 1,048,576 | 12 | yes | 0.30 | 1.9e-09 | 69,784,630 | 69,206,122..671,088,946 | 66.6 | yes |
| fermi_half | 64 | 9 | yes | 0.13 | 3.9e-08 | 2,427 | 2,215..36,143 | 37.9 | yes |
| decay_wide | 128 | 5 | yes | 0.00 | 1.1e-08 | 911 | 738..41,258 | 7.1 | yes |
| decay_wide | 65,536 | 7 | yes | 0.00 | 0 | 520,262 | 458,852..26,214,700 | 7.9 | yes |
| rf_oscillatory | 512 | 6 | yes | 0.37 | 1.9e-09 | 3,609 | 3,171..184,619 | 7.0 | yes |
| rf_oscillatory | 65,536 | 9 | yes | 0.37 | 0 | 653,529 | 589,926..31,457,582 | 10.0 | yes |
| cube_large | 512 | 12 | yes | 1.00 | 1.9e-09 | 6,420 | 6,248..246,064 | 12.5 | yes |
| cube_large | 2,097,152 | 12 | yes | 0.00 | 1.9e-09 | 25,166,100 | 25,165,928..1,006,633,264 | 12.0 | yes |

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.003 | 1.23 | 0.32 | 0.045 | 0.003 | 0.006 | 0.00 | 95.5x slower |
| sin | 16,384 | 0.657 | 156.17 | 0.97 | 0.166 | 0.086 | 0.025 | 0.00 | 11.3x slower |
| poly_box | 64 | 0.009 | 0.93 | 0.31 | 2.057 | 0.010 | 0.005 | 0.33 | 30.3x slower |
| gaussian_2d | 1,024 | 0.127 | 2.13 | 0.54 | 0.042 | 0.012 | 0.007 | 0.48 | 46.5x slower |
| gaussian_2d | 65,536 | 7.918 | 14.55 | 8.43 | 1.076 | 0.662 | 0.151 | 0.48 | 12.7x slower |
| gaussian_2d | 1,048,576 | 126.786 | 147.97 | 127.45 | 27.325 | 21.553 | 9.895 | 0.52 | 5.9x slower |
| charge_sphere | 4,096 | 0.663 | 2.44 | 1.13 | 0.224 | 0.125 | 0.060 | 0.76 | 9.1x slower |
| charge_sphere | 262,144 | 42.328 | 46.98 | 42.94 | 12.239 | 10.452 | 5.194 | 0.24 | 4.1x slower |
| circulation | 32 | 0.002 | 1.97 | 0.33 | 0.026 | 0.004 | 0.004 | 0.63 | 87.9x slower |
| flux_sphere | 1,024 | 0.250 | 8.33 | 0.68 | 0.189 | 0.134 | 0.071 | 0.44 | 5.1x slower |
| dipole_power | 32 | 0.015 | 1.96 | 0.36 | 0.044 | 0.007 | 0.006 | 1.38 | 54.1x slower |
| loop_bfield | 64 | 0.045 | 1.98 | 0.44 | 0.045 | 0.009 | 0.014 | 1.05 | 48.8x slower |
| plate_potential | 1,024 | 0.684 | 2.46 | 1.13 | 0.048 | 0.012 | 0.015 | 25620.70 | 96.8x slower |
| plate_potential | 65,536 | 43.618 | 53.48 | 44.32 | 0.840 | 0.558 | 0.104 | 0.30 | 79.4x slower |
| plate_potential | 1,048,576 | 697.846 | 738.36 | 698.56 | 39.977 | 29.571 | 11.567 | 0.30 | 23.6x slower |
| fermi_half | 64 | 0.024 | 2.49 | 0.44 | 0.075 | 0.014 | 0.015 | 0.87 | 31.3x slower |
| decay_wide | 128 | 0.009 | 2.43 | 0.39 | 0.042 | 0.005 | 0.004 | 1.00 | 75.7x slower |
| decay_wide | 65,536 | 5.203 | 28.96 | 5.68 | 0.823 | 0.311 | 0.124 | 0.00 | 18.3x slower |
| rf_oscillatory | 512 | 0.036 | 6.72 | 0.45 | 0.030 | 0.009 | 0.006 | 0.37 | 50.1x slower |
| rf_oscillatory | 65,536 | 6.535 | 38.19 | 7.21 | 0.827 | 0.500 | 0.123 | 0.63 | 14.4x slower |
| cube_large | 512 | 0.064 | 1.48 | 0.36 | 0.029 | 0.006 | 0.006 | 0.00 | 59.8x slower |
| cube_large | 2,097,152 | 251.661 | 263.63 | 252.25 | 32.781 | 20.526 | 10.852 | 0.00 | 12.3x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 271,842 | 0 | 0 | 151.0 |
| div, 2,000 points | 2,000 | 1 | 132,253 | 0 | 0 | 41.3 |
| curl, 2,000 points | 6,000 | 3 | 266,316 | 0 | 0 | 147.5 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 198,414 | 0 | 0 | 96.5 |
| split across runs, 20,000 points | 20,000 | 2 | 660,652 | 0 | 0 | 535.3 |
| tensor grid, 8,000 points | 24,000 | 3 | 1,083,029 | 0 | 0 | 270.8 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 14.4 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 15.6 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 15.5 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 8.7 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 9.1 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.102 | 18.4 |

Long mixed run: 65,536 points, 19 instructions, 4,385,408 cycles (66.9 per point), bounds 4,325,489..83,886,393, matches the model, in bounds.

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
