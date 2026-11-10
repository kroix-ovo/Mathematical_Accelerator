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
| sin | 16 | 0.003 | 1.57 | 0.27 | 0.044 | 0.003 | 0.003 | 0.00 | 79.9x slower |
| sin | 16,384 | 0.657 | 150.03 | 1.01 | 0.150 | 0.082 | 0.025 | 0.00 | 12.3x slower |
| poly_box | 64 | 0.009 | 1.07 | 0.29 | 0.055 | 0.006 | 0.005 | 0.33 | 51.4x slower |
| gaussian_2d | 1,024 | 0.127 | 1.54 | 0.40 | 0.038 | 0.012 | 0.007 | 0.48 | 32.2x slower |
| gaussian_2d | 65,536 | 7.918 | 13.69 | 8.35 | 1.150 | 0.551 | 0.102 | 0.48 | 15.1x slower |
| gaussian_2d | 1,048,576 | 126.786 | 145.99 | 127.35 | 23.253 | 16.923 | 6.827 | 0.52 | 7.5x slower |
| charge_sphere | 4,096 | 0.663 | 2.25 | 1.00 | 0.133 | 0.101 | 0.049 | 0.76 | 9.9x slower |
| charge_sphere | 262,144 | 42.328 | 46.28 | 42.86 | 11.066 | 9.212 | 4.250 | 0.24 | 4.7x slower |
| circulation | 32 | 0.002 | 1.18 | 0.29 | 0.026 | 0.003 | 0.003 | 0.63 | 90.1x slower |
| flux_sphere | 1,024 | 0.250 | 2.13 | 0.54 | 0.159 | 0.116 | 0.062 | 0.44 | 4.7x slower |
| dipole_power | 32 | 0.007 | 1.53 | 0.29 | 0.039 | 0.006 | 0.006 | 1.38 | 49.3x slower |
| loop_bfield | 64 | 0.025 | 1.92 | 0.31 | 0.047 | 0.009 | 0.008 | 1.05 | 36.6x slower |
| plate_potential | 1,024 | 0.351 | 2.12 | 0.68 | 0.040 | 0.011 | 0.009 | 25620.70 | 63.9x slower |
| plate_potential | 65,536 | 22.285 | 28.16 | 22.80 | 0.760 | 0.463 | 0.092 | 0.30 | 49.2x slower |
| plate_potential | 1,048,576 | 356.519 | 385.64 | 357.03 | 22.546 | 18.004 | 8.546 | 0.30 | 19.8x slower |
| fermi_half | 64 | 0.009 | 1.46 | 0.32 | 0.037 | 0.007 | 0.007 | 0.87 | 45.0x slower |
| decay_wide | 128 | 0.009 | 2.10 | 0.36 | 0.023 | 0.004 | 0.003 | 1.00 | 84.0x slower |
| decay_wide | 65,536 | 5.203 | 25.28 | 5.72 | 0.915 | 0.637 | 0.081 | 0.00 | 9.0x slower |
| rf_oscillatory | 512 | 0.036 | 6.64 | 0.35 | 0.034 | 0.009 | 0.006 | 0.37 | 37.6x slower |
| rf_oscillatory | 65,536 | 6.535 | 25.93 | 7.03 | 0.785 | 0.513 | 0.174 | 0.63 | 13.7x slower |
| cube_large | 512 | 0.064 | 1.46 | 0.34 | 0.026 | 0.005 | 0.005 | 0.00 | 66.3x slower |
| cube_large | 2,097,152 | 251.661 | 261.57 | 252.16 | 33.057 | 20.290 | 10.929 | 0.00 | 12.4x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 109,992 | 0 | 0 | 144.9 |
| div, 2,000 points | 2,000 | 1 | 68,266 | 0 | 0 | 41.2 |
| curl, 2,000 points | 6,000 | 3 | 111,363 | 0 | 0 | 146.3 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 82,128 | 0 | 0 | 95.1 |
| split across runs, 20,000 points | 20,000 | 2 | 660,652 | 0 | 0 | 476.5 |
| tensor grid, 8,000 points | 24,000 | 3 | 437,317 | 0 | 0 | 228.7 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 14.4 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 6.6 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 14.5 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 7.7 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 7.7 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.096 | 14.5 |

Long mixed run: 65,536 points, 19 instructions, 2,202,172 cycles (33.6 per point), bounds 2,162,801..83,886,393, matches the model, in bounds.

| divide run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 165,426 | 147,559..9,175,343 | 10.1 | yes | 1.654 | 2.05 |
| lorentz_large | 16,384 | yes | 117,641 | 98,403..5,243,179 | 7.2 | yes | 1.176 | 1.49 |
| two_divides | 16,384 | yes | 172,892 | 147,559..8,519,983 | 10.6 | yes | 1.729 | 2.04 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
