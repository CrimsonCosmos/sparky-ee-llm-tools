// Testbench for tpu_core (any version): reads plusargs +m +kt +nt +bwn +bwd +lat, files dram.hex (N-byte beats), act.hex (ABUF entries, address = kt*MMAX+m), writes out.txt and prints the cycle count.
`timescale 1ns/1ps
module tb_core;
  parameter N = 16, MMAX = 64, KTMAX = 8, DRAM_BEATS = 65536;
  reg clk = 0, rst_n = 0;
  always #5 clk = ~clk;
  reg cmd_valid = 0; wire cmd_ready, cmd_done;
  reg [15:0] cmd_m, cmd_kt, cmd_nt; reg [31:0] cmd_wbase = 0;
  reg abuf_we = 0; reg [15:0] abuf_waddr; reg [N*8-1:0] abuf_wdata;
  wire mem_req_valid; reg mem_req_ready; wire [31:0] mem_req_addr; wire [15:0] mem_req_beats;
  reg mem_rvalid; reg [N*8-1:0] mem_rdata;
  wire out_valid; wire [15:0] out_nt, out_m; wire [N*32-1:0] out_data;
  tpu_core #(.N(N), .MMAX(MMAX), .KTMAX(KTMAX)) dut (.*);
  reg [N*8-1:0] dram [0:DRAM_BEATS-1];
  reg [N*8-1:0] act [0:KTMAX*MMAX-1];
  integer m, kt, nt, bwn, bwd, lat, i, fd, cycles = 0, started = 0;
  // memory model: after a request, wait `lat` cycles, then deliver one beat whenever the credit counter allows (bandwidth = bwn/bwd beats per cycle)
  reg busy = 0; integer wait_c = 0, credit = 0, sent = 0; reg [31:0] base_beat;
  always @(posedge clk) begin
    mem_rvalid <= 0; mem_req_ready <= 0;
    if (mem_req_valid && !busy) begin mem_req_ready <= 1; busy <= 1; wait_c <= lat; credit = 0; sent <= 0; base_beat <= mem_req_addr / (N); end
    else if (busy) begin
      if (wait_c > 0) wait_c <= wait_c - 1;
      else begin
        credit = credit + bwn;
        if (credit >= bwd) begin
          credit = credit - bwd;
          mem_rvalid <= 1; mem_rdata <= dram[base_beat + sent]; sent <= sent + 1;
          if (sent == mem_req_beats - 1) busy <= 0;
        end
      end
    end
    if (started) cycles <= cycles + 1;
  end
  always @(posedge clk) if (out_valid) $fdisplay(fd, "%0d %0d %h", out_nt, out_m, out_data);
  initial begin
    if (!$value$plusargs("m=%d", m)) m = 4; if (!$value$plusargs("kt=%d", kt)) kt = 1; if (!$value$plusargs("nt=%d", nt)) nt = 1;
    if (!$value$plusargs("bwn=%d", bwn)) bwn = 1; if (!$value$plusargs("bwd=%d", bwd)) bwd = 1; if (!$value$plusargs("lat=%d", lat)) lat = 0;
    $readmemh("dram.hex", dram); $readmemh("act.hex", act);
    fd = $fopen("out.txt", "w");
    repeat (3) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);
    for (i = 0; i < kt * MMAX; i = i + 1) begin @(negedge clk); abuf_we = 1; abuf_waddr = i; abuf_wdata = act[i]; end
    @(negedge clk); abuf_we = 0;
    @(negedge clk); cmd_m = m; cmd_kt = kt; cmd_nt = nt; cmd_valid = 1; started = 1;   // stimulus on negedge: no race with the DUT's posedge sampling
    while (!cmd_ready) @(negedge clk);                  // valid is sampled at the next posedge with ready=1 (core idle)
    @(negedge clk); cmd_valid = 0;
    while (!cmd_done) @(posedge clk);
    repeat (5) @(posedge clk);
    $display("CYCLES %0d", cycles);
    $fclose(fd); $finish;
  end
  initial begin #200000000; $display("TIMEOUT"); $finish; end
endmodule
