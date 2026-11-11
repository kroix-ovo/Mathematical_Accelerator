# Errors log

My running log of everything that's gone wrong on this project: RTL bugs, board and link trouble, testbench bugs, tool weirdness, and the times I said something the data didn't back up. I pieced the early entries together after the fact from my session notes, so those are a bit tidier than they'd be if I'd written them on the day. From here on I add an entry the day something breaks, fixed or not.

Every entry gets a number (E-NN) so I can point at it from other docs, and every one ends with the fix, or with what I'm doing about it if it's still open.

## Still open

Quick list so I don't lose track. The full write-ups are in the dated entries below.

- **E-01:** Ethernet goes quiet after a long idle stretch (2026-11-06). No cause yet.
- **E-02:** the link won't come back after the laptop's adapter resets (2026-10-21). Needs MDIO.
- **E-03:** extra UDP datagrams during a run get dropped silently (2026-11-05).
- **E-04:** a run can't be cancelled (2026-11-05).
- **E-05:** 99th-percentile round trip got worse on the new Realtek driver (2026-10-21).
- **E-49:** the Ethernet transmit checksum path has almost no timing margin, and it failed the SRT divider's build (2026-11-09).

---

## 2026-09-30: phase 0 and 1, link and control layer

**E-06. Model counted empty packets as errors.** The Vivado vector test flagged it: the hardware ignored empty packets and my Python model counted them as errors. The two just disagreed on what an empty packet means.
**Fix:** changed the model to ignore them, same as the hardware.

**E-07. Scratchpad read as X in simulation.** Unwritten memory came back undefined in sim, but on the FPGA it starts at zero, so sim and board didn't match.
**Fix:** initialised the scratchpad to zero.

**E-08. Write enable missing the byte select.** Caught this one reading the code before any test ran: `m_we` wasn't qualified by `bsel == 3`.
**Fix:** added the qualifier.

**E-09. `uart_tx` counter and COBS decoder were a mess.** First drafts of both were muddled and hard to follow. Nothing had failed yet, but I didn't trust them.
**Fix:** rewrote both cleanly before testing.

**E-10. Barely met 100 MHz.** First build passed with only +0.36 ns of slack, which is too tight for logic this simple. The long paths were the transmit path, the scratchpad writes, and the command processor's `len`-based loop compares.
**Fix:** registered the transmit path and scratchpad writes (+0.55 ns), then precomputed the loop limits into registers (+0.72 ns).

## 2026-10-08 to 2026-10-14: phase 2, fp32 FPU

**E-11. Adder lost NX on tiny operands.** The UVM regression found adds with a very small operand coming back without the inexact flag. Shifting the tiny operand far enough dropped its bits before the sticky bit ever saw them. This was the only real arithmetic bug the regression caught.
**Fix:** the sticky bit now collects every bit shifted out, however far the shift goes.

**E-12. Missed 100 MHz by about 1.5 ns.** The block RAM output went straight into the FPU, and rounding happened in one clock.
**Fix:** registered the RAM output and split rounding over two clocks. The kernel went to `4n + 9` cycles, `33n + 6` for divide and sqrt.

**E-13. Two `always_ff` blocks driving one array.** Verilator complained, since that breaks the one-driver rule.
**Fix:** plain `always` blocks, the way Vivado's dual-port RAM template does it.

**E-14. A pile of tool problems.** Most of the day went to tools, not the design. Each workaround has a comment at the spot, and they're also in `docs/verification.md`.
- `xelab` crashed on an inline constraint mixing a sequence variable and an enum cast. **Fix:** two `randomize()` calls and `local::` in `fpu_rand_seq`.
- `xelab` wrote DPI paths into C strings, and `C:\Users` has the bad escape `\U`. **Fix:** `run_uvm.py` passes forward-slash paths.
- `cmd.exe` split plusargs at `=`. **Fix:** quote them on Windows.
- The ancient gcc inside `xsc` compiles `<` and `<=` to quiet compares, and its `sqrtf(-x)` returns the input. **Fix:** the C reference spells out those IEEE rules itself, and pytest builds the C model with the same compiler and flags.
- Icarus turned a port-connection expression into X. **Fix:** named wires in `fpu.sv`.
- Icarus wants a cast for `?:` between enum values. **Fix:** `if`/`else` in `kernel_engine.sv`.
- Icarus defaults to 1 s time precision, so cocotb couldn't make a 10 ns clock. **Fix:** pass an explicit timescale.
- `xvlog` wouldn't take a function's `output` queue into a `ref` argument. **Fix:** `inout` on the helpers.
- Shell escaping put an actual null byte into a file. **Fix:** replaced it, and edits go through script files now.

