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
| loop_bfield | 64 | 11 | yes | 1.05 | 1.6e-07 | 1,127 | 810..46,386 | 17.6 | yes |
| plate_potential | 1,024 | 12 | yes | 25620.70 | 2.6e+04 | 14,754 | 12,394..655,666 | 14.4 | yes |
| plate_potential | 65,536 | 12 | yes | 0.30 | 3.7e-09 | 947,846 | 786,538..41,943,346 | 14.5 | yes |
| plate_potential | 1,048,576 | 12 | yes | 0.30 | 1.9e-09 | 15,158,786 | 12,583,018..671,088,946 | 14.5 | yes |
| fermi_half | 64 | 9 | yes | 0.13 | 3.9e-08 | 911 | 679..36,143 | 14.2 | yes |
| decay_wide | 128 | 5 | yes | 0.00 | 1.1e-08 | 911 | 738..41,258 | 7.1 | yes |
| decay_wide | 65,536 | 7 | yes | 0.00 | 0 | 520,262 | 458,852..26,214,700 | 7.9 | yes |
| rf_oscillatory | 512 | 6 | yes | 0.37 | 1.9e-09 | 3,609 | 3,171..184,619 | 7.0 | yes |
| rf_oscillatory | 65,536 | 9 | yes | 0.37 | 0 | 653,529 | 589,926..31,457,582 | 10.0 | yes |
| cube_large | 512 | 12 | yes | 1.00 | 1.9e-09 | 6,420 | 6,248..246,064 | 12.5 | yes |
| cube_large | 2,097,152 | 12 | yes | 0.00 | 1.9e-09 | 25,166,100 | 25,165,928..1,006,633,264 | 12.0 | yes |

| problem | points | kernel | board first | board repeat | NumPy f64 first | NumPy f64 repeat | NumPy f32 repeat | f32 error (ULP) | board vs f64, repeat |
|---|---|---|---|---|---|---|---|---|---|
| sin | 16 | 0.003 | 6.88 | 2.88 | 0.032 | 0.003 | 0.003 | 0.00 | 871.9x slower |
| sin | 16,384 | 0.657 | 907.91 | 2.98 | 0.133 | 0.082 | 0.024 | 0.00 | 36.2x slower |
| poly_box | 64 | 0.009 | 6.48 | 2.77 | 0.031 | 0.005 | 0.005 | 0.33 | 504.0x slower |
| gaussian_2d | 1,024 | 0.127 | 8.75 | 2.91 | 0.029 | 0.012 | 0.007 | 0.48 | 244.2x slower |
| gaussian_2d | 65,536 | 7.918 | 42.32 | 10.74 | 1.190 | 0.779 | 0.099 | 0.48 | 13.8x slower |
| gaussian_2d | 1,048,576 | 126.786 | 251.61 | 129.49 | 23.486 | 16.194 | 6.526 | 0.52 | 8.0x slower |
| charge_sphere | 4,096 | 0.663 | 8.87 | 2.99 | 0.122 | 0.097 | 0.048 | 0.76 | 30.9x slower |
| charge_sphere | 262,144 | 42.328 | 62.79 | 44.80 | 12.354 | 8.763 | 4.050 | 0.24 | 5.1x slower |
| circulation | 32 | 0.002 | 6.48 | 2.88 | 0.022 | 0.003 | 0.003 | 0.63 | 961.6x slower |
| flux_sphere | 1,024 | 0.250 | 9.22 | 2.82 | 0.129 | 0.108 | 0.061 | 0.44 | 26.1x slower |
| dipole_power | 32 | 0.008 | 7.43 | 2.80 | 0.036 | 0.009 | 0.008 | 1.38 | 321.8x slower |
| loop_bfield | 64 | 0.011 | 9.56 | 2.76 | 0.047 | 0.012 | 0.011 | 1.05 | 229.6x slower |
| plate_potential | 1,024 | 0.148 | 8.88 | 2.81 | 0.047 | 0.014 | 0.012 | 25620.70 | 195.2x slower |
| plate_potential | 65,536 | 9.478 | 42.54 | 11.81 | 0.730 | 0.187 | 0.083 | 0.30 | 63.1x slower |
| plate_potential | 1,048,576 | 151.588 | 271.67 | 153.97 | 18.335 | 13.870 | 6.410 | 0.30 | 11.1x slower |
| fermi_half | 64 | 0.009 | 8.75 | 2.90 | 0.025 | 0.007 | 0.006 | 0.87 | 427.1x slower |
| decay_wide | 128 | 0.009 | 13.12 | 2.93 | 0.015 | 0.004 | 0.003 | 1.00 | 732.6x slower |
| decay_wide | 65,536 | 5.203 | 131.11 | 7.39 | 0.785 | 0.592 | 0.077 | 0.00 | 12.5x slower |
| rf_oscillatory | 512 | 0.036 | 35.27 | 2.79 | 0.022 | 0.008 | 0.005 | 0.37 | 328.5x slower |
| rf_oscillatory | 65,536 | 6.535 | 124.83 | 8.46 | 0.752 | 0.662 | 0.098 | 0.63 | 12.8x slower |
| cube_large | 512 | 0.064 | 6.83 | 2.88 | 0.019 | 0.005 | 0.005 | 0.00 | 565.0x slower |
| cube_large | 2,097,152 | 251.661 | 279.41 | 254.76 | 28.065 | 17.913 | 8.831 | 0.00 | 14.2x slower |

