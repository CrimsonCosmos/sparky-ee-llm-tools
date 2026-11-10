module top2 (input clk, input rst_n, input en, output reg [7:0] y);
  wire [7:0] qa, qb;
  counter ca (.clk(clk), .rst_n(rst_n), .en(en), .q(qa));
  counter cb (.clk(clk), .rst_n(rst_n), .en(~en), .q(qb));
  always @(posedge clk or negedge rst_n)
    if (!rst_n) y <= 8'd0; else y <= qa ^ qb;
endmodule
