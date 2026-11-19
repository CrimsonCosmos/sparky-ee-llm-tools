// v11: inter-chip RING all-gather. Each of C chips owns a node; node k sends to node (k+1) mod C and receives from node (k-1) mod C over a W-byte-per-cycle link
// (one beat per cycle, registered: one cycle of link latency per hop). A node first streams its own vector (own-first priority), then forwards, cut-through, the beats it receives from its
// upstream neighbour until each beat has travelled C-1 hops. Every node delivers the other C-1 vectors (tagged with their source rank) to its local consumer, so after the gather every chip
// holds all C vectors and can combine them (e.g. sum the partial outputs of a row-parallel layer in a fixed order, which keeps the result deterministic).
// This engine moves data only (no arithmetic), so the collective is exact for any payload (int32 partial sums, float32 partials, int8 activations).
module tpu_ring_node #(parameter C = 4, parameter RANK = 0, parameter W = 16, parameter SRCW = 8, parameter DEPTH = 1024) (
  input  wire                  clk,
  input  wire                  rst_n,
  input  wire                  lvalid,            // local vector beat (own vector, in order), consumed in the cycle it is valid
  input  wire [W*8-1:0]        ldata,
  input  wire                  rin_valid,
  input  wire [SRCW-1:0]       rin_src,
  input  wire [W*8-1:0]        rin_data,
  output reg                   rout_valid,
  output reg  [SRCW-1:0]       rout_src,
  output reg  [W*8-1:0]        rout_data,
  output reg                   ov,                // a beat of another chip's vector for the local consumer
  output reg  [SRCW-1:0]       osrc,
  output reg  [W*8-1:0]        odata
);
  localparam NEXT = (RANK + 1) % C;
  localparam AW = $clog2(DEPTH) + 1;
  reg [W*8+SRCW-1:0] fifo [0:DEPTH-1];
  reg [AW-1:0] wp, rp;
  wire have = (wp != rp);
  wire fwd = rin_valid && (rin_src != NEXT);      // the next node is the originator: the beat has completed its trip
  always @(posedge clk) begin
    rout_valid <= 1'b0; ov <= 1'b0;
    if (!rst_n) begin wp <= 0; rp <= 0; end
    else begin
      if (lvalid) begin rout_valid <= 1'b1; rout_src <= RANK; rout_data <= ldata; end
      else if (have) begin rout_valid <= 1'b1; {rout_src, rout_data} <= fifo[rp[AW-2:0]]; rp <= rp + 1; end
      if (rin_valid) begin ov <= 1'b1; osrc <= rin_src; odata <= rin_data; end
      if (fwd) begin fifo[wp[AW-2:0]] <= {rin_src, rin_data}; wp <= wp + 1; end
    end
  end
endmodule

module tpu_ring #(parameter C = 4, parameter W = 16, parameter DEPTH = 1024) (
  input  wire                  clk,
  input  wire                  rst_n,
  input  wire [C-1:0]          lvalid,
  input  wire [C*W*8-1:0]      ldata,
  output wire [C-1:0]          ov,
  output wire [C*8-1:0]        osrc,
  output wire [C*W*8-1:0]      odata
);
  wire [C-1:0] rv;
  wire [C*8-1:0] rs;
  wire [C*W*8-1:0] rd;
  genvar k;
  generate
    for (k = 0; k < C; k = k + 1) begin : g_node
      localparam PREV = (k + C - 1) % C;
      tpu_ring_node #(.C(C), .RANK(k), .W(W), .DEPTH(DEPTH)) node (
        .clk(clk), .rst_n(rst_n), .lvalid(lvalid[k]), .ldata(ldata[k*W*8 +: W*8]),
        .rin_valid(rv[PREV]), .rin_src(rs[PREV*8 +: 8]), .rin_data(rd[PREV*W*8 +: W*8]),
        .rout_valid(rv[k]), .rout_src(rs[k*8 +: 8]), .rout_data(rd[k*W*8 +: W*8]),
        .ov(ov[k]), .osrc(osrc[k*8 +: 8]), .odata(odata[k*W*8 +: W*8]));
    end
  endgenerate
endmodule