| map run | words checked | runs | cycles | out of bounds | wrong words | wall ms |
|---|---|---|---|---|---|---|
| grad, 2,000 points | 6,000 | 3 | 109,770 | 0 | 0 | 903.5 |
| div, 2,000 points | 2,000 | 1 | 20,760 | 0 | 0 | 224.5 |
| curl, 2,000 points | 6,000 | 3 | 66,186 | 0 | 0 | 846.3 |
| mixed with bad points, 2,000 points | 4,000 | 2 | 33,290 | 0 | 0 | 560.6 |
| split across runs, 20,000 points | 20,000 | 2 | 194,695 | 0 | 0 | 2793.3 |
| tensor grid, 8,000 points | 24,000 | 3 | 436,995 | 0 | 0 | 1365.5 |

| equation | method | iterations | root | same as model | error (ULP) | kernel ms | wall ms |
|---|---|---|---|---|---|---|---|
| -x + cos(x) = 0 | bisect | 24 | 0.73908514 | yes | 0.08 | 0.045 | 106.1 |
| -x + cos(x) = 0 | newton | 4 | 0.73908514 | yes | 0.08 | 0.012 | 33.4 |
| x*exp(x) - 3 = 0 | bisect | 25 | 1.0499089 | yes | 0.16 | 0.052 | 110.4 |
| x*exp(x) - 3 = 0 | newton | 4 | 1.0499089 | yes | 0.16 | 0.017 | 40.9 |
| -x + tan(x) = 0 | newton | 3 | 4.4934096 | yes | 0.37 | 0.019 | 34.5 |
| tanh(x) - 1/x = 0 | bisect | 23 | 1.1996787 | yes | 0.16 | 0.097 | 106.7 |

Long mixed run: 65,536 points, 19 instructions, 1,360,293 cycles (20.8 per point), bounds 1,245,297..83,886,393, matches the model, in bounds.

| large run | points | match | cycles | low..high | clocks/pt | in bounds | kernel ms | repeat ms |
|---|---|---|---|---|---|---|---|---|
| fermi_large | 16,384 | yes | 165,468 | 147,559..9,175,343 | 10.1 | yes | 1.655 | 4.57 |
| lorentz_large | 16,384 | yes | 117,556 | 98,403..5,243,179 | 7.2 | yes | 1.176 | 3.57 |
| two_divides | 16,384 | yes | 172,385 | 147,559..8,519,983 | 10.5 | yes | 1.724 | 4.93 |
| root_large | 16,384 | yes | 117,129 | 98,403..5,243,179 | 7.1 | yes | 1.171 | 3.91 |
| two_roots | 16,384 | yes | 133,462 | 114,788..7,209,260 | 8.1 | yes | 1.335 | 3.62 |
| root_divide | 16,384 | yes | 139,923 | 114,788..7,209,260 | 8.5 | yes | 1.399 | 3.93 |

Random programs, seed 1: 100 runs, 14,265 points, 178,212 FPU operations, 0 mismatches, 0 out of bounds, largest cycles/high bound 0.437.

STATUS afterwards: 0 CRC errors, 0 frame errors.
Checks failed: 0.
