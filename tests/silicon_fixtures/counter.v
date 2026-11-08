module counter (input clk, input rst_n, input en, output reg [7:0] q);
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 8'd0;
    else if (en) q <= q + 8'd1;
endmodule
