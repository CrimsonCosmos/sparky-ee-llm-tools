// v2 N x N systolic array, shadow-weight (double-buffered) version. Shadow rows are written in ascending row order, one row per cycle (`wload`, `wl_idx`, `w_row`);
// byte j of the row is delayed j cycles (column skew) and the row-select travels along the row, so the shadow write of PE(i,j) happens at ts+i+j, after that PE's swap
// for the previous tile (t0+i+j, t0 < ts) and before the swap for the next one. `a_first` marks the first activation row of a tile and triggers the swap.
module tpu_array #(parameter N = 16) (
  input  wire               clk,
  input  wire               rst_n,
  input  wire               wload,
  input  wire [$clog2(N)-1:0] wl_idx,
  input  wire [N*8-1:0]     w_row,
  input  wire [N*8-1:0]     a_vec,
  input  wire               a_valid,
  input  wire               a_first,
  output wire [N*32-1:0]    y_vec,
  output wire               y_valid
);
  // local registered copy of the reset: the array's control flops use rst_q so the external reset drives a single flop (short reset path)
  reg rst_q;
  always @(posedge clk) rst_q <= rst_n;
  localparam LAT = 2 * N;       // v3's 2N-1 plus the product register
  localparam PW = 16 + $clog2(N);  // exact width of a sum of N signed 8x8 products
  genvar i, j;
  // input skew for activation + swap tag
  wire signed [7:0] a_skew [0:N-1];
  wire              s_skew [0:N-1];
  generate
    for (i = 0; i < N; i = i + 1) begin : g_skew
      if (i == 0) begin : g0
        assign a_skew[i] = a_vec[7:0];
        assign s_skew[i] = a_first && a_valid;
      end else begin : gd
        reg signed [7:0] d [0:i-1];
        reg              s [0:i-1];
        integer k;
        always @(posedge clk) begin
          d[0] <= a_vec[i*8 +: 8]; s[0] <= a_first && a_valid;
          for (k = 1; k < i; k = k + 1) begin d[k] <= d[k-1]; s[k] <= s[k-1]; end
          if (!rst_q) for (k = 0; k < i; k = k + 1) s[k] <= 1'b0;
        end
        assign a_skew[i] = d[i-1];
        assign s_skew[i] = s[i-1];
      end
    end
  endgenerate
  // shadow data column skew: column j delayed j cycles
  wire signed [7:0] wd_col [0:N-1];
  generate
    for (j = 0; j < N; j = j + 1) begin : g_wskew
      if (j == 0) begin : w0
        assign wd_col[j] = w_row[7:0];
      end else begin : wd
        reg signed [7:0] d [0:j-1];
        integer k;
        always @(posedge clk) begin
          d[0] <= w_row[j*8 +: 8];
          for (k = 1; k < j; k = k + 1) d[k] <= d[k-1];
        end
        assign wd_col[j] = d[j-1];
      end
    end
  endgenerate
  // PE grid
  wire signed [7:0]  a_w [0:N-1][0:N];
  wire               l_w [0:N-1][0:N];
  wire               s_w [0:N-1][0:N];
  wire signed [PW-1:0] p_w [0:N][0:N-1];
  generate
    for (i = 0; i < N; i = i + 1) begin : g_row
      assign a_w[i][0] = a_skew[i];
      assign s_w[i][0] = s_skew[i];
      assign l_w[i][0] = wload && (wl_idx == i);
      for (j = 0; j < N; j = j + 1) begin : g_col
        if (i == 0) begin : top
          assign p_w[0][j] = {PW{1'b0}};
        end
        tpu_pe #(.PW(PW)) pe (.clk(clk), .rst_n(rst_q), .wl_in(l_w[i][j]), .wd_in(wd_col[j]), .wl_out(l_w[i][j+1]),
                   .swap_in(s_w[i][j]), .swap_out(s_w[i][j+1]), .a_in(a_w[i][j]), .a_out(a_w[i][j+1]), .p_in(p_w[i][j]), .p_out(p_w[i+1][j]));
      end
    end
  endgenerate
  // output de-skew
  generate
    for (j = 0; j < N; j = j + 1) begin : g_out
      if (j == N - 1) begin : e0
        assign y_vec[j*32 +: 32] = {{(32-PW){p_w[N][j][PW-1]}}, p_w[N][j]};
      end else begin : ed
        reg signed [PW-1:0] d [0:N-2-j];
        integer k;
        always @(posedge clk) begin
          d[0] <= p_w[N][j];
          for (k = 1; k <= N - 2 - j; k = k + 1) d[k] <= d[k-1];
        end
        assign y_vec[j*32 +: 32] = {{(32-PW){d[N-2-j][PW-1]}}, d[N-2-j]};
      end
    end
  endgenerate
  reg [LAT-1:0] vp;
  always @(posedge clk) begin
    if (!rst_q) vp <= {LAT{1'b0}};
    else vp <= {vp[LAT-2:0], a_valid};
  end
  assign y_valid = vp[LAT-1];
endmodule
