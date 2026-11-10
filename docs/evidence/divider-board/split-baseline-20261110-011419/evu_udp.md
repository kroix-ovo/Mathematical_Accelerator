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
| sin | 16 | 0.003 | 1.34 | 0.32 | 0.048 | 0.004 | 0.003 | 0.00 | 89.6x slower |
| sin | 16,384 | 0.657 | 161.27 | 0.99 | 0.163 | 0.087 | 0.026 | 0.00 | 11.4x slower |
| poly_box | 64 | 0.009 | 1.23 | 0.32 | 0.297 | 0.006 | 0.006 | 0.33 | 51.4x slower |
| gaussian_2d | 1,024 | 0.127 | 1.67 | 0.43 | 0.039 | 0.013 | 0.008 | 0.48 | 32.2x slower |
| gaussian_2d | 65,536 | 7.918 | 15.30 | 8.42 | 1.501 | 0.905 | 0.106 | 0.48 | 9.3x slower |
| gaussian_2d | 1,048,576 | 126.786 | 158.36 | 127.30 | 26.908 | 18.717 | 7.420 | 0.52 | 6.8x slower |
| charge_sphere | 4,096 | 0.663 | 2.06 | 0.98 | 0.135 | 0.104 | 0.078 | 0.76 | 9.4x slower |
| charge_sphere | 262,144 | 42.328 | 46.82 | 42.88 | 11.570 | 9.146 | 4.170 | 0.24 | 4.7x slower |
| circulation | 32 | 0.002 | 1.66 | 0.27 | 0.018 | 0.003 | 0.003 | 0.63 | 86.5x slower |
| flux_sphere | 1,024 | 0.250 | 2.08 | 0.61 | 0.139 | 0.112 | 0.062 | 0.44 | 5.4x slower |
| dipole_power | 32 | 0.015 | 1.23 | 0.32 | 0.024 | 0.006 | 0.005 | 1.38 | 56.8x slower |
| loop_bfield | 64 | 0.027 | 1.29 | 0.30 | 0.032 | 0.008 | 0.007 | 1.05 | 38.9x slower |
| plate_potential | 1,024 | 0.351 | 2.27 | 0.69 | 0.061 | 0.009 | 0.008 | 25620.70 | 74.2x slower |
| plate_potential | 65,536 | 22.285 | 28.05 | 22.76 | 0.873 | 0.534 | 0.080 | 0.30 | 42.6x slower |
| plate_potential | 1,048,576 | 356.519 | 386.70 | 357.04 | 22.764 | 18.220 | 7.963 | 0.30 | 19.6x slower |
| fermi_half | 64 | 0.024 | 1.54 | 0.29 | 0.078 | 0.007 | 0.007 | 0.87 | 39.1x slower |
| decay_wide | 128 | 0.009 | 1.93 | 0.30 | 0.019 | 0.004 | 0.003 | 1.00 | 72.2x slower |
| decay_wide | 65,536 | 5.203 | 25.38 | 5.69 | 0.488 | 0.305 | 0.080 | 0.00 | 18.6x slower |
| rf_oscillatory | 512 | 0.036 | 5.64 | 0.38 | 0.029 | 0.013 | 0.006 | 0.37 | 28.2x slower |
| rf_oscillatory | 65,536 | 6.535 | 27.71 | 7.02 | 0.949 | 0.736 | 0.104 | 0.63 | 9.5x slower |
| cube_large | 512 | 0.064 | 1.48 | 0.35 | 0.034 | 0.005 | 0.005 | 0.00 | 66.1x slower |
| cube_large | 2,097,152 | 251.661 | 260.63 | 252.26 | 37.393 | 20.923 | 10.598 | 0.00 | 12.1x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 271,842 | 0 | 0 | 141.2 |
| div, 2,000 points | 2,000 | 1 | 68,307 | 0 | 0 | 37.7 |
| curl, 2,000 points | 6,000 | 3 | 202,422 | 0 | 0 | 139.1 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 134,583 | 0 | 0 | 95.4 |
| split across runs, 20,000 points | 20,000 | 2 | 660,652 | 0 | 0 | 459.9 |
| tensor grid, 8,000 points | 24,000 | 3 | 1,083,029 | 0 | 0 | 228.8 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 13.0 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 6.4 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 12.7 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 6.9 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 7.1 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.102 | 13.8 |

Long mixed run: 65,536 points, 19 instructions, 2,317,646 cycles (35.4 per point), bounds 2,162,801..83,886,393, matches the model, in bounds.

| divide run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 540,987 | 540,775..9,175,343 | 33.0 | yes | 5.410 | 5.91 |
| lorentz_large | 16,384 | yes | 540,874 | 540,771..5,243,179 | 33.0 | yes | 5.409 | 5.87 |
| two_divides | 16,384 | yes | 1,081,552 | 1,081,447..8,519,983 | 66.0 | yes | 10.816 | 11.39 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