**E-15. Every command took 16 ms on the board.** Not the FPGA: the FTDI driver's default latency timer is 16 ms. Also learned the hard way that unplugging the board wipes the FPGA, since JTAG programming doesn't survive a power cycle.
**Fix:** set the latency timer to 1 ms in Device Manager, and the NOP round trip dropped to about 1 ms. Reprogram with `program.tcl` after every power cycle.

## 2026-10-20 to 2026-10-21: Ethernet

**E-16. Every reply went out twice.** ARP, ping and UDP, all doubled in sim. The transmitter was looking at the stale "pending" flag and starting again.
**Fix:** a one-clock wait before it checks the flag again.

**E-17. Second frame in a datagram could get dropped.** There's one clock while `cmd_proc` hands its reply to `frame_tx` where a second frame could slip in and be lost.
**Fix:** the idle check covers that clock now.

**E-18. RMII receive window off-centre.** The first build looked fine, but only because my multicycle constraint made the receive hold check way too lenient. With the correct check, the window was off-centre.
**Fix:** took the PHY delays from the LAN8720A datasheet (rev 1.4, Table 5.10), moved the 50 MHz reference clock to 321.75°, and made TXD and TX_EN change on the falling edge. The board-level xsim checker throws 594 violations at the wrong phase, so it isn't passing by luck. The margins still lean on my guess of 1 ns of board trace delay.

**E-19. `eth_rx` checks were wrong.** The ARP fixed-header check and the `l4_end` width came out muddled in my first pass.
**Fix:** rewrote both before testing.

**E-20. The cocotb vector test had never worked on Icarus.** The runner didn't set a timescale, and CI only runs Verilator, so nobody noticed. A separate "results file not found" turned out to be my WSL venv missing `mathaccel`.
**Fix:** the runner sets a timescale, and I ran with `PYTHONPATH=sw`. Passes on both simulators now.

**E-21. Link wouldn't come up.** The board's RJ45 LEDs were on, but Windows said the cable was unplugged. It was the Realtek adapter's power-saving stuff.
**Fix:** turned off Green Ethernet, Power Saving Mode and Gigabit Lite, replugged, and got 100 Mbit/s full duplex. The later driver update turned them back on and the link stayed up anyway.

**E-22. Replies showing up seconds late.** About 1 in 190 replies arrived late, up to 5 s. The board had run every request; the reply just didn't reach Python until the next frame came in. I added latching debug LEDs (LED12 to LED14) and they stayed dark through about 50 stalls, so the board sent every reply within 10 ms. The laptop's adapter was sitting on received frames.
**Fix:** turning off Interrupt Moderation did nothing. Turning off Receive Side Scaling got it to about 1 in 20,000. Updating the driver to 10.80.50.407 got 0 in 10,000. I kept the debug LEDs in the design.

