// IEEE-754 binary32 adder, round-to-nearest-even, bit-exact with numpy/C float32 addition for FINITE NORMAL-RANGE operands and results.
// Not handled (documented, outside the operating range of partial sums of a quantised network): infinities/NaN, subnormal inputs (flushed to zero) and subnormal results (flushed to zero).
module fp32_add (input [31:0] a, input [31:0] b, output reg [31:0] y);
  reg        sa, sb, sbig, ssmall, sres;
  reg [7:0]  ea, eb, ebig, esmall, eres;
  reg [23:0] ma, mb, mbig, msmall;
  reg [26:0] big27, small27, shifted, sum27, norm;
  reg [27:0] s28;
  reg [8:0]  d;
  reg        sticky, guard, rs, lsb, up;
  reg [24:0] rounded;
  reg [9:0]  e10;
  integer i, lz;
  always @* begin
    sa = a[31]; sb = b[31]; ea = a[30:23]; eb = b[30:23];
    ma = (ea == 0) ? 24'd0 : {1'b1, a[22:0]};
    mb = (eb == 0) ? 24'd0 : {1'b1, b[22:0]};
    if (ea == 0) ea = 8'd0;
    if (eb == 0) eb = 8'd0;
    // big = operand with the larger magnitude
    if ({ea, ma[22:0]} >= {eb, mb[22:0]}) begin sbig = sa; ebig = ea; mbig = ma; ssmall = sb; esmall = eb; msmall = mb; end
    else                                   begin sbig = sb; ebig = eb; mbig = mb; ssmall = sa; esmall = ea; msmall = ma; end
    d = {1'b0, ebig} - {1'b0, esmall};
    big27 = {mbig, 3'b000};
    small27 = {msmall, 3'b000};
    sticky = 1'b0;
    if (msmall == 0) begin shifted = 27'd0; end
    else if (d > 9'd26) begin shifted = 27'd0; sticky = 1'b1; end
    else begin
      shifted = small27 >> d;
      sticky = |(small27 & ~(27'h7FFFFFF << d));
    end
    shifted[0] = shifted[0] | sticky;
    if (sbig == ssmall) s28 = {1'b0, big27} + {1'b0, shifted};
    else                s28 = {1'b0, big27} - {1'b0, shifted};
    sres = sbig; e10 = {2'b00, ebig};
    // normalise to a 27-bit value with the leading one at bit 26
    if (s28[27]) begin sum27 = {s28[27:2], s28[1] | s28[0]}; e10 = e10 + 10'd1; end
    else begin
      lz = 0;
      for (i = 26; i >= 0; i = i - 1) if (lz == 0 && s28[i]) lz = 26 - i + 1;      // lz = leading zeros + 1 (0 when the value is zero)
      if (lz == 0) begin sum27 = 27'd0; end
      else begin sum27 = s28[26:0] << (lz - 1); e10 = e10 - (lz - 1); end
    end
    // round to nearest even on 24 bits (bits 26:3), guard = bit 2, round|sticky = bits 1:0
    guard = sum27[2]; rs = sum27[1] | sum27[0]; lsb = sum27[3];
    up = guard & (rs | lsb);
    rounded = {1'b0, sum27[26:3]} + {24'd0, up};
    if (rounded[24]) begin rounded = rounded >> 1; e10 = e10 + 10'd1; end
    if (sum27 == 27'd0 || e10[9] || e10 == 10'd0) y = 32'd0;                          // exact zero (+0, as round-to-nearest gives for x - x) or flushed subnormal
    else if (e10 >= 10'd255) y = {sres, 8'hFF, 23'd0};
    else y = {sres, e10[7:0], rounded[22:0]};
  end
endmodule
