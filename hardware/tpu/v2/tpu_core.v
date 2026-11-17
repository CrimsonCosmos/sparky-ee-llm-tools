// v2 TPU core: same command/memory/stream interface as v1, but the weight fetch engine and the compute engine run decoupled over a shadow-weight array:
// while tile t streams its M activation rows, tile t+1's weights are fetched and written into the shadow registers; tiles issue back to back.
// Tile layout in memory: tiles [nt][kt], rows of a tile in ASCENDING order (row 0 first), N bytes per beat.
module tpu_core #(parameter N = 16, parameter MMAX = 64, parameter KTMAX = 8) (
  input  wire               clk,
  input  wire               rst_n,
  input  wire               cmd_valid,
  output wire               cmd_ready,
  input  wire [15:0]        cmd_m,
  input  wire [15:0]        cmd_kt,
  input  wire [15:0]        cmd_nt,
  input  wire [31:0]        cmd_wbase,
  output reg                cmd_done,
  input  wire               abuf_we,
  input  wire [15:0]        abuf_waddr,
  input  wire [N*8-1:0]     abuf_wdata,
  output reg                mem_req_valid,
  input  wire               mem_req_ready,
  output reg  [31:0]        mem_req_addr,
  output wire [15:0]        mem_req_beats,
  input  wire               mem_rvalid,
  input  wire [N*8-1:0]     mem_rdata,
  output reg                out_valid,
  output reg  [15:0]        out_nt,
  output reg  [15:0]        out_m,
  output reg  [N*32-1:0]    out_data
);
  localparam LAT = 2 * N - 1;
  assign mem_req_beats = N;
  reg [N*8-1:0] abuf [0:KTMAX*MMAX-1];
  always @(posedge clk) if (abuf_we) abuf[abuf_waddr] <= abuf_wdata;

  reg run;
  reg [15:0] m_r, kt_r, nt_r;
  reg [31:0] wbase_r;
  assign cmd_ready = !run;
  wire start_cmd = cmd_valid && !run;
  wire fin;                               // last output vector leaves this cycle

  // shared handshake signals
  reg        sh_valid;                    // shadow holds a fully loaded tile not yet activated
  reg        cs_active;
  reg [15:0] row, c_kt, c_nt;
  reg [31:0] tiles_started;
  wire [31:0] tiles_total = kt_r * nt_r;
  wire       row_last = (row == m_r - 1);
  wire       more     = (tiles_started < tiles_total);
  wire       can_start = sh_valid && !cs_active && run && more;
  wire       chain     = cs_active && row_last && sh_valid && more;
  wire       swap_now  = can_start || chain;          // the next cycle carries row 0 of a new tile (with the swap tag)

  // ---------------- fetch engine ----------------
  localparam F_IDLE = 0, F_REQ = 1, F_LOAD = 2;
  reg [1:0]  fs;
  reg [15:0] f_kt, f_nt, f_beat;
  reg        f_more;
  wire       wload = (fs == F_LOAD) && mem_rvalid;
  always @(posedge clk) begin
    if (!rst_n) begin fs <= F_IDLE; mem_req_valid <= 1'b0; sh_valid <= 1'b0; f_more <= 1'b0; end
    else begin
      if (start_cmd) begin f_more <= 1'b1; f_kt <= 0; f_nt <= 0; sh_valid <= 1'b0; fs <= F_IDLE; end
      else begin
        case (fs)
          F_IDLE: if (run && f_more && !sh_valid && !swap_now) begin
            mem_req_valid <= 1'b1; mem_req_addr <= wbase_r + (f_nt * kt_r + f_kt) * N * N; fs <= F_REQ;
          end
          F_REQ: if (mem_req_ready) begin mem_req_valid <= 1'b0; f_beat <= 0; fs <= F_LOAD; end
          F_LOAD: if (mem_rvalid) begin
            f_beat <= f_beat + 1;
            if (f_beat == N - 1) begin
              sh_valid <= 1'b1; fs <= F_IDLE;
              if (f_kt == kt_r - 1) begin f_kt <= 0; f_nt <= f_nt + 1; if (f_nt == nt_r - 1) f_more <= 1'b0; end else f_kt <= f_kt + 1;
            end
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
        run <= 1'b1; m_r <= cmd_m; kt_r <= cmd_kt; nt_r <= cmd_nt; wbase_r <= cmd_wbase; c_kt <= 0; c_nt <= 0; row <= 0; cs_active <= 1'b0; tiles_started <= 0;
      end else if (run) begin
        if (fin) run <= 1'b0;
        if (cs_active) begin
          if (!row_last) row <= row + 1;
          else begin
            if (c_kt == kt_r - 1) begin c_kt <= 0; c_nt <= c_nt + 1; end else c_kt <= c_kt + 1;
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

  reg [15:0] tag_m [0:LAT-1]; reg tag_f [0:LAT-1]; reg tag_l [0:LAT-1]; reg [15:0] tag_nt [0:LAT-1];
  integer t;
  always @(posedge clk) begin
    tag_m[0] <= row; tag_f[0] <= (c_kt == 0); tag_l[0] <= (c_kt == kt_r - 1); tag_nt[0] <= c_nt;
    for (t = 1; t < LAT; t = t + 1) begin tag_m[t] <= tag_m[t-1]; tag_f[t] <= tag_f[t-1]; tag_l[t] <= tag_l[t-1]; tag_nt[t] <= tag_nt[t-1]; end
  end
  reg [N*32-1:0] acc [0:MMAX-1];
  integer c;
  reg [N*32-1:0] sum;
  always @* begin
    sum = {N*32{1'b0}};
    for (c = 0; c < N; c = c + 1) sum[c*32 +: 32] = (tag_f[LAT-1] ? 32'sd0 : acc[tag_m[LAT-1]][c*32 +: 32]) + y_vec[c*32 +: 32];
  end
  reg [31:0] out_cnt;
  wire [31:0] out_total = m_r * nt_r;
  wire emit = y_valid && tag_l[LAT-1];
  assign fin = emit && (out_cnt + 1 == out_total);
  always @(posedge clk) begin
    out_valid <= 1'b0; cmd_done <= fin;
    if (start_cmd) out_cnt <= 0;
    if (y_valid) begin
      if (tag_l[LAT-1]) begin
        out_valid <= 1'b1; out_nt <= tag_nt[LAT-1]; out_m <= tag_m[LAT-1]; out_data <= sum; out_cnt <= out_cnt + 1;
      end else acc[tag_m[LAT-1]] <= sum;
    end
  end
endmodule
