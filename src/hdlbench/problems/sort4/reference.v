// 5-comparator sorting network
module sort4(input [3:0] a0, a1, a2, a3, output [3:0] s0, s1, s2, s3);
  wire [3:0] b0 = a0 < a1 ? a0 : a1, b1 = a0 < a1 ? a1 : a0;
  wire [3:0] b2 = a2 < a3 ? a2 : a3, b3 = a2 < a3 ? a3 : a2;
  wire [3:0] c0 = b0 < b2 ? b0 : b2, c2 = b0 < b2 ? b2 : b0;
  wire [3:0] c1 = b1 < b3 ? b1 : b3, c3 = b1 < b3 ? b3 : b1;
  assign s0 = c0, s3 = c3;
  assign s1 = c1 < c2 ? c1 : c2, s2 = c1 < c2 ? c2 : c1;
endmodule
