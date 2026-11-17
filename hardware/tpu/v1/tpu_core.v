// v1 TPU core: one command computes C[M,N] = A[M,K] x W[K,N] (INT8 x INT8 -> INT32, exact) for M <= MMAX with the array tiled over K and N.
//  * Weights stream from external memory (one N-byte beat per cycle at the memory's rate); each N x N tile is stored contiguously, rows in REVERSE order (row N-1 first), tiles ordered [nt][kt].
//  * Activations are read from the on-chip activation buffer ABUF (address kt*MMAX + m, one N-byte K-slice per entry), written by the host before the command.
//  * Results leave on the `out_*` stream, one N-column vector per cycle: (out_nt, out_m, out_data) once all K tiles of that column block are accumulated. The host is assumed to accept one vector per cycle.
// v1 is deliberately naive (single-buffered weights, no overlap between load, stream and drain): the baseline whose efficiency later versions must beat.
module tpu_core #(parameter N = 16, parameter MMAX = 64, parameter KTMAX = 8) (
  input  wire               clk,
  input  wire               rst_n,
  // command
  input  wire               cmd_valid,
  output wire               cmd_ready,
  input  wire [15:0]        cmd_m,               // rows, 1..MMAX
  input  wire [15:0]        cmd_kt,              // K tiles (1..KTMAX)
  input  wire [15:0]        cmd_nt,              // N tiles
  input  wire [31:0]        cmd_wbase,           // byte address of tile (0,0)
  output reg                cmd_done,            // pulse when the last output vector has left
  // activation buffer (host write port)
  input  wire               abuf_we,
  input  wire [15:0]        abuf_waddr,
  input  wire [N*8-1:0]     abuf_wdata,
  // weight memory port
  output reg                mem_req_valid,
  input  wire               mem_req_ready,
  output reg  [31:0]        mem_req_addr,        // byte address of the tile
  output wire [15:0]        mem_req_beats,       // N beats of N bytes
  input  wire               mem_rvalid,
  input  wire [N*8-1:0]     mem_rdata,
  // result stream
  output reg                out_valid,
  output reg  [15:0]        out_nt,
  output reg  [15:0]        out_m,
  output reg  [N*32-1:0]    out_data
);
  localparam LAT = 2 * N - 1;
  assign mem_req_beats = N;
  reg [N*8-1:0] abuf [0:KTMAX*MMAX-1];
  always @(posedge clk) if (abuf_we) abuf[abuf_waddr] <= abuf_wdata;

  reg [2:0] st;
  localparam S_IDLE = 0, S_REQ = 1, S_LOAD = 2, S_STREAM = 3, S_DRAIN = 4, S_FIN = 5;
  reg [15:0] m_r, kt_r, nt_r, nt_i, kt_i, row, beat, drain;
  reg [31:0] wbase_r;
  assign cmd_ready = (st == S_IDLE);

  // array
  wire                 wload = (st == S_LOAD) && mem_rvalid;
  wire [N*8-1:0]       a_vec = abuf[kt_i * MMAX + row];
  wire                 a_valid = (st == S_STREAM);
  wire [N*32-1:0]      y_vec;
  wire                 y_valid;
  tpu_array #(.N(N)) array (.clk(clk), .rst_n(rst_n), .wload(wload), .w_row(mem_rdata), .a_vec(a_vec), .a_valid(a_valid), .y_vec(y_vec), .y_valid(y_valid));

  // tag pipeline: which (m, kt, nt) each output vector belongs to
  reg [15:0] tag_m  [0:LAT-1];
  reg        tag_f  [0:LAT-1];             // first K tile
  reg        tag_l  [0:LAT-1];             // last K tile
  reg [15:0] tag_nt [0:LAT-1];
  integer t;
  always @(posedge clk) begin
    tag_m[0] <= row; tag_f[0] <= (kt_i == 0); tag_l[0] <= (kt_i == kt_r - 1); tag_nt[0] <= nt_i;
    for (t = 1; t < LAT; t = t + 1) begin tag_m[t] <= tag_m[t-1]; tag_f[t] <= tag_f[t-1]; tag_l[t] <= tag_l[t-1]; tag_nt[t] <= tag_nt[t-1]; end
  end

  // accumulators
  reg [N*32-1:0] acc [0:MMAX-1];
  integer c;
  reg [N*32-1:0] sum;
  always @* begin
    sum = {N*32{1'b0}};
    for (c = 0; c < N; c = c + 1) sum[c*32 +: 32] = (tag_f[LAT-1] ? 32'sd0 : acc[tag_m[LAT-1]][c*32 +: 32]) + y_vec[c*32 +: 32];
  end
  reg last_pending;                         // set while the final tile's outputs are still in flight
  always @(posedge clk) begin
    out_valid <= 1'b0;
    if (y_valid) begin
      if (tag_l[LAT-1]) begin out_valid <= 1'b1; out_nt <= tag_nt[LAT-1]; out_m <= tag_m[LAT-1]; out_data <= sum; end
      else acc[tag_m[LAT-1]] <= sum;
    end
  end

  // controller
  always @(posedge clk) begin
    cmd_done <= 1'b0;
    if (!rst_n) begin st <= S_IDLE; mem_req_valid <= 1'b0; end
    else case (st)
      S_IDLE: if (cmd_valid) begin
        m_r <= cmd_m; kt_r <= cmd_kt; nt_r <= cmd_nt; wbase_r <= cmd_wbase; nt_i <= 0; kt_i <= 0; row <= 0; beat <= 0;
        mem_req_valid <= 1'b1; mem_req_addr <= cmd_wbase; st <= S_REQ;
      end
      S_REQ: if (mem_req_ready) begin mem_req_valid <= 1'b0; beat <= 0; st <= S_LOAD; end
      S_LOAD: if (mem_rvalid) begin
        beat <= beat + 1;
        if (beat == N - 1) begin st <= S_STREAM; row <= 0; end
      end
      S_STREAM: begin
        row <= row + 1;
        if (row == m_r - 1) begin st <= S_DRAIN; drain <= 0; end
      end
      S_DRAIN: begin
        drain <= drain + 1;
        if (drain == LAT) begin
          if (kt_i == kt_r - 1 && nt_i == nt_r - 1) st <= S_FIN;
          else begin
            if (kt_i == kt_r - 1) begin kt_i <= 0; nt_i <= nt_i + 1; end else kt_i <= kt_i + 1;
            mem_req_valid <= 1'b1;
            mem_req_addr <= wbase_r + ((kt_i == kt_r - 1) ? ((nt_i + 1) * kt_r) : (nt_i * kt_r + kt_i + 1)) * N * N;
            st <= S_REQ;
          end
        end
      end
      S_FIN: begin cmd_done <= 1'b1; st <= S_IDLE; end
      default: st <= S_IDLE;
    endcase
  end
endmodule
