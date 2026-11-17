// v1 N x N systolic array with input skew and output de-skew inside, so the interface is one activation vector in and one output vector out per cycle.
// A vector presented at cycle t (a_valid) produces its result vector at cycle t + LAT (y_valid), LAT = 2N-1 (checked by the testbench).
module tpu_array #(parameter N = 16) (
  input  wire               clk,
  input  wire               rst_n,
  input  wire               wload,                 // shift weights: row `w_row` enters at the top, existing rows move down
  input  wire [N*8-1:0]     w_row,
  input  wire [N*8-1:0]     a_vec,                 // K-slice of one activation row: element i feeds array row i
  input  wire               a_valid,
  output wire [N*32-1:0]    y_vec,                 // N output columns of one row
  output wire               y_valid
);
  localparam LAT = 2 * N - 1;
  // ---- input skew: row i delayed by i cycles ----
  wire signed [7:0] a_skew [0:N-1];
  genvar i, j;
  generate
    for (i = 0; i < N; i = i + 1) begin : g_skew
      if (i == 0) begin : g0
        assign a_skew[i] = a_vec[7:0];
      end else begin : gd
        reg signed [7:0] d [0:i-1];
        integer k;
        always @(posedge clk) begin
          d[0] <= a_vec[i*8 +: 8];
          for (k = 1; k < i; k = k + 1) d[k] <= d[k-1];
        end
        assign a_skew[i] = d[i-1];
      end
    end
  endgenerate
  // ---- PE grid ----
  wire signed [7:0]  a_w [0:N-1][0:N];             // a_w[i][j] = activation entering PE(i,j); a_w[i][N] unused
  wire signed [7:0]  w_w [0:N][0:N-1];             // w_w[i][j] = weight entering PE(i,j) from above
  wire signed [31:0] p_w [0:N][0:N-1];             // p_w[i][j] = partial sum entering PE(i,j) from above
  generate
    for (i = 0; i < N; i = i + 1) begin : g_row
      assign a_w[i][0] = a_skew[i];
      for (j = 0; j < N; j = j + 1) begin : g_col
        if (i == 0) begin : top
          assign w_w[0][j] = w_row[j*8 +: 8];
          assign p_w[0][j] = 32'sd0;
        end
        tpu_pe pe (.clk(clk), .rst_n(rst_n), .wload(wload), .w_in(w_w[i][j]), .w_out(w_w[i+1][j]), .a_in(a_w[i][j]), .a_out(a_w[i][j+1]), .p_in(p_w[i][j]), .p_out(p_w[i+1][j]));
      end
    end
  endgenerate
  // ---- output de-skew: column j delayed by N-1-j cycles ----
  generate
    for (j = 0; j < N; j = j + 1) begin : g_out
      if (j == N - 1) begin : e0
        assign y_vec[j*32 +: 32] = p_w[N][j];
      end else begin : ed
        reg signed [31:0] d [0:N-2-j];
        integer k;
        always @(posedge clk) begin
          d[0] <= p_w[N][j];
          for (k = 1; k <= N - 2 - j; k = k + 1) d[k] <= d[k-1];
        end
        assign y_vec[j*32 +: 32] = d[N-2-j];
      end
    end
  endgenerate
  // ---- valid pipeline ----
  reg [LAT-1:0] vp;
  always @(posedge clk) begin
    if (!rst_n) vp <= {LAT{1'b0}};
    else vp <= {vp[LAT-2:0], a_valid};
  end
  assign y_valid = vp[LAT-1];
endmodule
