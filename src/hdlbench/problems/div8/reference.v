module div8(input [7:0] a, input [7:0] b, output reg [7:0] q, output reg [7:0] r);
  always @* begin
    if (b == 8'd0) begin q = 8'hFF; r = a; end
    else begin q = a / b; r = a % b; end
  end
endmodule
