module sha256_round(input [31:0] a, b, c, d, e, f, g, h, k, w, output [31:0] na, nb, nc, nd, ne, nf, ng, nh);
  function [31:0] rotr(input [31:0] x, input integer n); rotr = (x >> n) | (x << (32 - n)); endfunction
  wire [31:0] s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25), s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
  wire [31:0] ch = (e & f) ^ (~e & g), maj = (a & b) ^ (a & c) ^ (b & c);
  wire [31:0] t1 = h + s1 + ch + k + w, t2 = s0 + maj;
  assign na = t1 + t2, nb = a, nc = b, nd = c, ne = d + t1, nf = e, ng = f, nh = g;
endmodule
