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
| loop_bfield | 64 | 11 | yes | 1.05 | 1.6e-07 | 2,521 | 2,218..46,386 | 39.4 | yes |
| plate_potential | 1,024 | 12 | yes | 25620.70 | 2.6e+04 | 35,394 | 33,898..655,666 | 34.6 | yes |
| plate_potential | 65,536 | 12 | yes | 0.30 | 3.7e-09 | 2,245,779 | 2,162,794..41,943,346 | 34.3 | yes |
| plate_potential | 1,048,576 | 12 | yes | 0.30 | 1.9e-09 | 35,927,834 | 34,603,114..671,088,946 | 34.3 | yes |
| fermi_half | 64 | 9 | yes | 0.13 | 3.9e-08 | 911 | 679..36,143 | 14.2 | yes |
| decay_wide | 128 | 5 | yes | 0.00 | 1.1e-08 | 911 | 738..41,258 | 7.1 | yes |
| decay_wide | 65,536 | 7 | yes | 0.00 | 0 | 520,262 | 458,852..26,214,700 | 7.9 | yes |
| rf_oscillatory | 512 | 6 | yes | 0.37 | 1.9e-09 | 3,609 | 3,171..184,619 | 7.0 | yes |
| rf_oscillatory | 65,536 | 9 | yes | 0.37 | 0 | 653,529 | 589,926..31,457,582 | 10.0 | yes |
| cube_large | 512 | 12 | yes | 1.00 | 1.9e-09 | 6,420 | 6,248..246,064 | 12.5 | yes |
| cube_large | 2,097,152 | 12 | yes | 0.00 | 1.9e-09 | 25,166,100 | 25,165,928..1,006,633,264 | 12.0 | yes |

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.003 | 1.26 | 0.30 | 0.044 | 0.003 | 0.003 | 0.00 | 86.3x slower |
| sin | 16,384 | 0.657 | 151.54 | 0.97 | 0.155 | 0.097 | 0.026 | 0.00 | 10.0x slower |
| poly_box | 64 | 0.009 | 1.29 | 0.28 | 0.295 | 0.006 | 0.005 | 0.33 | 47.0x slower |
| gaussian_2d | 1,024 | 0.127 | 1.94 | 0.43 | 0.038 | 0.011 | 0.007 | 0.48 | 39.1x slower |
| gaussian_2d | 65,536 | 7.918 | 15.20 | 8.48 | 1.424 | 0.700 | 0.114 | 0.48 | 12.1x slower |
| gaussian_2d | 1,048,576 | 126.786 | 160.52 | 127.34 | 22.816 | 18.258 | 7.581 | 0.52 | 7.0x slower |
| charge_sphere | 4,096 | 0.663 | 2.17 | 1.01 | 0.161 | 0.105 | 0.050 | 0.76 | 9.6x slower |
| charge_sphere | 262,144 | 42.328 | 46.44 | 42.84 | 11.395 | 9.297 | 4.272 | 0.24 | 4.6x slower |
| circulation | 32 | 0.002 | 1.21 | 0.27 | 0.018 | 0.003 | 0.003 | 0.63 | 88.9x slower |
| flux_sphere | 1,024 | 0.250 | 1.94 | 0.55 | 0.147 | 0.109 | 0.060 | 0.44 | 5.1x slower |
| dipole_power | 32 | 0.008 | 1.08 | 0.29 | 0.031 | 0.006 | 0.005 | 1.38 | 50.9x slower |
| loop_bfield | 64 | 0.025 | 1.35 | 0.29 | 0.039 | 0.008 | 0.007 | 1.05 | 35.7x slower |
| plate_potential | 1,024 | 0.354 | 2.19 | 0.64 | 0.037 | 0.009 | 0.008 | 25620.70 | 70.9x slower |
| plate_potential | 65,536 | 22.458 | 28.40 | 22.97 | 0.899 | 0.457 | 0.106 | 0.30 | 50.2x slower |
| plate_potential | 1,048,576 | 359.278 | 391.83 | 359.88 | 42.808 | 26.928 | 12.782 | 0.30 | 13.4x slower |
| fermi_half | 64 | 0.009 | 1.56 | 0.28 | 0.043 | 0.007 | 0.007 | 0.87 | 38.3x slower |
| decay_wide | 128 | 0.009 | 2.06 | 0.29 | 0.019 | 0.004 | 0.003 | 1.00 | 68.1x slower |
| decay_wide | 65,536 | 5.203 | 26.26 | 5.54 | 0.476 | 0.305 | 0.083 | 0.00 | 18.2x slower |
| rf_oscillatory | 512 | 0.036 | 6.14 | 0.31 | 0.032 | 0.009 | 0.006 | 0.37 | 34.7x slower |
| rf_oscillatory | 65,536 | 6.535 | 28.63 | 6.99 | 0.824 | 0.495 | 0.107 | 0.63 | 14.1x slower |
| cube_large | 512 | 0.064 | 1.57 | 0.34 | 0.028 | 0.005 | 0.005 | 0.00 | 64.6x slower |
| cube_large | 2,097,152 | 251.661 | 259.79 | 252.20 | 57.315 | 31.600 | 13.710 | 0.00 | 8.0x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 109,770 | 0 | 0 | 148.0 |
| div, 2,000 points | 2,000 | 1 | 68,502 | 0 | 0 | 37.1 |
| curl, 2,000 points | 6,000 | 3 | 111,305 | 0 | 0 | 143.8 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 84,417 | 0 | 0 | 96.6 |
| split across runs, 20,000 points | 20,000 | 2 | 660,652 | 0 | 0 | 461.7 |
| tensor grid, 8,000 points | 24,000 | 3 | 436,995 | 0 | 0 | 232.6 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 14.0 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 7.2 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 13.1 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 6.9 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 7.2 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.097 | 14.0 |

Long mixed run: 65,536 points, 19 instructions, 2,206,120 cycles (33.7 per point), bounds 2,162,801..83,886,393, matches the model, in bounds.

| divide run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 165,468 | 147,559..9,175,343 | 10.1 | yes | 1.655 | 2.06 |
| lorentz_large | 16,384 | yes | 117,556 | 98,403..5,243,179 | 7.2 | yes | 1.176 | 1.54 |
| two_divides | 16,384 | yes | 172,385 | 147,559..8,519,983 | 10.5 | yes | 1.724 | 2.08 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
