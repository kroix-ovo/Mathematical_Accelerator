# My S3 integration check (2026-11-11)

I brought the shared divide/root candidate onto main's current test and study
commits in the `sqrt-shared` worktree. I kept the original
`experiment/divsqrt-shared` checkout and its board-tested bitstream intact.

My fresh integration gate passed **27/27 steps**, including the full Vivado
build. The gate tested commit `eccab0eef6b5758fd2a08bcf598852d65a9d5f36`. To reproduce it with the optional benchmark dependency already installed, I use:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,bench]"
.\.venv\Scripts\python.exe hw/scripts/run_gate.py --vivado --jobs 4
```

I initially installed only the dev dependencies, so the gate's software row
records 460 passes and four optional Numba skips. After installing the bench
extra I reran the whole software suite: **464 passed, no skips**. I keep that
[complete rerun](software-complete.txt) separately so I don't rewrite the first
run's history. The [UDP simulator run](udp-simulator.txt) passed all 30 tests.

## what I measured again

| check | fresh integration result |
| --- | --- |
| full gate | 27/27 steps passed |
| root hard cases and randoms | 39,263 results in Verilator; 11,263 in Icarus; zero errors |
| SV UVM FPU | 103,920 results, zero mismatches; zero UVM errors/fatals |
| EVU profile | all 29 rows match the original S3 candidate exactly, including all three plate_potential sizes |
| full-design setup/hold slack | +0.237 ns / +0.033 ns at 100 MHz |
| full design | 20,121 LUTs, 13,880 FFs, 32 DSPs, 73 BRAM tiles |
| shared divide/root unit | 982 LUTs, 861 FFs, 12 DSPs |

I expanded the first-person comments in the shared core, FPU integration and
root-model tests. I checked that every RTL file has the same executable tokens
as the tested S3 candidate, and that the Python comment/docstring edits leave
the executable test AST unchanged. I also put the dedicated divide and root
hard-case commands into GitHub CI for both simulators. They repeat the exact
commands that passed this local gate.

The runtime source remains the tested commit's source. The following commit
only adds documentation, standalone timing-script comments, CI commands and
these evidence files, so I don't need to reroute for an evidence-only update.

## which bitstream has board evidence

The fresh integration bitstream is local to
`worktrees/sqrt-shared/build/vivado/nexys_top.bit`, with SHA-256:

`8b7bb6ad4b319985cdc36937395c7a6d13a0901d398c41ffcbc655ddd5e34138`

I built and routed that file, but **I haven't programmed or board-tested it**
as part of this integration. My unchanged study file is
`worktrees/divsqrt-shared/build/vivado/nexys_top.bit`, SHA-256:

`d6df14da5ae3392965c60eff07330073691d04ed9276cccbd28ee2fa9358840c`

That is the one with the [original UDP/UART board run](../sqrt-board/divsqrt-shared-20261111-144840/manifest.json).
Updating source doesn't replace an existing bitstream in main or any worktree.
I choose the checkout and build explicitly before programming.

## the records I kept

- [Gate summary](gate-summary.json): each step, its tested commit, time and result excerpts.
- [Provenance](provenance.json): source, timing, resources and both bitstream identities.
- [RTL source comparison](rtl-source-check.json) and [comment checks](comment-checks.json).
- [EVU profile](evu-profile.json): the actual clocks, issue counters, result checks and model bounds.
- [Timing](timing.rpt), [RMII timing](rmii_timing.rpt), [utilization](util.rpt), and [hierarchy](util_hier.rpt).
- [Python packages](requirements.txt) and the separate complete software/UDP logs above.

I leave the full simulator and Vivado console logs under this checkout's
`build/gate/` and `build/vivado/`. I don't commit the executable bitstream,
virtual environment or generated simulator objects.
