module lfsr8(input clk, input rst_n, input en, output reg [7:0] q);
  always @(posedge clk) if (!rst_n) q <= 8'h01; else if (en) q <= {q[6:0], q[7] ^ q[5] ^ q[4] ^ q[3]};
endmodule
