// v8: K independent v7 cores sharing one command and one activation write port; each core owns a weight memory port and the output columns [nt_off(k), nt_off(k)+nt(k)) of the GEMM.
// Weight bandwidth and MAC count therefore scale together (K x N bytes/cycle, K x N^2 MACs). The host splits the column tiles (cmd_nt_vec) and supplies one tile-stream base per core.
// cmd_done pulses once every core has emitted its last output vector. Every core must be given at least one column tile.
module tpu_multi #(parameter N = 16, parameter MMAX = 64, parameter KTMAX = 8, parameter K = 2) (
  input  wire                 clk,
  input  wire                 rst_n,
  input  wire                 cmd_valid,
  output wire                 cmd_ready,
  input  wire [15:0]          cmd_m,
  input  wire [15:0]          cmd_kt,
  input  wire [K*16-1:0]      cmd_nt_vec,
  input  wire [K*32-1:0]      cmd_wbase_vec,
  output reg                  cmd_done,
  input  wire                 abuf_we,
  input  wire [15:0]          abuf_waddr,
  input  wire [N*8-1:0]       abuf_wdata,
  output wire [K-1:0]         mem_req_valid,
  input  wire [K-1:0]         mem_req_ready,
  output wire [K*32-1:0]      mem_req_addr,
  output wire [K*32-1:0]      mem_req_beats,
  input  wire [K-1:0]         mem_rvalid,
  output wire [K-1:0]         mem_rready,
  input  wire [K*N*8-1:0]     mem_rdata,
  output wire [K-1:0]         out_valid,
  output wire [K*16-1:0]      out_nt,
  output wire [K*16-1:0]      out_m,
  output wire [K*N*32-1:0]    out_data
);
  wire [K-1:0] c_ready, c_done;
  assign cmd_ready = &c_ready;
  genvar k;
  generate
    for (k = 0; k < K; k = k + 1) begin : g_core
      tpu_core #(.N(N), .MMAX(MMAX), .KTMAX(KTMAX)) core (
        .clk(clk), .rst_n(rst_n), .cmd_valid(cmd_valid), .cmd_ready(c_ready[k]), .cmd_m(cmd_m), .cmd_kt(cmd_kt), .cmd_nt(cmd_nt_vec[k*16 +: 16]),
        .cmd_wbase(cmd_wbase_vec[k*32 +: 32]), .cmd_done(c_done[k]), .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata),
        .mem_req_valid(mem_req_valid[k]), .mem_req_ready(mem_req_ready[k]), .mem_req_addr(mem_req_addr[k*32 +: 32]), .mem_req_beats(mem_req_beats[k*32 +: 32]),
        .mem_rvalid(mem_rvalid[k]), .mem_rready(mem_rready[k]), .mem_rdata(mem_rdata[k*N*8 +: N*8]),
        .out_valid(out_valid[k]), .out_nt(out_nt[k*16 +: 16]), .out_m(out_m[k*16 +: 16]), .out_data(out_data[k*N*32 +: N*32]));
    end
  endgenerate
  reg [K-1:0] seen;
  always @(posedge clk) begin
    cmd_done <= 1'b0;
    if (!rst_n) seen <= {K{1'b0}};
    else begin
      seen <= seen | c_done;
      if ((seen | c_done) == {K{1'b1}}) begin cmd_done <= 1'b1; seen <= {K{1'b0}}; end
    end
  end
endmodule
