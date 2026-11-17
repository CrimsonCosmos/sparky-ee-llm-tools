// v1 processing element: weight-stationary INT8 MAC. The weight register is loaded by SHIFTING down the column (wload), activations flow left to right, partial sums flow top to bottom.
module tpu_pe (
  input  wire               clk,
  input  wire               rst_n,
  input  wire               wload,        // shift the weight chain by one row
  input  wire signed [7:0]  w_in,         // weight from the PE above
  output wire signed [7:0]  w_out,        // this PE's weight (feeds the PE below)
  input  wire signed [7:0]  a_in,         // activation from the left
  output reg  signed [7:0]  a_out,
  input  wire signed [31:0] p_in,         // partial sum from above
  output reg  signed [31:0] p_out
);
  reg signed [7:0] w;
  assign w_out = w;
  always @(posedge clk) begin
    if (wload) w <= w_in;
    if (!rst_n) begin a_out <= 8'sd0; p_out <= 32'sd0; end
    else begin a_out <= a_in; p_out <= p_in + a_in * w; end
  end
endmodule