**E-02. Link doesn't come back after the laptop's adapter resets. (open)** Every time a Windows settings change reset the adapter, the link stayed down until I replugged the cable at the laptop end. The design has no MDIO management (`nexys_top.sv` runs on the PHY's power-on defaults), so the FPGA never notices the link is gone or restarts the PHY. On 2026-11-05 a replug wasn't even enough (see E-38).
**Fix so far:** replug the cable, and reprogram if the UART is dead too. The real fix is reading the PHY's link status over MDIO and resetting it when the link stays down.

**E-05. Tail latency went up on the new driver. (open)** After the driver update the 99th-percentile NOP round trip was 986 µs, up from 234 µs, even though the median improved to 88 µs. I haven't looked into it, and I haven't tried the new driver with RSS back on.
**Fix so far:** nothing. Every reply still arrives, so it's not hurting correctness.

**E-23. More tool problems.**
- Icarus crashed (`assign_to_lvector`) on `'{a, b}` into an unpacked-array output port. **Fix:** two separate assignments in `eth_rx.sv`.
- xsim crashed on a `fork` inside a `foreach` inside a `fork`. **Fix:** `tb_nexys_eth` uses a free-running receiver that fills a queue.
- A virtual interface has to match its instance's parameter values. **Fix:** the PHY model's timing values are variables, not parameters.

## 2026-10-22 to 2026-11-04: phase 3, CORDIC functions

**E-24. Signedness bug in the CORDIC shift.** I wrote `(mode==M_LIN) ? '0 : (cy >>> i)`, and that made the whole expression unsigned, so `>>>` shifted logically. The scoreboard caught the bad results.
**Fix:** split it into separate statements, with a comment so I don't do it again.

**E-25. FPU tags coming round again.** The first SV run had an EXP result waiting 263 clocks. Tags are 8 bits, so after 256 newer requests a tag repeats and the caller can't tell whose result is whose.
**Fix:** an age rule in the result FIFO so the oldest result leaves first, plus upper-bound latency checks in the scoreboard.

**E-26. Timing kept slipping as CORDIC grew.** Slack dropped to +0.268 ns, then +0.061 ns in milestone 3.
**Fix:** moved the exponent add to B1, gave `round_pre` its own stage (latency 51 to 52), and folded `zneg` and the linear gating into the adder direction, which also cut LUTs from 11.5k to 8.5k.

**E-27. Reference model problems.**
- MinGW's `sin` and `cos` are wrong for huge arguments (`sin(FLT_MAX)` gave 0.99998). **Fix:** my own integer Payne–Hanek reduction in `fp_ref.c`.
- `mpf_to_fraction` ran out of memory on `exp` of huge negative inputs. **Fix:** clamp to 2^±400.
- `sin` of a subnormal was missing UF, because mpmath hands back exactly x. **Fix:** tiny results off the exact list force UF.
- `exp` overflow counted as infinite ULP error. **Fix:** a correctly rounded inf counts as 0.5 ULP.
- The `small_sin` significand window was off by one bit, coverage bins counted cases that can't happen, and the screened-mode count included rule-decided cases. **Fix:** corrected all three.

**E-28. Keywords, Icarus and a slow driver.**
- `small` and `pure` are keywords. **Fix:** renamed `is_small` and `only6`.
- Icarus won't do field writes on array-of-struct elements. **Fix:** whole-struct local assignments, local copies for reads, and the valid bits moved into the shift register.
- The pyuvm FPU driver wasted an idle clock per item. **Fix:** it grabs the next item on the falling edge.

**E-29. Vivado failing for no reason.** First a stale `.Xil` folder, then "couldn't read file …/common.tcl", then "…/unimacro_vhdl.tcl". Every time, WSL simulations were running alongside. Antivirus scanning `C:\Xilinx` might be part of it; I haven't checked.
**Fix:** delete `.Xil` first, and never run Vivado and WSL sims at the same time. Hasn't happened since.

**E-30. Icarus `EthMultiTest` read X at time 0.** Its monitor read `rmii_tx_en` before reset had settled it. It failed the same way on a102de5, so it was older than phase 3.
**Fix:** I fixed it in a67302d. The monitor in `test_eth_uvm.py` waits for reset to release (`await FallingEdge(dut.rst)`) before it starts watching.

**E-31. Things I claimed without measuring.**
- I said `ln`'s lower correctly-rounded rate came from the near-1 path. Measured it: the near-1 path misses 4.7%, the main path 0.52%, and the worst error is on the main path. **Fix:** the docs say that now.
- I guessed 80 minutes per function for the every-input sweep. At about 0.7 MB/s it's more like 26 hours. **Fix:** corrected in the docs.
- `docs/protocol.md` said 0.61 ULP max. The highest I ever measured is 0.629 (atan2). **Fix:** changed it to 0.629.

## 2026-11-04 to 2026-11-05: phase 4 milestones 1 to 4, the expression unit

**E-32. Every negative total came out as -inf.** First accumulator run failed on every negative sum. In `fp_accum`, `33'(~rd)` widens before it inverts, so the extra top bit got inverted too and pushed a false carry up through the segments.
**Fix:** `{1'b0, ~rd}`.

**E-33. xsim hung on every EVU run.** Verilator was fine. `desc_addr` went through `arg32(0)` in a port expression, which xsim evaluated once at time 0 and got X. Real hardware would have been fine; Verilator just hid it.
**Fix:** a plain slice, `args[SCRATCH_AW-1:0]`.

**E-34. Injected bugs showed holes in my tests.** First pass of 26 injected bugs only caught 21. Table 5 was never range-tested, power-of-two grids hid lost bits, and nothing checked a carry out of the running sum. Back in milestone 3 my mutation script also said one bug survived when it hadn't; the script misread its own output.
**Fix:** added tests for all three holes, and now 27 of 30 get caught. The other 3 can't be told apart from correct behaviour (one needs a 2^32+1 grid, which can't exist). The script trusts the exit code now instead of parsing text.

