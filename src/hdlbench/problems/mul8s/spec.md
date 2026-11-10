Write a synchronous Verilog module `mul8s` (a sequential 8x8 unsigned multiplier) with inputs `clk`, `rst_n` (active-low, synchronous reset), `start`, `a[7:0]`, `b[7:0]` and outputs `busy`, `done`, `p[15:0]`.
Behaviour (all outputs change only on rising clock edges; reset can arrive at any time and returns the unit to idle with busy = 0, done = 0, p = 0):
 * Idle means busy = 0. If `start` is high at a rising edge while idle, the operands a and b present at that edge are captured and busy is 1 after that edge. A `start` while busy is ignored.
 * If the capturing edge is edge k, then busy is 1 after edges k .. k+7 and 0 after edge k+8; `done` is 1 after edge k+8 only (0 at all other times); `p` becomes a*b after edge k+8 and holds that value until the next completion (it is 0 after reset and must not show partial results at any other time).
 * Edge k+8 itself is not a capture edge for a new start (busy is still 1 when it arrives); a start that arrives at edge k+9 or later is accepted.
Fewer LUT4s, fewer flip-flops and higher clock frequency are better. The design must be exactly correct from reset for any sequence of inputs.
