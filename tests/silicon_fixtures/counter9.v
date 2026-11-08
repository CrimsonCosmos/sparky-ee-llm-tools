// the same counter with one more bit: 9 flip-flops where the 8-bit RTL has 8 (used to show the flip-flop cross-check can fail)
module counter (input clk, input rst_n, input en, output reg [8:0] q);
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 9'd0;
    else if (en) q <= q + 9'd1;
endmodule