**E-35. Testbench races and monitor bugs.**
- The accumulator monitor lost a result when the driver's clear landed on the same clock as `out_valid`. **Fix:** check `out_valid` first.
- The EVU monitor missed `done`, which shows up after busy drops. **Fix:** check `done` before the busy skip.
- `EvuLimitTest` wrote the next mode on the done clock. My first guess (a reset skip) was wrong. **Fix:** one more falling edge.
- A `start` helper clobbered `uvm_sequence.start`. **Fix:** renamed `run_desc`.
- Icarus showed X on `pending` before the first run. **Fix:** the per-run counters reset on `fifo_rst`.
- The random generator's `b` field could hit 127 and trip `Program.check`. **Fix:** `randrange(64)`.
- `program` is a keyword. **Fix:** renamed `code_at`.
- C functions weren't exported on Windows. **Fix:** an `EXPORT` macro.

**E-04. No way to cancel a run. (open)** Once an EVU run starts, only a reset stops it. A huge grid just keeps the board busy until it's done.
**Fix so far:** guard rails on the host. The driver refuses anything it expects to take over 30 s (`RunTooLong`), and `Run.check` rejects a dimension of 65,536 or more and more than 2^32 points. The real fix would be an abort command in the protocol.

## 2026-11-05 to 2026-11-06: phase 4 milestones 5 and 6, host tools and board

**E-36. The biggest class rules could never fit.** `Run.check` threw out the 4096×16 rules because one axis went past the 65,535 limit.
**Fix:** `integral_run` splits long axes into a panel midpoint and a node offset on two grid dimensions.

**E-37. udpsim timing out.** The Python model is a lot slower than the hardware, so timeouts based on cycle counts kept firing.
**Fix:** `evu_time_scale` is 100 for udpsim only. Board timeouts didn't change.

**E-38. Board dead at the start of milestone 6.** The link was `Disconnected` before and after programming, a replug didn't help, and the UART had gone quiet too. The board had probably lost its configuration. No idea why.
**Fix:** reprogrammed it, and the link came up within 2 s. Same root problem as E-02 as far as I can tell.

**E-03. Extra datagrams during a run vanish. (open)** A datagram holds its slot until its command finishes, so during a run only one more fits. That one waits and runs afterwards. Anything past it gets dropped with no reply, and only LED6 shows it. A resent RUN gets held and runs a second time, and no counter shows that either. I'd also written that two more fit; reading the RTL said one.
**Fix so far:** the driver sends EVU runs exactly once (`evu_start`), and on a timeout it asks whether the run is `running`, `finished` or `not started` instead of resending. Fixed my driver comment and the milestone 5 gate. `docs/protocol.md` still says frames during a run are dropped, which is only true on the UART. That waits for milestone 7.

**E-39. `frame_errors` stuck at 6.** Not really a fault, just logging it so the number makes sense later: my deliberate UART requests during a run got dropped, which is what the UART does.
**Fix:** nothing needed. It clears on reset.

