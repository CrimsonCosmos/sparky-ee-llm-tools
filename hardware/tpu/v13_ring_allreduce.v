// v13: timing-oriented ring all-reduce node (same algorithm and result as v12_ring_allreduce.v). Changes for a credible clock: the float adder is a 3-stage pipeline (fp32_add_p); chunk indices are
// registers updated incrementally (no modulo logic); memory addresses are {chunk, beat} concatenations (NBC must be a power of two); local data is read in the cycle before it is needed (registered).
// A step now lasts nbc + 4 cycles instead of nbc + 1 (receive latency 1 + adder pipeline 3), validated by the RTL test (perf.ring_allreduce_cycles with pipe=3).
module tpu_ring_ar_node #(parameter C = 4, parameter RANK = 0, parameter W = 16, parameter NBCL2 = 6) (
  input  wire                  clk,
  input  wire                  rst_n,
  input  wire                  lwe,
  input  wire [$clog2(C)+NBCL2-1:0] laddr,
  input  wire [W*8-1:0]        ldata,
  input  wire [$clog2(C)+NBCL2-1:0] raddr,
  output wire [W*8-1:0]        rdata,
  input  wire                  start,
  input  wire [15:0]           nbc,
  output reg                   done,
  input  wire                  rin_valid,
  input  wire [W*8-1:0]        rin_data,
  output reg                   rout_valid,
  output reg  [W*8-1:0]        rout_data
);
  localparam L = W / 4;
  localparam NBC = 1 << NBCL2;
  localparam CW = $clog2(C);
  reg [W*8-1:0] loc [0:C*NBC-1];
  reg [W*8-1:0] res [0:C*NBC-1];
  reg [W*8-1:0] buf0 [0:NBC-1];
  reg [W*8-1:0] buf1 [0:NBC-1];
  reg cur;
  reg [1:0]  phase;                 // 0 idle, 1 reduce-scatter, 2 all-gather
  reg [15:0] step, sb, rb, nbc_r;
  reg [CW-1:0] cs, cr;
  assign rdata = res[raddr];
  always @(posedge clk) if (lwe) loc[laddr] <= ldata;

  wire sending = (phase != 2'd0) && (sb < nbc_r);
  wire [NBCL2-1:0] sbi = sb[NBCL2-1:0];
  wire [W*8-1:0] sbuf_beat = cur ? buf1[sbi] : buf0[sbi];
  wire [W*8-1:0] loc_cs = loc[{cs, sbi}];

  // ---- receive pipeline: r0 capture (+ local read), 3 adder stages ----
  reg          v0;  reg [W*8-1:0] d0, l0;  reg [15:0] b0;  reg [CW-1:0] c0;  reg ph0;      // ph0: 1 = reduce-scatter
  wire [W*8-1:0] sumbeat;
  genvar k;
  generate
    for (k = 0; k < L; k = k + 1) begin : g_add
      fp32_add_p add (.clk(clk), .a(l0[k*32 +: 32]), .b(d0[k*32 +: 32]), .y(sumbeat[k*32 +: 32]));
    end
  endgenerate
  reg [W*8-1:0] dd1, dd2, dd3;                       // raw incoming beat delayed to match the adder latency (all-gather path)
  reg v1, v2, v3;  reg [15:0] b1, b2, b3;  reg [CW-1:0] c1, c2, c3;  reg p1, p2, p3;
  wire [W*8-1:0] wdata = p3 ? sumbeat : dd3;
  wire step_end = v3 && (b3 == nbc_r - 1);
  wire last_step = (step == C - 2);
  localparam [CW-1:0] CS0 = RANK % C;
  localparam [CW-1:0] CR0 = (RANK + C - 1) % C;
  localparam [CW-1:0] CSAG = (RANK + 1) % C;
  localparam [CW-1:0] CRAG = RANK % C;
  wire [CW-1:0] cs_dec = (cs == 0) ? C - 1 : cs - 1;
  wire [CW-1:0] cr_dec = (cr == 0) ? C - 1 : cr - 1;
  always @(posedge clk) begin
    rout_valid <= 1'b0;
    done <= 1'b0;
    v0 <= 1'b0; v1 <= v0; v2 <= v1; v3 <= v2;
    b1 <= b0; b2 <= b1; b3 <= b2; c1 <= c0; c2 <= c1; c3 <= c2; p1 <= ph0; p2 <= p1; p3 <= p2;
    dd1 <= d0; dd2 <= dd1; dd3 <= dd2;
    if (!rst_n) begin phase <= 2'd0; v0 <= 1'b0; v1 <= 1'b0; v2 <= 1'b0; v3 <= 1'b0; end
    else if (start && phase == 2'd0) begin phase <= 2'd1; step <= 0; sb <= 0; rb <= 0; cur <= 1'b0; nbc_r <= nbc; cs <= CS0; cr <= CR0; end
    else if (phase != 2'd0) begin
      if (sending) begin
        rout_valid <= 1'b1;
        rout_data <= (phase == 2'd1 && step == 0) ? loc_cs : sbuf_beat;
        if (phase == 2'd2 && step == 0) res[{cs, sbi}] <= sbuf_beat;           // keep our own fully reduced chunk
        sb <= sb + 1;
      end
      if (rin_valid) begin                                                      // r0: capture the incoming beat and read our local copy of the same chunk position
        v0 <= 1'b1; d0 <= rin_data; l0 <= loc[{cr, rb[NBCL2-1:0]}]; b0 <= rb; c0 <= cr; ph0 <= (phase == 2'd1);
        rb <= rb + 1;
      end
      if (v3) begin                                                             // write-back (reduce-scatter: the sum; all-gather: the raw data, also kept as the result)
        if (c3 == c3) begin
          if (cur) buf0[b3[NBCL2-1:0]] <= wdata; else buf1[b3[NBCL2-1:0]] <= wdata;
          if (!p3) res[{c3, b3[NBCL2-1:0]}] <= dd3;
        end
      end
      if (step_end) begin
        cur <= ~cur; sb <= 0; rb <= 0;
        if (last_step) begin
          if (phase == 2'd1) begin phase <= 2'd2; step <= 0; cs <= CSAG; cr <= CRAG; end
          else begin phase <= 2'd0; done <= 1'b1; end
        end else begin step <= step + 1; cs <= cs_dec; cr <= cr_dec; end
      end
    end
  end
endmodule

module tpu_ring_ar #(parameter C = 4, parameter W = 16, parameter NBCL2 = 6) (
  input  wire                       clk,
  input  wire                       rst_n,
  input  wire [C-1:0]               lwe,
  input  wire [$clog2(C)+NBCL2-1:0] laddr,
  input  wire [W*8-1:0]             ldata,
  input  wire [$clog2(C)+NBCL2-1:0] raddr,
  output wire [C*W*8-1:0]           rdata,
  input  wire                       start,
  input  wire [15:0]                nbc,
  output wire [C-1:0]               done
);
  wire [C-1:0] rv;
  wire [C*W*8-1:0] rd;
  genvar k;
  generate
    for (k = 0; k < C; k = k + 1) begin : g_node
      localparam PREV = (k + C - 1) % C;
      tpu_ring_ar_node #(.C(C), .RANK(k), .W(W), .NBCL2(NBCL2)) node (
        .clk(clk), .rst_n(rst_n), .lwe(lwe[k]), .laddr(laddr), .ldata(ldata), .raddr(raddr), .rdata(rdata[k*W*8 +: W*8]), .start(start), .nbc(nbc), .done(done[k]),
        .rin_valid(rv[PREV]), .rin_data(rd[PREV*W*8 +: W*8]), .rout_valid(rv[k]), .rout_data(rd[k*W*8 +: W*8]));
    end
  endgenerate
endmodule
