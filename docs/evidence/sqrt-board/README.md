# My square-root board runs (2026-11-11)

I copied these from `build/board/` straight after the runs. Each folder is one `tools/board_suite.py` run on one bitstream, and its `manifest.json` names the commit, the bitstream's SHA-256 and every step's exit code. The script still calls the main checkout "split-baseline" (a name left over from the divider study); here that's the square-root baseline: E's divider with the original one-at-a-time square root.

| folder | design | bitstream SHA-256 (first 12) | steps |
|---|---|---|---|
| `split-baseline-20261111-144538` | baseline | `4fd517e3d5cd` | program, Ethernet bring-up (failed: link down, E-56) |
| `split-baseline-20261111-144629` | baseline | `4fd517e3d5cd` | Ethernet bring-up, UDP suite, UART suite |
| `divsqrt-shared-20261111-144840` | S3 shared | `d6df14da5ae3` | program, Ethernet bring-up, UDP suite, UART suite |
| `sqrt-multiplicative-20261111-145059` | S2 multiplicative | `6cd545803bad` | program, Ethernet bring-up, UDP suite, UART suite |
| `sqrt-pipeline-20261111-145319` | S1 pipeline | `d73a90d2d917` | program, Ethernet bring-up, UDP suite, UART suite |

The first baseline run programmed the board and then stopped, because the cable had been unplugged. After it was plugged back in, I ran the suite again without programming. Programming resets the PHY, and I didn't want to lose the link a second time. So the second baseline run has no `program` step: the board was still running the bitstream the first run had loaded about a minute earlier, with the same SHA-256.

Every report has 0 failed checks: all results bit-exact against each design's own model, every cycle count inside that design's own bounds, and zero CRC and frame errors afterwards. On the six 16,384-point runs (three divide, three square root), every design's board clock count is exactly its simulated `EvuProfileTest` count plus the 2 clocks kernel_engine adds around the unit, over both links.