**E-42. Small stuff in my own code.**
- A `test_calc` test expected 2.0 for a gradient line integral. **Fix:** it's 4.0.
- A report check matched the speed table, which has the same column count as the correctness table. **Fix:** filter on `" | yes | "`.
- `sorted` choked on a SymPy boolean. **Fix:** wrap it in `bool()`.
- The `exact_sum` sNaN check gave up after 1,000 terms. **Fix:** vectorised it so it checks all of them.
- The `early_last` coverage logic was wrong. **Fix:** `any(i.last for i in written[:-1])`.
- A test lambda failed on base 0 for unused tables (real memory was fine). **Fix:** fixed the lambda.
- Big inline bash commands with several heredocs wouldn't parse, and heredocs mangle `\n` and backticks. **Fix:** edits go through script files or the Edit tool.
- My shell grabbed an old system Verilator for cocotb. **Fix:** source the OSS CAD Suite first, like `run_pyuvm.py` does.

## 2026-11-06 to 2026-11-07: CPU comparison

**E-40. CPU calibration said 12 GHz.** LLVM vectorised my plain multiply loop, so it was doing several passes per clock.
**Fix:** switched to `x = (x*k) ^ (x>>29)`, which can't be vectorised and takes 4 clocks a pass. Got 4.227 GHz against Windows' 4.268 GHz on the busiest core.

**E-41. More claims the data didn't back up.**
- I'd said the board uses fewer clocks per point than the CPU on big runs. That was only true against NumPy. Against compiled Numba it depends on the program: the board wins on CORDIC-heavy work and gets crushed on divides and tensor grids. **Fix:** the comparison doc says that now.
- A gate draft said "at most 35x" and listed causes I never measured. **Fix:** swapped in the measured numbers and dropped the causes.
- Back in milestone 4 a draft had some unsupported figures (43 s per instruction, bug counts, the old WHS). **Fix:** corrected from the logs before it went in.

**E-43. Small stuff while building the comparison.**
- ruff flagged long lines and a loop closure in `evu_report.py`. **Fix:** per-file ignores in `pyproject.toml`.
- The enum reference `u_evu.RUN` for the profile counters didn't work. **Fix:** `PH_*` localparams on `cov_phase`.

**E-01. Ethernet went quiet after sitting idle. (open)** After about 40 minutes with no traffic from me, the board stopped answering ARP, ping and UDP. Windows still said the adapter was Up at 100 Mbps but had no ARP entry for the board. The UART was fine: v0.4, and `last_cycles` still held my last run (388,212,137), so the board hadn't reset and the core was healthy. UDP had answered STATUS right before the idle stretch. Then I reprogrammed it before reading any LEDs, which threw away the evidence. Two reprograms later the link was still `Disconnected`, and it still was on 2026-11-07. Could be the board's receive path, the PHY, or the laptop's adapter; nothing I have separates them. First time the design has sat idle that long and then been asked to talk.
**Fix so far:** none. Next time, before touching the board: read LED5, LED6 and LED8 to LED14, check the adapter in Windows, get STATUS over the UART, then try a replug at the laptop end. Once the link's back I'll run an idle watcher that sends a UDP STATUS at about 10, 20 and 40 minutes, with a UART STATUS each time, and see if it happens again. If it's the PHY, the MDIO fix for E-02 probably covers it.

## 2026-11-08 to 2026-11-09: divider split and experiments

**E-44. Two cycle-bound tests assumed the 33-clock divider.** Experiment A's 20-clock divider failed `test_cycle_bounds_follow_the_divider`, which wanted the divide program's bound to be over 30 clocks per point higher than the plain one. My new overlap test made the same assumption about one lane, so it would have failed for D, B and E.
**Fix:** both tests now compute the expected floor from `DIV_INTERVAL`, `DIV_LANES` and the square-root constants (`324e24f`), cherry-picked identically into every branch. The baseline's numbers didn't change.

**E-45. My overlap probe never saw a pipelined divider busy.** `DsProbe` counted the divider as busy while its `in_ready` was low. B's pipelined divider is almost never refusing, so the probe reported 0 clocks of divide/sqrt overlap and the regression failed, though every result was right.
**Fix:** in B and E, the probe counts a divide as in progress while it holds a queue place (`u_div.cnt != 0`). Then B's regression showed 4,127 clocks of overlap.

**E-46. A comment I generated was too long for ruff.** My candidate script wrote a `LAT_CORD_MAX` comment of 129 characters in D, B and E, and D's lint step failed.
**Fix:** shortened it in all three before B's and E's gates ran.

