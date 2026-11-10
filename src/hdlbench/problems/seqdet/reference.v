module seqdet(input clk, input rst_n, input d, output hit);
  reg [3:0] h; reg v0, v1, v2, v3, r;
  always @(posedge clk) begin
    if (!rst_n) begin h <= 0; v0 <= 0; v1 <= 0; v2 <= 0; v3 <= 0; r <= 0; end
    else begin h <= {h[2:0], d}; v0 <= 1; v1 <= v0; v2 <= v1; v3 <= v2; r <= v2 && ({h[2:0], d} == 4'b1011); end
  end
  assign hit = r;
endmodule
