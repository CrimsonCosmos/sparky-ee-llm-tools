// v12: inter-chip RING ALL-REDUCE of float32 vectors (reduce-scatter then all-gather) with a float32 adder per lane in every node (fp32_add, bit-exact IEEE-754 binary32).
// C nodes in a ring, node k -> node k+1, W bytes per beat (W/4 float lanes), bulk-synchronous (all nodes start together). The vector is C chunks of `nbc` beats.
//  Reduce-scatter, step s = 0..C-2: node i sends chunk (i-s) mod C (step 0: its own data, later: the partial sum it just built), receives chunk (i-1-s) mod C from its upstream neighbour and adds its own copy.
//    Chunk j therefore accumulates in the fixed order ((x_j + x_{j+1}) + x_{j+2}) + ... + x_{j-1} (mod C); after C-1 steps node i holds the fully reduced chunk (i+1) mod C.
//  All-gather, step t = 0..C-2: node i sends chunk (i+1-t) mod C and stores the chunk (i-t) mod C it receives.
// Every node ends with the identical, deterministic result. Traffic per node: 2 (C-1)/C of the vector, versus (C-1) vectors for an all-gather of the raw partials.
module tpu_ring_ar_node #(parameter C = 4, parameter RANK = 0, parameter W = 16, parameter NBC = 64) (
  input  wire                  clk,
  input  wire                  rst_n,
  input  wire                  lwe,
  input  wire [$clog2(C*NBC)-1:0] laddr,
  input  wire [W*8-1:0]        ldata,
  input  wire [$clog2(C*NBC)-1:0] raddr,
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
  localparam AWV = $clog2(C * NBC);
  reg [W*8-1:0] loc [0:C*NBC-1];
  reg [W*8-1:0] res [0:C*NBC-1];
  reg [W*8-1:0] buf0 [0:NBC-1];
  reg [W*8-1:0] buf1 [0:NBC-1];
  reg cur;
  reg [1:0]  phase;                 // 0 idle, 1 reduce-scatter, 2 all-gather
  reg [15:0] step, sb, rb;
  assign rdata = res[raddr];
  always @(posedge clk) if (lwe) loc[laddr] <= ldata;

  // chunk indices of the current step
  wire [15:0] cs_rs = (RANK + 2 * C - step) % C;
  wire [15:0] cr_rs = (RANK + 2 * C - 1 - step) % C;
  wire [15:0] cs_ag = (RANK + 1 + 2 * C - step) % C;
  wire [15:0] cr_ag = (RANK + 2 * C - step) % C;
  wire [15:0] cs = (phase == 2'd1) ? cs_rs : cs_ag;
  wire [15:0] cr = (phase == 2'd1) ? cr_rs : cr_ag;
  wire sending = (phase != 2'd0) && (sb < nbc);
  wire [W*8-1:0] sbuf_beat = cur ? buf1[sb[$clog2(NBC)-1:0]] : buf0[sb[$clog2(NBC)-1:0]];
  wire [W*8-1:0] loc_cs = loc[cs * NBC + sb];
  wire [W*8-1:0] loc_cr = loc[cr * NBC + rb];
  wire [W*8-1:0] sumbeat;
  genvar k;
  generate
    for (k = 0; k < L; k = k + 1) begin : g_add
      fp32_add add (.a(loc_cr[k*32 +: 32]), .b(rin_data[k*32 +: 32]), .y(sumbeat[k*32 +: 32]));
    end
  endgenerate
  // reduction order: (incoming partial sum) + (local copy); float addition is commutative so the operand order is immaterial
  wire last_step = (step == C - 2);
  wire step_end = rin_valid && (rb == nbc - 1);
  always @(posedge clk) begin
    rout_valid <= 1'b0;
    done <= 1'b0;
    if (!rst_n) phase <= 2'd0;
    else if (start && phase == 2'd0) begin phase <= 2'd1; step <= 0; sb <= 0; rb <= 0; cur <= 1'b0; end
    else if (phase != 2'd0) begin
      if (sending) begin
        rout_valid <= 1'b1;
        rout_data <= (phase == 2'd1 && step == 0) ? loc_cs : sbuf_beat;
        if (phase == 2'd2 && step == 0) res[cs * NBC + sb] <= sbuf_beat;        // keep our own fully reduced chunk
        sb <= sb + 1;
      end
      if (rin_valid) begin
        if (phase == 2'd1) begin
          if (cur) buf0[rb[$clog2(NBC)-1:0]] <= sumbeat; else buf1[rb[$clog2(NBC)-1:0]] <= sumbeat;
        end else begin
          if (cur) buf0[rb[$clog2(NBC)-1:0]] <= rin_data; else buf1[rb[$clog2(NBC)-1:0]] <= rin_data;
          res[cr * NBC + rb] <= rin_data;
        end
        rb <= rb + 1;
      end
      if (step_end) begin
        cur <= ~cur; sb <= 0; rb <= 0;
        if (last_step) begin
          if (phase == 2'd1) begin phase <= 2'd2; step <= 0; end
          else begin phase <= 2'd0; done <= 1'b1; end
        end else step <= step + 1;
      end
    end
  end
endmodule

module tpu_ring_ar #(parameter C = 4, parameter W = 16, parameter NBC = 64) (
  input  wire                       clk,
  input  wire                       rst_n,
  input  wire [C-1:0]               lwe,
  input  wire [$clog2(C*NBC)-1:0]   laddr,
  input  wire [W*8-1:0]             ldata,
  input  wire [$clog2(C*NBC)-1:0]   raddr,
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
      tpu_ring_ar_node #(.C(C), .RANK(k), .W(W), .NBC(NBC)) node (
        .clk(clk), .rst_n(rst_n), .lwe(lwe[k]), .laddr(laddr), .ldata(ldata), .raddr(raddr), .rdata(rdata[k*W*8 +: W*8]), .start(start), .nbc(nbc), .done(done[k]),
        .rin_valid(rv[PREV]), .rin_data(rd[PREV*W*8 +: W*8]), .rout_valid(rv[k]), .rout_data(rd[k*W*8 +: W*8]));
    end
  endgenerate
endmodule
