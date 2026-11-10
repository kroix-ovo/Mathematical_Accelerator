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
| sin | 16 | 0.003 | 3.82 | 1.47 | 0.047 | 0.003 | 0.003 | 0.00 | 433.4x slower |
| sin | 16,384 | 0.657 | 162.36 | 1.02 | 0.156 | 0.092 | 0.027 | 0.00 | 11.1x slower |
| poly_box | 64 | 0.009 | 0.95 | 0.29 | 0.043 | 0.006 | 0.006 | 0.33 | 47.3x slower |
| gaussian_2d | 1,024 | 0.127 | 1.67 | 0.43 | 0.046 | 0.011 | 0.012 | 0.48 | 37.2x slower |
| gaussian_2d | 65,536 | 7.918 | 13.81 | 8.46 | 1.056 | 0.782 | 0.109 | 0.48 | 10.8x slower |
| gaussian_2d | 1,048,576 | 126.786 | 145.64 | 127.30 | 22.683 | 18.072 | 7.019 | 0.52 | 7.0x slower |
| charge_sphere | 4,096 | 0.663 | 2.03 | 0.95 | 0.164 | 0.096 | 0.046 | 0.76 | 9.8x slower |
| charge_sphere | 262,144 | 42.328 | 46.28 | 42.87 | 11.812 | 9.193 | 4.475 | 0.24 | 4.7x slower |
| circulation | 32 | 0.002 | 1.10 | 0.27 | 0.021 | 0.003 | 0.003 | 0.63 | 81.9x slower |
| flux_sphere | 1,024 | 0.250 | 1.94 | 0.57 | 0.157 | 0.118 | 0.065 | 0.44 | 4.8x slower |
| dipole_power | 32 | 0.007 | 1.28 | 0.41 | 0.033 | 0.006 | 0.006 | 1.38 | 72.1x slower |
| loop_bfield | 64 | 0.025 | 1.42 | 0.33 | 0.040 | 0.008 | 0.008 | 1.05 | 39.7x slower |
| plate_potential | 1,024 | 0.351 | 1.84 | 0.64 | 0.038 | 0.009 | 0.008 | 25620.70 | 68.3x slower |
| plate_potential | 65,536 | 22.285 | 28.73 | 22.78 | 1.013 | 0.597 | 0.100 | 0.30 | 38.2x slower |
| plate_potential | 1,048,576 | 356.519 | 386.43 | 356.99 | 21.879 | 15.434 | 7.188 | 0.30 | 23.1x slower |
| fermi_half | 64 | 0.009 | 1.82 | 0.27 | 0.037 | 0.007 | 0.008 | 0.87 | 36.2x slower |
| decay_wide | 128 | 0.009 | 2.04 | 0.27 | 0.017 | 0.004 | 0.003 | 1.00 | 69.7x slower |
| decay_wide | 65,536 | 5.203 | 24.18 | 5.66 | 0.436 | 0.315 | 0.078 | 0.00 | 18.0x slower |
| rf_oscillatory | 512 | 0.036 | 5.83 | 0.29 | 0.029 | 0.009 | 0.006 | 0.37 | 32.5x slower |
| rf_oscillatory | 65,536 | 6.535 | 25.21 | 6.91 | 0.579 | 0.402 | 0.099 | 0.63 | 17.2x slower |
| cube_large | 512 | 0.064 | 1.23 | 0.34 | 0.023 | 0.005 | 0.005 | 0.00 | 68.1x slower |
| cube_large | 2,097,152 | 251.661 | 260.11 | 252.19 | 35.646 | 22.339 | 9.794 | 0.00 | 11.3x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 109,992 | 0 | 0 | 134.2 |
| div, 2,000 points | 2,000 | 1 | 68,266 | 0 | 0 | 35.0 |
| curl, 2,000 points | 6,000 | 3 | 111,363 | 0 | 0 | 138.0 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 82,128 | 0 | 0 | 88.2 |
| split across runs, 20,000 points | 20,000 | 2 | 660,652 | 0 | 0 | 449.5 |
| tensor grid, 8,000 points | 24,000 | 3 | 437,317 | 0 | 0 | 235.1 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 13.0 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 6.3 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 13.4 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 8.0 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 7.9 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.096 | 12.5 |

Long mixed run: 65,536 points, 19 instructions, 2,202,172 cycles (33.6 per point), bounds 2,162,801..83,886,393, matches the model, in bounds.

| divide run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 165,426 | 147,559..9,175,343 | 10.1 | yes | 1.654 | 2.02 |
| lorentz_large | 16,384 | yes | 117,641 | 98,403..5,243,179 | 7.2 | yes | 1.176 | 1.50 |
| two_divides | 16,384 | yes | 172,892 | 147,559..8,519,983 | 10.6 | yes | 1.729 | 2.06 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
