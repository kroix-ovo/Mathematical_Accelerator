
Requests during a 1.5 s run, 4,410,000 points.
UDP: sent RUN(101) at 0, a second RUN(102) at 0.30 s, PING(103) and PING(104) after that.
  reply to RUN(101) 1.46 s after the first RUN
  reply to RUN(102) 2.91 s after the first RUN
  second RUN answered, so it ran; frame_errors 0 -> 0
UART: sent RUN(101) at 0, a second RUN(102) at 0.30 s, PING(103) and PING(104) after that.
  reply to RUN(101) 1.46 s after the first RUN
  second RUN never answered, so it did not run; frame_errors 0 -> 3
Forced timeout at 0.3 s: state 'running', 2.35 s in.
  afterwards STATUS answers: state 'finished', last_cycles 145530137 -> 388212137, which is 3.88 s, and still 388212137 a moment later. One run, no restart.
