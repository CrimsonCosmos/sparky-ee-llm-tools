// v13: 4-stage PIPELINED binary32 adder (round-to-nearest-even, same function and operating range as v12_fp32_add.v: finite normal-range operands/results; subnormals flushed).
//  stage 1: magnitude compare/swap, exponent difference, alignment shift with sticky;  stage 2: add/subtract;  stage 3: leading-zero count;  stage 4: normalise, round, pack.  One result per cycle, latency 4.
module fp32_add_p (input clk, input [31:0] a, input [31:0] b, output reg [31:0] y);
  // ---- stage 1 ----
  reg        sa, sb_, sbig, ssmall;
  reg [7:0]  ea, eb, ebig, esmall;
  reg [23:0] ma, mb, mbig, msmall;
  reg [8:0]  d;
  reg [26:0] big27, shifted, small27;
  reg        sticky;
  reg        s1_sres, s1_sub, s1_zero_small;
  reg [7:0]  s1_e;
  reg [26:0] s1_big, s1_sh;
  always @(posedge clk) begin
    sa = a[31]; sb_ = b[31]; ea = a[30:23]; eb = b[30:23];
    ma = (ea == 0) ? 24'd0 : {1'b1, a[22:0]};
    mb = (eb == 0) ? 24'd0 : {1'b1, b[22:0]};
    if ({ea, ma[22:0]} >= {eb, mb[22:0]}) begin sbig = sa; ebig = ea; mbig = ma; ssmall = sb_; esmall = eb; msmall = mb; end
    else                                   begin sbig = sb_; ebig = eb; mbig = mb; ssmall = sa; esmall = ea; msmall = ma; end
    d = {1'b0, ebig} - {1'b0, esmall};
    big27 = {mbig, 3'b000}; small27 = {msmall, 3'b000};
    sticky = 1'b0; shifted = 27'd0;
    if (msmall != 0) begin
      if (d > 9'd26) begin shifted = 27'd0; sticky = 1'b1; end
      else begin shifted = small27 >> d; sticky = |(small27 & ~(27'h7FFFFFF << d)); end
    end
    shifted[0] = shifted[0] | sticky;
    s1_sres <= sbig; s1_sub <= (sbig != ssmall); s1_e <= ebig; s1_big <= big27; s1_sh <= shifted;
  end
  // ---- stage 2: add / subtract ----
  reg        s2_sres;
  reg [7:0]  s2_e;
  reg [27:0] s2_s28;
  always @(posedge clk) begin
    s2_s28 <= s1_sub ? ({1'b0, s1_big} - {1'b0, s1_sh}) : ({1'b0, s1_big} + {1'b0, s1_sh});
    s2_sres <= s1_sres; s2_e <= s1_e;
  end
  // ---- stage 3: leading-zero count ----
  reg [4:0]  lz;
  integer i;
  reg        s3_sres;
  reg [7:0]  s3_e;
  reg [27:0] s3_s28;
  reg [4:0]  s3_lz;
  always @(posedge clk) begin
    lz = 5'd0;
    for (i = 26; i >= 0; i = i - 1) if (lz == 0 && s2_s28[i]) lz = 26 - i + 1;
    s3_sres <= s2_sres; s3_e <= s2_e; s3_s28 <= s2_s28; s3_lz <= lz;
  end
  // ---- stage 4: normalise, round, pack ----
  reg [26:0] sum27;
  reg [9:0]  e10;
  reg        guard, rs, lsb, up;
  reg [24:0] rounded;
  always @(posedge clk) begin
    e10 = {2'b00, s3_e};
    sum27 = 27'd0;
    if (s3_s28[27]) begin sum27 = {s3_s28[27:2], s3_s28[1] | s3_s28[0]}; e10 = e10 + 10'd1; end
    else if (s3_lz != 0) begin sum27 = s3_s28[26:0] << (s3_lz - 1); e10 = e10 - (s3_lz - 1); end
    guard = sum27[2]; rs = sum27[1] | sum27[0]; lsb = sum27[3];
    up = guard & (rs | lsb);
    rounded = {1'b0, sum27[26:3]} + {24'd0, up};
    if (rounded[24]) begin rounded = rounded >> 1; e10 = e10 + 10'd1; end
    if (sum27 == 27'd0 || e10[9] || e10 == 10'd0) y <= 32'd0;
    else if (e10 >= 10'd255) y <= {s3_sres, 8'hFF, 23'd0};
    else y <= {s3_sres, e10[7:0], rounded[22:0]};
  end
endmodule
