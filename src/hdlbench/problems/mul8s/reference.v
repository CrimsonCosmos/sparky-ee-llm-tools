module mul8s(input clk, input rst_n, input start, input [7:0] a, input [7:0] b, output reg busy, output reg done, output reg [15:0] p);
  reg [3:0] cnt; reg [15:0] acc; reg [15:0] ra; reg [7:0] rb;
  wire [15:0] nacc = acc + (rb[0] ? ra : 16'd0);
  always @(posedge clk) begin
    if (!rst_n) begin busy <= 0; done <= 0; p <= 0; cnt <= 0; acc <= 0; ra <= 0; rb <= 0; end
    else begin
      done <= 0;
      if (!busy) begin
        if (start) begin busy <= 1; cnt <= 0; acc <= 0; ra <= {8'b0, a}; rb <= b; end
      end else begin
        acc <= nacc; ra <= ra << 1; rb <= rb >> 1; cnt <= cnt + 1;
        if (cnt == 4'd7) begin busy <= 0; done <= 1; p <= nacc; end
      end
    end
  end
endmodule
