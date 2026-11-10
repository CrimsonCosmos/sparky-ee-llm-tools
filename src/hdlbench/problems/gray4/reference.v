module gray4(input clk, input rst_n, input en, output [3:0] q);
  reg [3:0] n;
  always @(posedge clk) if (!rst_n) n <= 0; else if (en) n <= n + 1;
  assign q = n ^ (n >> 1);
endmodule