**E-47. `cmd.exe` split `STEPS=1` at the equals sign.** Passing `-tclargs STEPS=1` to Vivado through `vivado.bat` gave synthesis a generic called `1`. Quoting it got the quotes through to Tcl as part of the value. Same trap as the xsim plusargs in verification.md.
**Fix:** `build_div.tcl` strips quotes from its arguments, and I pass the argument quoted.

**E-48. Vivado sometimes couldn't read its own install files.** Three out-of-context builds stopped at synthesis with `couldn't read file "C:/Xilinx/Vivado/2023.2/scripts/rt/..."`, twice while I was starting several at once and once with only one running. Running the same command again worked every time. There was also a second, idle `vivado.exe` I hadn't started, which I left alone. I don't know what was holding the files.
**Fix so far:** retried. If it keeps happening, check for an antivirus scan of `C:\Xilinx` and start Vivado runs one at a time.

**E-49. The Ethernet checksum path failed the SRT divider's full build. (open)** Experiment C routed at WNS -0.101 ns. Its worst path was `u_enc/rd_reg` → `u_eth/u_tx/psum_reg`, nothing to do with the divider: out of context the SRT divider had +1.356 ns. It is also the worst path in B's build (+0.191). Every build has had only 0.05 to 0.3 ns of slack, so placement changes elsewhere can push this path over.
**Fix so far:** none; I counted C as failing timing, as my rule says. The path needs its own look (a register stage or a narrower adder) before I merge any divider.

## 2026-11-10: divider board runs

**E-50. No Ethernet link at the start of the divider board runs.** Windows showed the `Ethernet` adapter (192.168.10.1) as Disconnected at 0 bps, and ping to 192.168.10.2 failed. The board was on USB and answered PING and STATUS over COM3 with zero CRC and frame errors. Reprogramming the board didn't bring the link up, though it did last time (see the replug trap in verification.md), so I think the cable isn't seated at one end.
**Fix:** the cable had come loose. After reseating it, Windows showed 100 Mbps and the board answered ping, with no reprogramming needed. The UART runs had gone ahead first, then the Ethernet runs passed.

**E-51. My `--no-eth` option ran the Ethernet suite anyway.** I added `--no-eth` to `board_suite.py` when the link was down (E-50). It skipped the bring-up but left the UDP suite in a block that still ran, so each "UART only" run did UART, UDP, then UART again, and the second UART report overwrote the first. The cable had been reseated by then, so the UDP suite worked and every step passed. I only noticed because the folders had an `evu_udp.md` they shouldn't have had.
**Fix:** the UDP block now checks `--no-eth` too. A stubbed run shows exactly one step, `evu-uart`. The evidence README says which reports came from which steps.

## 2026-11-11: square-root experiments

**E-52. My probes took the old square root's step counter for a queue.** To let one testbench serve every square-root candidate, I made the overlap and queue probes treat any divide or square-root unit with a `cnt` register as pipelined. The original one-at-a-time `fp_sqrt` also has a `cnt`: it counts its iteration clocks. On main the queue probe then looked for a `q_cnt` that doesn't exist, and all seven pyuvm FPU steps failed at time 45 ns. The experiment branches weren't affected, since every unit there that has `cnt` also has a queue.
**Fix:** the probes now look for `q_cnt`, which only a unit with a result queue has. I cherry-picked the same commit into all three branches.

**E-53. The three square-root gates stopped after two minutes, and no Vivado build ran.** Each worktree's `summary.json` said "failed" on 18 of 26 steps, the same 18 in all three, including steps the square root doesn't touch (udpsim, CORDIC, xsim). Every one of those steps took 0.0 to 0.1 s and left an empty log, so they never started. My first launcher script called Vivado through `cmd.exe /c`, which Git Bash mangles, so I stopped it. That also took down the gate processes it had started. The steps already running finished, and every one queued behind them failed to launch. My second script was meant to wait for the gates and then run the builds, but it exited with code 4 and printed nothing. Its wait loop counted processes whose command line matched `run_gate.py`, and that query matches itself, so it could never have reached zero anyway.
**Fix:** none of the 18 results count, and I'm re-running each gate from its own shell. The wait now leaves out its own process, and the Vivado builds run only after every gate's summary has been read.
