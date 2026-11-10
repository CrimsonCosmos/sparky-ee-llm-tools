Write a combinational Verilog module `shift32` with inputs `x[31:0]`, `sh[4:0]` (shift amount 0..31) and `mode[1:0]`, output `y[31:0]`:
mode 0: logical shift left by sh; mode 1: logical shift right by sh; mode 2: arithmetic shift right by sh (sign bit x[31] replicated); mode 3: rotate left by sh. Fewer LUT4s and fewer logic levels are better.
