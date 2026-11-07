Board: v0.4, 100 MHz, port udpsim

| problem | points | instrs | match | error (ULP) | double rule | cycles | low..high | clocks/pt | in bounds |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 4 | yes | 0.00 | 3.7e-09 | 160 | 160..4,776 | 10.0 | yes |
| sin | 16,384 | 4 | yes | 0.00 | 0 | 65,632 | 65,632..4,587,816 | 4.0 | yes |
| poly_box | 64 | 11 | yes | 0.33 | 5.6e-09 | 807 | 807..28,463 | 12.6 | yes |
| gaussian_2d | 1,024 | 11 | yes | 0.52 | 3.2e-08 | 11,368 | 11,368..573,744 | 11.1 | yes |
| gaussian_2d | 65,536 | 11 | yes | 0.48 | 1.9e-09 | 721,000 | 721,000..36,700,464 | 11.0 | yes |
| gaussian_2d | 1,048,576 | 11 | yes | 0.48 | 1.9e-09 | 11,534,440 | 11,534,440..587,202,864 | 11.0 | yes |
| charge_sphere | 4,096 | 15 | yes | 0.24 | 3.7e-09 | 61,547 | 61,547..3,440,947 | 15.0 | yes |
| charge_sphere | 262,144 | 15 | yes | 0.24 | 1.9e-09 | 3,932,267 | 3,932,267..220,201,267 | 15.0 | yes |
| circulation | 32 | 2 | yes | 0.37 | 1.9e-09 | 159 | 159..2,855 | 5.0 | yes |
| flux_sphere | 1,024 | 23 | yes | 0.44 | 1.5e-08 | 23,667 | 23,667..1,433,915 | 23.1 | yes |
| dipole_power | 32 | 10 | yes | 1.38 | 1.3e-08 | 1,160 | 1,160..27,184 | 36.2 | yes |
| loop_bfield | 64 | 11 | yes | 1.05 | 1.6e-07 | 4,330 | 4,330..46,386 | 67.7 | yes |
| plate_potential | 1,024 | 12 | yes | 25620.70 | 2.6e+04 | 67,690 | 67,690..655,666 | 66.1 | yes |
| plate_potential | 65,536 | 12 | yes | 0.30 | 3.7e-09 | 4,325,482 | 4,325,482..41,943,346 | 66.0 | yes |
| plate_potential | 1,048,576 | 12 | yes | 0.30 | 1.9e-09 | 69,206,122 | 69,206,122..671,088,946 | 66.0 | yes |
| fermi_half | 64 | 9 | yes | 0.13 | 3.9e-08 | 2,215 | 2,215..36,143 | 34.6 | yes |
| decay_wide | 128 | 5 | yes | 0.00 | 1.1e-08 | 738 | 738..41,258 | 5.8 | yes |
| decay_wide | 65,536 | 7 | yes | 0.00 | 0 | 458,852 | 458,852..26,214,700 | 7.0 | yes |
| rf_oscillatory | 512 | 6 | yes | 0.37 | 1.9e-09 | 3,171 | 3,171..184,619 | 6.2 | yes |
| rf_oscillatory | 65,536 | 9 | yes | 0.37 | 0 | 589,926 | 589,926..31,457,582 | 9.0 | yes |
| cube_large | 512 | 12 | yes | 1.00 | 1.9e-09 | 6,248 | 6,248..246,064 | 12.2 | yes |
| cube_large | 2,097,152 | 12 | yes | 0.00 | 1.9e-09 | 25,165,928 | 25,165,928..1,006,633,264 | 12.0 | yes |

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.002 | 2.42 | 0.85 | 0.074 | 0.005 | 0.009 | 0.00 | 179.9x slower |
| sin | 16,384 | 0.656 | 320.49 | 47.53 | 0.188 | 0.089 | 0.027 | 0.00 | 532.2x slower |
| poly_box | 64 | 0.008 | 2.49 | 1.39 | 0.049 | 0.006 | 0.006 | 0.33 | 216.4x slower |
| gaussian_2d | 1,024 | 0.114 | 12.04 | 8.89 | 0.050 | 0.013 | 0.008 | 0.48 | 663.1x slower |
| gaussian_2d | 65,536 | 7.210 | 592.98 | 586.45 | 1.336 | 0.523 | 0.109 | 0.48 | 1120.5x slower |
| gaussian_2d | 1,048,576 | 115.344 | 9654.11 | 9491.08 | 31.592 | 22.904 | 12.256 | 0.52 | 414.4x slower |
| charge_sphere | 4,096 | 0.615 | 58.42 | 50.35 | 0.183 | 0.113 | 0.057 | 0.76 | 446.4x slower |
| charge_sphere | 262,144 | 39.323 | 3393.74 | 2771.16 | 16.416 | 12.365 | 4.783 | 0.24 | 224.1x slower |
| circulation | 32 | 0.002 | 2.96 | 0.65 | 0.022 | 0.003 | 0.003 | 0.63 | 190.8x slower |
| flux_sphere | 1,024 | 0.237 | 24.08 | 21.15 | 0.157 | 0.114 | 0.065 | 0.44 | 185.4x slower |
| dipole_power | 32 | 0.012 | 2.59 | 1.20 | 0.040 | 0.010 | 0.011 | 1.38 | 125.0x slower |
| loop_bfield | 64 | 0.043 | 5.81 | 1.87 | 0.047 | 0.009 | 0.008 | 1.05 | 212.1x slower |
| plate_potential | 1,024 | 0.677 | 16.38 | 11.21 | 0.061 | 0.012 | 0.010 | 25620.70 | 897.0x slower |
| plate_potential | 65,536 | 43.255 | 671.22 | 645.08 | 1.056 | 0.594 | 0.096 | 0.30 | 1086.5x slower |
| plate_potential | 1,048,576 | 692.061 | 10525.77 | 10428.92 | 27.148 | 18.234 | 10.343 | 0.30 | 571.9x slower |
| fermi_half | 64 | 0.022 | 3.89 | 1.69 | 0.053 | 0.007 | 0.007 | 0.87 | 231.7x slower |
| decay_wide | 128 | 0.007 | 6.02 | 1.28 | 0.022 | 0.004 | 0.004 | 1.00 | 298.6x slower |
| decay_wide | 65,536 | 4.589 | 301.62 | 267.60 | 0.732 | 0.514 | 0.114 | 0.00 | 520.4x slower |
| rf_oscillatory | 512 | 0.032 | 14.66 | 3.69 | 0.033 | 0.013 | 0.010 | 0.37 | 279.8x slower |
| rf_oscillatory | 65,536 | 5.899 | 481.59 | 440.15 | 0.808 | 0.442 | 0.101 | 0.63 | 994.9x slower |
| cube_large | 512 | 0.062 | 6.30 | 5.03 | 0.028 | 0.006 | 0.005 | 0.00 | 882.2x slower |
| cube_large | 2,097,152 | 251.659 | 15856.21 | 15355.99 | 41.897 | 27.819 | 11.343 | 0.00 | 552.0x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 264,248 | 0 | 0 | 382.6 |
| div, 2,000 points | 2,000 | 1 | 132,072 | 0 | 0 | 95.4 |
| curl, 2,000 points | 6,000 | 3 | 264,218 | 0 | 0 | 318.5 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 198,140 | 0 | 0 | 214.1 |
| split across runs, 20,000 points | 20,000 | 2 | 660,146 | 0 | 0 | 979.9 |
| tensor grid, 8,000 points | 24,000 | 3 | 1,056,248 | 0 | 0 | 880.6 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.018 | 23.0 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.005 | 11.1 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.019 | 26.3 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.006 | 13.7 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.007 | 14.3 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.036 | 32.6 |

Long mixed run: 65,536 points, 19 instructions, 4,325,489 cycles (66.0 per point), bounds 4,325,489..83,886,393, matches the model, in bounds.

Random programs, seed 1: 60 runs, 9,745 points, 86,800 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.364.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
