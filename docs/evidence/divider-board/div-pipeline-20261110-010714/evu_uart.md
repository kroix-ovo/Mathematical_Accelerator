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
| dipole_power | 32 | 10 | yes | 1.38 | 1.3e-08 | 736 | 424..27,184 | 23.0 | yes |
| loop_bfield | 64 | 11 | yes | 1.05 | 1.6e-07 | 2,514 | 2,218..46,386 | 39.3 | yes |
| plate_potential | 1,024 | 12 | yes | 25620.70 | 2.6e+04 | 35,126 | 33,898..655,666 | 34.3 | yes |
| plate_potential | 65,536 | 12 | yes | 0.30 | 3.7e-09 | 2,228,534 | 2,162,794..41,943,346 | 34.0 | yes |
| plate_potential | 1,048,576 | 12 | yes | 0.30 | 1.9e-09 | 35,651,894 | 34,603,114..671,088,946 | 34.0 | yes |
| fermi_half | 64 | 9 | yes | 0.13 | 3.9e-08 | 921 | 679..36,143 | 14.4 | yes |
| decay_wide | 128 | 5 | yes | 0.00 | 1.1e-08 | 911 | 738..41,258 | 7.1 | yes |
| decay_wide | 65,536 | 7 | yes | 0.00 | 0 | 520,262 | 458,852..26,214,700 | 7.9 | yes |
| rf_oscillatory | 512 | 6 | yes | 0.37 | 1.9e-09 | 3,609 | 3,171..184,619 | 7.0 | yes |
| rf_oscillatory | 65,536 | 9 | yes | 0.37 | 0 | 653,529 | 589,926..31,457,582 | 10.0 | yes |
| cube_large | 512 | 12 | yes | 1.00 | 1.9e-09 | 6,420 | 6,248..246,064 | 12.5 | yes |
| cube_large | 2,097,152 | 12 | yes | 0.00 | 1.9e-09 | 25,166,100 | 25,165,928..1,006,633,264 | 12.0 | yes |

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.003 | 7.30 | 2.89 | 0.045 | 0.003 | 0.003 | 0.00 | 850.4x slower |
| sin | 16,384 | 0.657 | 910.10 | 2.88 | 0.157 | 0.086 | 0.027 | 0.00 | 33.7x slower |
| poly_box | 64 | 0.009 | 6.34 | 2.81 | 0.044 | 0.006 | 0.005 | 0.33 | 492.4x slower |
| gaussian_2d | 1,024 | 0.127 | 9.04 | 2.77 | 0.035 | 0.012 | 0.007 | 0.48 | 232.6x slower |
| gaussian_2d | 65,536 | 7.918 | 41.71 | 10.71 | 1.023 | 0.561 | 0.107 | 0.48 | 19.1x slower |
| gaussian_2d | 1,048,576 | 126.786 | 245.49 | 129.93 | 27.573 | 20.483 | 7.867 | 0.52 | 6.3x slower |
| charge_sphere | 4,096 | 0.663 | 8.67 | 3.00 | 0.144 | 0.104 | 0.049 | 0.76 | 28.9x slower |
| charge_sphere | 262,144 | 42.328 | 61.92 | 44.80 | 10.920 | 9.602 | 4.349 | 0.24 | 4.7x slower |
| circulation | 32 | 0.002 | 6.76 | 2.86 | 0.038 | 0.003 | 0.003 | 0.63 | 924.0x slower |
| flux_sphere | 1,024 | 0.250 | 9.12 | 2.72 | 0.160 | 0.119 | 0.063 | 0.44 | 22.9x slower |
| dipole_power | 32 | 0.007 | 7.21 | 2.85 | 0.035 | 0.006 | 0.006 | 1.38 | 482.8x slower |
| loop_bfield | 64 | 0.025 | 8.78 | 2.79 | 0.042 | 0.008 | 0.007 | 1.05 | 339.8x slower |
| plate_potential | 1,024 | 0.351 | 8.25 | 2.88 | 0.041 | 0.010 | 0.008 | 25620.70 | 282.3x slower |
| plate_potential | 65,536 | 22.285 | 56.41 | 24.74 | 0.872 | 0.440 | 0.078 | 0.30 | 56.3x slower |
| plate_potential | 1,048,576 | 356.519 | 474.75 | 358.38 | 19.981 | 15.469 | 7.166 | 0.30 | 23.2x slower |
| fermi_half | 64 | 0.009 | 8.79 | 2.83 | 0.034 | 0.007 | 0.007 | 0.87 | 410.8x slower |
| decay_wide | 128 | 0.009 | 11.56 | 2.86 | 0.024 | 0.004 | 0.004 | 1.00 | 681.5x slower |
| decay_wide | 65,536 | 5.203 | 124.16 | 7.97 | 0.579 | 0.396 | 0.083 | 0.00 | 20.1x slower |
| rf_oscillatory | 512 | 0.036 | 34.00 | 2.76 | 0.034 | 0.009 | 0.006 | 0.37 | 313.5x slower |
| rf_oscillatory | 65,536 | 6.535 | 126.13 | 8.49 | 0.608 | 0.423 | 0.100 | 0.63 | 20.1x slower |
| cube_large | 512 | 0.064 | 6.88 | 2.78 | 0.025 | 0.005 | 0.005 | 0.00 | 545.0x slower |
| cube_large | 2,097,152 | 251.661 | 279.21 | 254.62 | 34.311 | 22.842 | 10.383 | 0.00 | 11.1x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 109,992 | 0 | 0 | 838.5 |
| div, 2,000 points | 2,000 | 1 | 68,266 | 0 | 0 | 224.3 |
| curl, 2,000 points | 6,000 | 3 | 111,363 | 0 | 0 | 837.0 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 82,128 | 0 | 0 | 557.9 |
| split across runs, 20,000 points | 20,000 | 2 | 660,652 | 0 | 0 | 2774.1 |
| tensor grid, 8,000 points | 24,000 | 3 | 437,317 | 0 | 0 | 1340.2 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 105.9 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 32.3 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 110.3 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 43.1 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 36.0 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.096 | 103.6 |

Long mixed run: 65,536 points, 19 instructions, 2,202,172 cycles (33.6 per point), bounds 2,162,801..83,886,393, matches the model, in bounds.

| divide run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 165,426 | 147,559..9,175,343 | 10.1 | yes | 1.654 | 4.72 |
| lorentz_large | 16,384 | yes | 117,641 | 98,403..5,243,179 | 7.2 | yes | 1.176 | 3.71 |
| two_divides | 16,384 | yes | 172,892 | 147,559..8,519,983 | 10.6 | yes | 1.729 | 4.80 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
