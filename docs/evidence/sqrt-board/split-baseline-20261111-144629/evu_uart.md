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
| sin | 16 | 0.003 | 6.99 | 2.80 | 0.034 | 0.003 | 0.003 | 0.00 | 823.2x slower |
| sin | 16,384 | 0.657 | 907.52 | 2.36 | 0.142 | 0.085 | 0.025 | 0.00 | 27.7x slower |
| poly_box | 64 | 0.009 | 5.92 | 2.82 | 0.036 | 0.006 | 0.005 | 0.33 | 495.1x slower |
| gaussian_2d | 1,024 | 0.127 | 8.24 | 2.84 | 0.035 | 0.012 | 0.007 | 0.48 | 240.5x slower |
| gaussian_2d | 65,536 | 7.918 | 42.13 | 10.81 | 1.656 | 0.770 | 0.106 | 0.48 | 14.0x slower |
| gaussian_2d | 1,048,576 | 126.786 | 246.21 | 129.40 | 22.058 | 17.027 | 7.095 | 0.52 | 7.6x slower |
| charge_sphere | 4,096 | 0.663 | 9.62 | 3.00 | 0.136 | 0.102 | 0.056 | 0.76 | 29.5x slower |
| charge_sphere | 262,144 | 42.328 | 62.53 | 44.72 | 10.863 | 9.105 | 4.330 | 0.24 | 4.9x slower |
| circulation | 32 | 0.002 | 7.00 | 2.84 | 0.022 | 0.003 | 0.003 | 0.63 | 916.8x slower |
| flux_sphere | 1,024 | 0.250 | 8.97 | 2.82 | 0.226 | 0.118 | 0.062 | 0.44 | 24.0x slower |
| dipole_power | 32 | 0.008 | 7.49 | 2.83 | 0.033 | 0.006 | 0.006 | 1.38 | 479.4x slower |
| loop_bfield | 64 | 0.025 | 8.40 | 2.84 | 0.039 | 0.008 | 0.007 | 1.05 | 364.1x slower |
| plate_potential | 1,024 | 0.354 | 8.21 | 2.86 | 0.037 | 0.010 | 0.008 | 25620.70 | 274.6x slower |
| plate_potential | 65,536 | 22.458 | 56.42 | 24.68 | 0.684 | 0.450 | 0.095 | 0.30 | 54.9x slower |
| plate_potential | 1,048,576 | 359.278 | 477.72 | 361.96 | 20.215 | 14.683 | 7.162 | 0.30 | 24.7x slower |
| fermi_half | 64 | 0.009 | 8.47 | 2.91 | 0.028 | 0.007 | 0.007 | 0.87 | 421.6x slower |
| decay_wide | 128 | 0.009 | 11.94 | 2.84 | 0.018 | 0.004 | 0.003 | 1.00 | 710.9x slower |
| decay_wide | 65,536 | 5.203 | 124.89 | 7.45 | 0.604 | 0.310 | 0.082 | 0.00 | 24.1x slower |
| rf_oscillatory | 512 | 0.036 | 33.94 | 2.75 | 0.030 | 0.009 | 0.006 | 0.37 | 305.5x slower |
| rf_oscillatory | 65,536 | 6.535 | 124.93 | 8.48 | 0.718 | 0.476 | 0.103 | 0.63 | 17.8x slower |
| cube_large | 512 | 0.064 | 6.89 | 2.79 | 0.022 | 0.005 | 0.005 | 0.00 | 536.6x slower |
| cube_large | 2,097,152 | 251.661 | 279.01 | 254.65 | 32.645 | 18.727 | 9.345 | 0.00 | 13.6x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 109,770 | 0 | 0 | 837.5 |
| div, 2,000 points | 2,000 | 1 | 68,502 | 0 | 0 | 224.0 |
| curl, 2,000 points | 6,000 | 3 | 111,305 | 0 | 0 | 839.8 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 84,417 | 0 | 0 | 558.0 |
| split across runs, 20,000 points | 20,000 | 2 | 660,652 | 0 | 0 | 2767.2 |
| tensor grid, 8,000 points | 24,000 | 3 | 436,995 | 0 | 0 | 1340.3 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 105.6 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 32.8 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 110.3 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 40.6 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 33.6 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.097 | 103.2 |

Long mixed run: 65,536 points, 19 instructions, 2,206,120 cycles (33.7 per point), bounds 2,162,801..83,886,393, matches the model, in bounds.

| large run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 165,468 | 147,559..9,175,343 | 10.1 | yes | 1.655 | 4.88 |
| lorentz_large | 16,384 | yes | 117,556 | 98,403..5,243,179 | 7.2 | yes | 1.176 | 3.68 |
| two_divides | 16,384 | yes | 172,385 | 147,559..8,519,983 | 10.5 | yes | 1.724 | 4.99 |
| root_large | 16,384 | yes | 540,874 | 540,771..5,243,179 | 33.0 | yes | 5.409 | 7.85 |
| two_roots | 16,384 | yes | 1,081,534 | 1,081,444..7,209,260 | 66.0 | yes | 10.815 | 13.83 |
| root_divide | 16,384 | yes | 557,290 | 540,772..7,209,260 | 34.0 | yes | 5.573 | 7.87 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
