// v10 TPU core: v9 + timing fixes found with the core-level synthesis/STA check (tools/tpu_synth_sta.py ... core): (1) the accumulator is PIPELINED: stage 1 reads the accumulator row into a register,
// stage 2 adds and writes it back (a row recurs at least N cycles apart, so the 1-cycle read-modify-write distance is hazard free); (2) no runtime multipliers in the control logic: the host supplies the
// total tile count (cmd_tiles = kt*nt) and the total number of output vectors (cmd_outs = m*nt*groups).
// v9 TPU core: v6/v7/v8 + K-GROUP PARTIAL SUMS. The K tiles of a column block are accumulated in groups of cmd_gk tiles (0 = all K); one int32 output vector per (group, column block, row) leaves
// the core with its group index (out_g). The host applies a separate activation/weight scale per group (finer-grained quantisation: tools/tpu_quality_groups.py, +1.60 % -> +0.40 % perplexity).
// v6 TPU core: v5 + zero-bubble tile hand-over: the swap of tile t+1 may be decided in the SAME cycle as its last weight beat (sh_valid_eff), so tiles issue every
// max(M, N) cycles instead of max(M, N+1). Hazard check: the shadow write of PE(i,j) for the next tile happens at >= the swap cycle t0 (ts >= t0 is safe, nonblocking), and the
// last row (i=N-1) is written at e+j while its swap read is at t0+N-1+j = e+N+j.
// v4 TPU core (v3 + pipelined MAC, LAT = 2N):
// v3 TPU core: v2's decoupled fetch/compute over a shadow-weight array, plus ONE streaming memory request per command (all tiles are contiguous) with valid/ready
// backpressure (`mem_rready`), so the per-tile request handshake of v2 disappears and decode runs at the weight-streaming limit of the memory port.
// Tile layout in memory: tiles [nt][kt], rows of a tile in ASCENDING order (row 0 first), N bytes per beat.
module tpu_core #(parameter N = 16, parameter MMAX = 64, parameter KTMAX = 8) (
  input  wire               clk,
  input  wire               rst_n,
  input  wire               cmd_valid,
  output wire               cmd_ready,
  input  wire [15:0]        cmd_m,
  input  wire [15:0]        cmd_kt,
  input  wire [15:0]        cmd_nt,
  input  wire [15:0]        cmd_gk,             // K tiles per group (0 = one group = all K tiles)
  input  wire [15:0]        cmd_ng,
  input  wire [31:0]        cmd_tiles,          // kt * nt (host computed)
  input  wire [31:0]        cmd_outs,           // m * nt * groups (host computed)             // number of groups = ceil(kt / group size) (host computed)
  input  wire [31:0]        cmd_wbase,
  output reg                cmd_done,
  input  wire               abuf_we,
  input  wire [15:0]        abuf_waddr,
  input  wire [N*8-1:0]     abuf_wdata,
  output reg                mem_req_valid,
  input  wire               mem_req_ready,
  output reg  [31:0]        mem_req_addr,
  output wire [31:0]        mem_req_beats,      // whole command: nt*kt*N beats of N bytes, tiles in [nt][kt] order
  input  wire               mem_rvalid,
  output wire               mem_rready,
  input  wire [N*8-1:0]     mem_rdata,
  output reg                out_valid,
  output reg  [15:0]        out_nt,
  output reg  [15:0]        out_m,
  output reg  [15:0]        out_g,
  output reg  [N*32-1:0]    out_data
);
  localparam LAT = 2 * N;
  reg [N*8-1:0] abuf [0:KTMAX*MMAX-1];
  always @(posedge clk) if (abuf_we) abuf[abuf_waddr] <= abuf_wdata;

  reg run;
  reg [15:0] m_r, kt_r, nt_r, gk_r, ng_r;
  reg [31:0] wbase_r, tiles_r, outs_r;
  assign cmd_ready = !run;
  wire start_cmd = cmd_valid && !run;
  wire fin;                               // last output vector leaves this cycle

  // shared handshake signals
  localparam F_IDLE = 0, F_REQ = 1, F_LOAD = 2;
  reg [1:0]  fs;
  reg [15:0] f_beat;
  reg        f_more;
  reg [31:0] f_fetched;
  reg        sh_valid;                    // shadow holds a fully loaded tile not yet activated
  reg        cs_active;
  reg [15:0] row, c_kt, c_nt, c_kg, c_grp;
  reg [31:0] tiles_started;
  wire [31:0] tiles_total = tiles_r;
  assign mem_req_beats = tiles_total * N;
  wire       row_last = (row == m_r - 1);
  wire       more     = (tiles_started < tiles_total);
  assign mem_rready = (fs == F_LOAD) && !sh_valid && f_more;
  wire       wload = mem_rvalid && mem_rready;
  wire       last_beat = wload && (f_beat == N - 1);
  wire       sh_valid_eff = sh_valid || last_beat;
  wire       can_start = sh_valid_eff && !cs_active && run && more;
  wire       chain     = cs_active && row_last && sh_valid_eff && more;
  wire       swap_now  = can_start || chain;          // the next cycle carries row 0 of a new tile (with the swap tag)

  // ---------------- fetch engine ----------------
  always @(posedge clk) begin
    if (!rst_n) begin fs <= F_IDLE; mem_req_valid <= 1'b0; sh_valid <= 1'b0; f_more <= 1'b0; end
    else begin
      if (start_cmd) begin f_more <= 1'b1; f_beat <= 0; f_fetched <= 0; sh_valid <= 1'b0; fs <= F_IDLE; end
      else begin
        case (fs)
          F_IDLE: if (run && f_more) begin mem_req_valid <= 1'b1; mem_req_addr <= wbase_r; fs <= F_REQ; end
          F_REQ: if (mem_req_ready) begin mem_req_valid <= 1'b0; fs <= F_LOAD; end
          F_LOAD: if (wload) begin
            if (f_beat == N - 1) begin
              f_beat <= 0; sh_valid <= !swap_now; f_fetched <= f_fetched + 1;
              if (f_fetched + 1 == tiles_total) begin f_more <= 1'b0; fs <= F_IDLE; end
            end else f_beat <= f_beat + 1;
          end
          default: fs <= F_IDLE;
        endcase
        if (swap_now) sh_valid <= 1'b0;
      end
    end
  end

  // ---------------- compute engine ----------------
  wire       a_valid = cs_active;
  wire       a_first = cs_active && (row == 0);
  wire [N*8-1:0] a_vec = abuf[c_kt * MMAX + row];
  always @(posedge clk) begin
    if (!rst_n) begin cs_active <= 1'b0; run <= 1'b0; end
    else begin
      if (start_cmd) begin
        run <= 1'b1; m_r <= cmd_m; kt_r <= cmd_kt; nt_r <= cmd_nt; wbase_r <= cmd_wbase; tiles_r <= cmd_tiles; outs_r <= cmd_outs; c_kt <= 0; c_nt <= 0; c_kg <= 0; c_grp <= 0; gk_r <= (cmd_gk == 0) ? cmd_kt : cmd_gk; ng_r <= (cmd_gk == 0) ? 16'd1 : cmd_ng; row <= 0; cs_active <= 1'b0; tiles_started <= 0;
      end else if (run) begin
        if (fin) run <= 1'b0;
        if (cs_active) begin
          if (!row_last) row <= row + 1;
          else begin
            if (c_kt == kt_r - 1) begin c_kt <= 0; c_nt <= c_nt + 1; c_kg <= 0; c_grp <= 0; end
            else begin c_kt <= c_kt + 1; if (c_kg == gk_r - 1) begin c_kg <= 0; c_grp <= c_grp + 1; end else c_kg <= c_kg + 1; end
            if (chain) begin row <= 0; tiles_started <= tiles_started + 1; end
            else cs_active <= 1'b0;
          end
        end else if (can_start) begin
          cs_active <= 1'b1; row <= 0; tiles_started <= tiles_started + 1;
        end
      end
    end
  end

  // ---------------- array, tags, accumulators ----------------
  wire [N*32-1:0] y_vec; wire y_valid;
  tpu_array #(.N(N)) array (.clk(clk), .rst_n(rst_n), .wload(wload), .wl_idx(f_beat[$clog2(N)-1:0]), .w_row(mem_rdata), .a_vec(a_vec), .a_valid(a_valid), .a_first(a_first), .y_vec(y_vec), .y_valid(y_valid));

  reg [15:0] tag_m [0:LAT-1]; reg tag_f [0:LAT-1]; reg tag_l [0:LAT-1]; reg [15:0] tag_nt [0:LAT-1]; reg [15:0] tag_g [0:LAT-1];
  integer t;
  always @(posedge clk) begin
    tag_m[0] <= row; tag_f[0] <= (c_kg == 0); tag_l[0] <= (c_kg == gk_r - 1) || (c_kt == kt_r - 1); tag_nt[0] <= c_nt; tag_g[0] <= c_grp;
    for (t = 1; t < LAT; t = t + 1) begin tag_m[t] <= tag_m[t-1]; tag_f[t] <= tag_f[t-1]; tag_l[t] <= tag_l[t-1]; tag_nt[t] <= tag_nt[t-1]; tag_g[t] <= tag_g[t-1]; end
  end
  // ---- pipelined accumulator ----
  reg [N*32-1:0] acc [0:MMAX-1];
  // stage 1: register the array output vector, its tags and the accumulator row it will be added to
  reg              s1_valid, s1_f, s1_l;
  reg [15:0]       s1_m, s1_nt, s1_g;
  reg [N*32-1:0]   s1_y, s1_acc;
  always @(posedge clk) begin
    s1_valid <= y_valid; s1_f <= tag_f[LAT-1]; s1_l <= tag_l[LAT-1]; s1_m <= tag_m[LAT-1]; s1_nt <= tag_nt[LAT-1]; s1_g <= tag_g[LAT-1];
    s1_y <= y_vec; s1_acc <= acc[tag_m[LAT-1]];
    if (!rst_n) s1_valid <= 1'b0;
  end
  // stage 2: add and write back / emit
  integer c;
  reg [N*32-1:0] sum;
  always @* begin
    sum = {N*32{1'b0}};
    for (c = 0; c < N; c = c + 1) sum[c*32 +: 32] = (s1_f ? 32'sd0 : s1_acc[c*32 +: 32]) + s1_y[c*32 +: 32];
  end
  reg [31:0] out_cnt;
  wire emit = s1_valid && s1_l;
  assign fin = emit && (out_cnt + 1 == outs_r);
  always @(posedge clk) begin
    out_valid <= 1'b0; cmd_done <= fin;
    if (start_cmd) out_cnt <= 0;
    if (s1_valid) begin
      if (s1_l) begin
        out_valid <= 1'b1; out_nt <= s1_nt; out_g <= s1_g; out_m <= s1_m; out_data <= sum; out_cnt <= out_cnt + 1;
      end else acc[s1_m] <= sum;
    end
  end
endmodule
