module prienc8(input [7:0] in, output reg [2:0] idx, output valid);
  assign valid = |in;
  always @* begin
    casez (in)
      8'b1???????: idx = 7; 8'b01??????: idx = 6; 8'b001?????: idx = 5; 8'b0001????: idx = 4;
      8'b00001???: idx = 3; 8'b000001??: idx = 2; 8'b0000001?: idx = 1; 8'b00000001: idx = 0;
      default: idx = 3'bxxx;
    endcase
  end
endmodule
