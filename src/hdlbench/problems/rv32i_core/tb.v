`timescale 1ns/1ps
module tb;
  reg clk = 0, rst_n = 0;
  reg [31:0] imem [0:255]; reg [31:0] dmem [0:1023];
  wire [31:0] imem_addr, dmem_addr, dmem_wdata, imem_rdata, dmem_rdata; wire [3:0] dmem_wstrb;
  assign imem_rdata = imem[imem_addr[9:2]];
  assign dmem_rdata = dmem[dmem_addr[11:2]];
  rv32i_core dut(.clk(clk), .rst_n(rst_n), .imem_addr(imem_addr), .imem_rdata(imem_rdata), .dmem_addr(dmem_addr), .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb), .dmem_rdata(dmem_rdata));
  always #5 clk = ~clk;
  reg [31:0] sa, sd; reg [3:0] ss;
  always @(posedge clk) begin
    if (ss[0]) dmem[sa[11:2]][7:0] <= sd[7:0];
    if (ss[1]) dmem[sa[11:2]][15:8] <= sd[15:8];
    if (ss[2]) dmem[sa[11:2]][23:16] <= sd[23:16];
    if (ss[3]) dmem[sa[11:2]][31:24] <= sd[31:24];
  end
  integer i, fd;
  initial begin
    for (i = 0; i < 1024; i = i + 1) dmem[i] = 32'd0;
    $readmemh("prog.hex", imem);
    fd = $fopen("out.txt", "w");
    ss = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (i = 0; i < `CYCLES; i = i + 1) begin
      @(negedge clk); #1;
      $fdisplay(fd, "%h %h %h %h", imem_addr, dmem_addr, dmem_wdata, dmem_wstrb);
      sa = dmem_addr; sd = dmem_wdata; ss = dmem_wstrb;
      if (^dmem_wstrb === 1'bx) ss = 4'b0000;
      rst_n = (i == 700) ? 1'b0 : 1'b1;          // one reset pulse in the middle of the run: the core must return to pc = 0 and clear its registers at any time
    end
    $fclose(fd); $finish;
  end
endmodule
