module shift32(input [31:0] x, input [4:0] sh, input [1:0] mode, output reg [31:0] y);
  wire signed [31:0] xs = x;
  wire [63:0] rot = {x, x} << sh;
  always @* case (mode)
    2'd0: y = x << sh;
    2'd1: y = x >> sh;
    2'd2: y = xs >>> sh;
    default: y = rot[63:32];
  endcase
endmodule
