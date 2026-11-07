Board: v0.4, 100 MHz, port COM3

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
| sin | 16 | 0.003 | 7.13 | 2.91 | 0.049 | 0.003 | 0.005 | 0.00 | 856.3x slower |
| sin | 16,384 | 0.657 | 911.19 | 3.91 | 0.114 | 0.088 | 0.025 | 0.00 | 44.6x slower |
| poly_box | 64 | 0.009 | 7.20 | 2.83 | 0.049 | 0.006 | 0.009 | 0.33 | 495.8x slower |
| gaussian_2d | 1,024 | 0.127 | 8.50 | 2.87 | 0.063 | 0.012 | 0.007 | 0.48 | 241.3x slower |
| gaussian_2d | 65,536 | 7.918 | 42.78 | 10.58 | 0.960 | 0.387 | 0.109 | 0.48 | 27.4x slower |
| gaussian_2d | 1,048,576 | 126.786 | 254.01 | 129.92 | 27.623 | 18.888 | 8.994 | 0.52 | 6.9x slower |
| charge_sphere | 4,096 | 0.663 | 9.76 | 3.70 | 0.268 | 0.153 | 0.050 | 0.76 | 24.2x slower |
| charge_sphere | 262,144 | 42.328 | 63.32 | 44.69 | 11.935 | 9.717 | 4.406 | 0.24 | 4.6x slower |
| circulation | 32 | 0.002 | 7.04 | 2.73 | 0.025 | 0.005 | 0.005 | 0.63 | 607.7x slower |
| flux_sphere | 1,024 | 0.250 | 8.96 | 2.68 | 0.153 | 0.112 | 0.060 | 0.44 | 23.9x slower |
| dipole_power | 32 | 0.015 | 7.45 | 2.76 | 0.033 | 0.006 | 0.006 | 1.38 | 492.2x slower |
| loop_bfield | 64 | 0.045 | 8.52 | 2.80 | 0.049 | 0.008 | 0.014 | 1.05 | 336.9x slower |
| plate_potential | 1,024 | 0.684 | 9.39 | 3.73 | 0.041 | 0.010 | 0.008 | 25620.70 | 359.1x slower |
| plate_potential | 65,536 | 43.618 | 82.57 | 45.55 | 1.273 | 0.334 | 0.103 | 0.30 | 136.4x slower |
| plate_potential | 1,048,576 | 697.846 | 820.31 | 700.88 | 30.945 | 20.002 | 10.591 | 0.30 | 35.0x slower |
| fermi_half | 64 | 0.024 | 9.98 | 2.61 | 0.056 | 0.012 | 0.012 | 0.87 | 221.4x slower |
| decay_wide | 128 | 0.009 | 13.45 | 2.76 | 0.047 | 0.006 | 0.005 | 1.00 | 502.7x slower |
| decay_wide | 65,536 | 5.203 | 137.19 | 7.93 | 0.962 | 0.552 | 0.083 | 0.00 | 14.4x slower |
| rf_oscillatory | 512 | 0.036 | 34.04 | 2.71 | 0.046 | 0.015 | 0.009 | 0.37 | 184.4x slower |
| rf_oscillatory | 65,536 | 6.535 | 133.70 | 8.95 | 0.815 | 0.439 | 0.104 | 0.63 | 20.4x slower |
| cube_large | 512 | 0.064 | 6.89 | 2.88 | 0.031 | 0.008 | 0.005 | 0.00 | 360.3x slower |
| cube_large | 2,097,152 | 251.661 | 278.86 | 254.60 | 38.608 | 22.814 | 10.972 | 0.00 | 11.2x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 271,842 | 0 | 0 | 859.4 |
| div, 2,000 points | 2,000 | 1 | 132,253 | 0 | 0 | 228.1 |
| curl, 2,000 points | 6,000 | 3 | 266,316 | 0 | 0 | 855.1 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 198,414 | 0 | 0 | 570.1 |
| split across runs, 20,000 points | 20,000 | 2 | 660,652 | 0 | 0 | 2800.2 |
| tensor grid, 8,000 points | 24,000 | 3 | 1,083,029 | 0 | 0 | 1365.5 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 105.8 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 33.9 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 109.7 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 42.1 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 34.1 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.102 | 103.5 |

Long mixed run: 65,536 points, 19 instructions, 4,385,408 cycles (66.9 per point), bounds 4,325,489..83,886,393, matches the model, in bounds.

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
