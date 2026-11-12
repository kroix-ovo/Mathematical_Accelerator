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
| dipole_power | 32 | 10 | yes | 1.38 | 1.3e-08 | 768 | 424..27,184 | 24.0 | yes |
| loop_bfield | 64 | 11 | yes | 1.05 | 1.6e-07 | 1,099 | 810..46,386 | 17.2 | yes |
| plate_potential | 1,024 | 12 | yes | 25620.70 | 2.6e+04 | 14,215 | 12,394..655,666 | 13.9 | yes |
| plate_potential | 65,536 | 12 | yes | 0.30 | 3.7e-09 | 906,661 | 786,538..41,943,346 | 13.8 | yes |
| plate_potential | 1,048,576 | 12 | yes | 0.30 | 1.9e-09 | 14,514,945 | 12,583,018..671,088,946 | 13.8 | yes |
| fermi_half | 64 | 9 | yes | 0.13 | 3.9e-08 | 882 | 679..36,143 | 13.8 | yes |
| decay_wide | 128 | 5 | yes | 0.00 | 1.1e-08 | 911 | 738..41,258 | 7.1 | yes |
| decay_wide | 65,536 | 7 | yes | 0.00 | 0 | 520,262 | 458,852..26,214,700 | 7.9 | yes |
| rf_oscillatory | 512 | 6 | yes | 0.37 | 1.9e-09 | 3,609 | 3,171..184,619 | 7.0 | yes |
| rf_oscillatory | 65,536 | 9 | yes | 0.37 | 0 | 653,529 | 589,926..31,457,582 | 10.0 | yes |
| cube_large | 512 | 12 | yes | 1.00 | 1.9e-09 | 6,420 | 6,248..246,064 | 12.5 | yes |
| cube_large | 2,097,152 | 12 | yes | 0.00 | 1.9e-09 | 25,166,100 | 25,165,928..1,006,633,264 | 12.0 | yes |

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.003 | 7.13 | 2.90 | 0.041 | 0.003 | 0.003 | 0.00 | 854.4x slower |
| sin | 16,384 | 0.657 | 909.60 | 2.54 | 0.167 | 0.085 | 0.025 | 0.00 | 29.7x slower |
| poly_box | 64 | 0.009 | 5.80 | 2.90 | 0.043 | 0.006 | 0.005 | 0.33 | 500.3x slower |
| gaussian_2d | 1,024 | 0.127 | 8.52 | 2.84 | 0.035 | 0.011 | 0.007 | 0.48 | 267.5x slower |
| gaussian_2d | 65,536 | 7.918 | 41.91 | 10.69 | 0.987 | 0.716 | 0.102 | 0.48 | 14.9x slower |
| gaussian_2d | 1,048,576 | 126.786 | 245.39 | 129.43 | 24.136 | 20.978 | 6.601 | 0.52 | 6.2x slower |
| charge_sphere | 4,096 | 0.663 | 8.75 | 3.00 | 0.161 | 0.103 | 0.050 | 0.76 | 29.1x slower |
| charge_sphere | 262,144 | 42.328 | 62.37 | 44.64 | 10.868 | 8.886 | 4.139 | 0.24 | 5.0x slower |
| circulation | 32 | 0.002 | 6.66 | 2.91 | 0.019 | 0.003 | 0.003 | 0.63 | 968.6x slower |
| flux_sphere | 1,024 | 0.250 | 9.20 | 2.80 | 0.140 | 0.112 | 0.061 | 0.44 | 25.0x slower |
| dipole_power | 32 | 0.008 | 6.75 | 2.87 | 0.028 | 0.005 | 0.005 | 1.38 | 522.5x slower |
| loop_bfield | 64 | 0.011 | 8.91 | 2.83 | 0.036 | 0.008 | 0.007 | 1.05 | 362.7x slower |
| plate_potential | 1,024 | 0.142 | 8.85 | 2.77 | 0.052 | 0.009 | 0.008 | 25620.70 | 294.6x slower |
| plate_potential | 65,536 | 9.067 | 44.22 | 11.74 | 0.764 | 0.205 | 0.099 | 0.30 | 57.2x slower |
| plate_potential | 1,048,576 | 145.149 | 263.91 | 147.43 | 19.854 | 15.234 | 7.271 | 0.30 | 9.7x slower |
| fermi_half | 64 | 0.009 | 8.88 | 2.89 | 0.031 | 0.007 | 0.007 | 0.87 | 418.4x slower |
| decay_wide | 128 | 0.009 | 11.82 | 2.81 | 0.020 | 0.004 | 0.003 | 1.00 | 654.4x slower |
| decay_wide | 65,536 | 5.203 | 124.26 | 7.41 | 0.576 | 0.302 | 0.080 | 0.00 | 24.6x slower |
| rf_oscillatory | 512 | 0.036 | 32.87 | 2.74 | 0.028 | 0.009 | 0.006 | 0.37 | 311.8x slower |
| rf_oscillatory | 65,536 | 6.535 | 124.96 | 8.44 | 0.750 | 0.468 | 0.104 | 0.63 | 18.0x slower |
| cube_large | 512 | 0.064 | 6.75 | 2.66 | 0.024 | 0.005 | 0.005 | 0.00 | 511.8x slower |
| cube_large | 2,097,152 | 251.661 | 278.99 | 253.99 | 31.739 | 18.249 | 9.208 | 0.00 | 13.9x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 109,895 | 0 | 0 | 837.3 |
| div, 2,000 points | 2,000 | 1 | 20,613 | 0 | 0 | 222.7 |
| curl, 2,000 points | 6,000 | 3 | 65,882 | 0 | 0 | 835.7 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 32,459 | 0 | 0 | 557.1 |
| split across runs, 20,000 points | 20,000 | 2 | 195,105 | 0 | 0 | 2766.5 |
| tensor grid, 8,000 points | 24,000 | 3 | 436,229 | 0 | 0 | 1342.7 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 106.5 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 33.6 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 110.1 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 40.9 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 34.5 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.098 | 103.0 |

Long mixed run: 65,536 points, 19 instructions, 1,351,048 cycles (20.6 per point), bounds 1,245,297..83,886,393, matches the model, in bounds.

| large run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 165,630 | 147,559..9,175,343 | 10.1 | yes | 1.656 | 4.81 |
| lorentz_large | 16,384 | yes | 115,472 | 98,403..5,243,179 | 7.0 | yes | 1.155 | 3.62 |
| two_divides | 16,384 | yes | 172,585 | 147,559..8,519,983 | 10.5 | yes | 1.726 | 4.78 |
| root_large | 16,384 | yes | 115,472 | 98,403..5,243,179 | 7.0 | yes | 1.155 | 3.70 |
| two_roots | 16,384 | yes | 134,755 | 114,788..7,209,260 | 8.2 | yes | 1.348 | 3.92 |
| root_divide | 16,384 | yes | 131,473 | 114,788..7,209,260 | 8.0 | yes | 1.315 | 3.47 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
