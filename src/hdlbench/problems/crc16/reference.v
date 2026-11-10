module crc16(input [15:0] crc, input [7:0] data, output reg [15:0] next);
  integer i;
  always @* begin
    next = crc;
    for (i = 7; i >= 0; i = i - 1) next = (next[15] ^ data[i]) ? ((next << 1) ^ 16'h1021) : (next << 1);
  end
endmodule
