// v2 PE: active weight `w` plus shadow weight `ws`. A shadow write enable `wl` and a swap tag `swap` travel along the row with the activation wavefront,
// so a new tile can be loaded and activated without stopping the array: PE(i,j) swaps at the exact cycle the first activation of the new tile reaches it.
module tpu_pe (
  input clk, input rst_n,
  input wl_in, input signed [7:0] wd_in, output reg wl_out,
  input swap_in, output reg swap_out,
  input signed [7:0] a_in, output reg signed [7:0] a_out,
  input signed [31:0] p_in, output reg signed [31:0] p_out);
  reg signed [7:0] w, ws;
  wire signed [7:0] w_eff = swap_in ? ws : w;
  always @(posedge clk) begin
    if (wl_in) ws <= wd_in;
    if (swap_in) w <= ws;
    wl_out <= wl_in; swap_out <= swap_in;
    if (!rst_n) begin a_out <= 0; p_out <= 0; wl_out <= 0; swap_out <= 0; end
    else begin a_out <= a_in; p_out <= p_in + a_in * w_eff; end
  end
endmodule
