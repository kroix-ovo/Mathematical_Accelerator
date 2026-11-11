# My divider board runs (2026-11-10)

I copied these from `build/board/` straight after the runs. Each folder is one `tools/board_suite.py` run on one bitstream, and its `manifest.json` names the commit, the bitstream's SHA-256 and every step's exit code.

| folder | design | bitstream SHA-256 (first 12) | steps |
|---|---|---|---|
| `split-baseline-20261110-010329` | split baseline | `2da6f20f54c3` | program, UART suite, UDP suite, UART suite again |
| `split-baseline-20261110-011419` | split baseline | `2da6f20f54c3` | program, Ethernet bring-up, UDP suite |
| `div-pipeline-20261110-010714` | B pipeline | `7c9cf9bea99e` | program, UART suite, UDP suite, UART suite again |
| `div-pipeline-20261110-011544` | B pipeline | `7c9cf9bea99e` | program, Ethernet bring-up, UDP suite |
| `div-multiplicative-20261110-011036` | E reciprocal | `398a07521aa6` | program, UART suite, UDP suite, UART suite again |
| `div-multiplicative-20261110-011723` | E reciprocal | `398a07521aa6` | program, Ethernet bring-up, UDP suite |

The first run of each design was meant to be UART only, because the Ethernet link was down (E-50). A bug in my `--no-eth` option (E-51) made it also run the UDP suite and then the UART suite a second time. By then the cable had been reseated, so the UDP suite worked. The second UART report overwrote the first, so `evu_uart.md` here is the second one. Every step in every run passed.

Every report has 0 failed checks: all results bit-exact against the model, every cycle count inside that design's own bounds, and zero CRC and frame errors afterwards. The class problems took the same number of clocks on both links in every design.
