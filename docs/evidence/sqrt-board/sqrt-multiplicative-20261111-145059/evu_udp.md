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
| loop_bfield | 64 | 11 | yes | 1.05 | 1.6e-07 | 1,111 | 810..46,386 | 17.4 | yes |
| plate_potential | 1,024 | 12 | yes | 25620.70 | 2.6e+04 | 14,749 | 12,394..655,666 | 14.4 | yes |
| plate_potential | 65,536 | 12 | yes | 0.30 | 3.7e-09 | 942,635 | 786,538..41,943,346 | 14.4 | yes |
| plate_potential | 1,048,576 | 12 | yes | 0.30 | 1.9e-09 | 15,086,336 | 12,583,018..671,088,946 | 14.4 | yes |
| fermi_half | 64 | 9 | yes | 0.13 | 3.9e-08 | 911 | 679..36,143 | 14.2 | yes |
| decay_wide | 128 | 5 | yes | 0.00 | 1.1e-08 | 911 | 738..41,258 | 7.1 | yes |
| decay_wide | 65,536 | 7 | yes | 0.00 | 0 | 520,262 | 458,852..26,214,700 | 7.9 | yes |
| rf_oscillatory | 512 | 6 | yes | 0.37 | 1.9e-09 | 3,609 | 3,171..184,619 | 7.0 | yes |
| rf_oscillatory | 65,536 | 9 | yes | 0.37 | 0 | 653,529 | 589,926..31,457,582 | 10.0 | yes |
| cube_large | 512 | 12 | yes | 1.00 | 1.9e-09 | 6,420 | 6,248..246,064 | 12.5 | yes |
| cube_large | 2,097,152 | 12 | yes | 0.00 | 1.9e-09 | 25,166,100 | 25,165,928..1,006,633,264 | 12.0 | yes |

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.003 | 0.97 | 0.27 | 0.038 | 0.003 | 0.003 | 0.00 | 76.6x slower |
| sin | 16,384 | 0.657 | 149.11 | 0.99 | 0.129 | 0.079 | 0.024 | 0.00 | 12.4x slower |
| poly_box | 64 | 0.009 | 1.05 | 0.26 | 0.502 | 0.005 | 0.005 | 0.33 | 48.8x slower |
| gaussian_2d | 1,024 | 0.127 | 3.36 | 0.46 | 0.046 | 0.012 | 0.007 | 0.48 | 40.0x slower |
| gaussian_2d | 65,536 | 7.918 | 13.37 | 8.25 | 1.323 | 0.752 | 0.104 | 0.48 | 11.0x slower |
| gaussian_2d | 1,048,576 | 126.786 | 145.69 | 127.32 | 25.522 | 17.069 | 6.793 | 0.52 | 7.5x slower |
| charge_sphere | 4,096 | 0.663 | 1.95 | 0.98 | 0.143 | 0.103 | 0.047 | 0.76 | 9.5x slower |
| charge_sphere | 262,144 | 42.328 | 45.61 | 42.83 | 11.848 | 9.719 | 4.266 | 0.24 | 4.4x slower |
| circulation | 32 | 0.002 | 1.99 | 0.31 | 0.022 | 0.003 | 0.003 | 0.63 | 97.4x slower |
| flux_sphere | 1,024 | 0.250 | 4.61 | 0.58 | 0.177 | 0.116 | 0.063 | 0.44 | 5.0x slower |
| dipole_power | 32 | 0.008 | 1.33 | 0.31 | 0.056 | 0.006 | 0.005 | 1.38 | 54.2x slower |
| loop_bfield | 64 | 0.011 | 2.16 | 0.27 | 0.035 | 0.008 | 0.007 | 1.05 | 34.8x slower |
| plate_potential | 1,024 | 0.147 | 1.82 | 0.40 | 0.035 | 0.010 | 0.008 | 25620.70 | 40.3x slower |
| plate_potential | 65,536 | 9.426 | 15.42 | 9.86 | 0.714 | 0.502 | 0.076 | 0.30 | 19.6x slower |
| plate_potential | 1,048,576 | 150.863 | 182.97 | 151.43 | 19.203 | 14.609 | 6.787 | 0.30 | 10.4x slower |
| fermi_half | 64 | 0.009 | 1.25 | 0.29 | 0.026 | 0.007 | 0.007 | 0.87 | 40.7x slower |
| decay_wide | 128 | 0.009 | 1.83 | 0.28 | 0.014 | 0.004 | 0.003 | 1.00 | 68.3x slower |
| decay_wide | 65,536 | 5.203 | 24.84 | 5.63 | 0.544 | 0.303 | 0.083 | 0.00 | 18.6x slower |
| rf_oscillatory | 512 | 0.036 | 8.91 | 0.32 | 0.031 | 0.009 | 0.006 | 0.37 | 35.6x slower |
| rf_oscillatory | 65,536 | 6.535 | 25.18 | 6.87 | 0.649 | 0.392 | 0.096 | 0.63 | 17.5x slower |
| cube_large | 512 | 0.064 | 0.99 | 0.33 | 0.025 | 0.005 | 0.005 | 0.00 | 65.3x slower |
| cube_large | 2,097,152 | 251.661 | 261.67 | 252.17 | 30.179 | 18.864 | 8.991 | 0.00 | 13.4x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 109,770 | 0 | 0 | 144.2 |
| div, 2,000 points | 2,000 | 1 | 20,885 | 0 | 0 | 36.8 |
| curl, 2,000 points | 6,000 | 3 | 66,215 | 0 | 0 | 134.3 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 33,193 | 0 | 0 | 93.8 |
| split across runs, 20,000 points | 20,000 | 2 | 195,254 | 0 | 0 | 460.5 |
| tensor grid, 8,000 points | 24,000 | 3 | 436,995 | 0 | 0 | 228.2 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 13.0 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 6.2 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 13.2 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 6.7 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 6.9 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.097 | 14.5 |

Long mixed run: 65,536 points, 19 instructions, 1,361,272 cycles (20.8 per point), bounds 1,245,297..83,886,393, matches the model, in bounds.

| large run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 165,468 | 147,559..9,175,343 | 10.1 | yes | 1.655 | 2.02 |
| lorentz_large | 16,384 | yes | 117,556 | 98,403..5,243,179 | 7.2 | yes | 1.176 | 1.49 |
| two_divides | 16,384 | yes | 172,385 | 147,559..8,519,983 | 10.5 | yes | 1.724 | 2.03 |
| root_large | 16,384 | yes | 119,048 | 98,403..5,243,179 | 7.3 | yes | 1.190 | 1.52 |
| two_roots | 16,384 | yes | 134,306 | 114,788..7,209,260 | 8.2 | yes | 1.343 | 1.67 |
| root_divide | 16,384 | yes | 140,221 | 114,788..7,209,260 | 8.6 | yes | 1.402 | 1.74 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
