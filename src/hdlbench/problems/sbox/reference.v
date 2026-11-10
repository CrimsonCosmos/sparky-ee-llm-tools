module sbox(input [7:0] x, output [7:0] y);
  function [7:0] xtime(input [7:0] a); xtime = {a[6:0], 1'b0} ^ (a[7] ? 8'h1b : 8'h00); endfunction
  function [7:0] gmul(input [7:0] a, input [7:0] b);
    integer i; reg [7:0] p, t;
    begin p = 0; t = a; for (i = 0; i < 8; i = i + 1) begin if (b[i]) p = p ^ t; t = xtime(t); end gmul = p; end
  endfunction
  function [7:0] ginv(input [7:0] a);   // a^254 by square-and-multiply
    reg [7:0] r, s; integer i;
    begin r = 8'd1; s = a; for (i = 1; i < 8; i = i + 1) begin s = gmul(s, s); r = gmul(r, s); end ginv = r; end
  endfunction
  wire [7:0] b = ginv(x);
  assign y = b ^ {b[3:0], b[7:4]} ^ {b[4:0], b[7:5]} ^ {b[5:0], b[7:6]} ^ {b[6:0], b[7]} ^ 8'h63;       // y[i] = b[i]^b[i+4]^b[i+5]^b[i+6]^b[i+7]^c[i]
endmodule
