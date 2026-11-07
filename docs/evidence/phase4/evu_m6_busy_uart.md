
Requests during a 1.5 s run, 4,410,000 points.
UART: sent RUN(101) at 0, a second RUN(102) at 0.30 s, PING(103) and PING(104) after that.
  reply to RUN(101) 1.46 s after the first RUN
  second RUN never answered, so it did not run; frame_errors 0 -> 3
Forced timeout at 0.3 s: state 'finished', 1.98 s in.
  afterwards STATUS answers: state 'finished', last_cycles 145530137 -> 145530137, which is 1.46 s, and still 145530137 a moment later. One run, no restart.
