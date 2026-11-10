module rv32i_core(input clk, input rst_n, output [31:0] imem_addr, input [31:0] imem_rdata, output [31:0] dmem_addr, output [31:0] dmem_wdata, output [3:0] dmem_wstrb, input [31:0] dmem_rdata);
  reg [31:0] pc; reg [31:0] x [0:31];
  integer k;
  wire [4:0] ra = imem_rdata[19:15], rb = imem_rdata[24:20], rd = imem_rdata[11:7];
  wire [31:0] v1 = (ra == 0) ? 32'd0 : x[ra], v2 = (rb == 0) ? 32'd0 : x[rb];
  wire we, ill, mis; wire [31:0] rdv, npc, ma, mw; wire [3:0] ms;
  rv32i_exec ex(.instr(imem_rdata), .pc(pc), .rs1(v1), .rs2(v2), .mem_rdata(dmem_rdata), .rd_we(we), .rd_val(rdv), .next_pc(npc), .mem_addr(ma), .mem_wdata(mw), .mem_wstrb(ms), .illegal(ill), .misaligned(mis));
  assign imem_addr = pc, dmem_addr = ma, dmem_wdata = mw, dmem_wstrb = ms;
  always @(posedge clk) begin
    if (!rst_n) begin pc <= 0; for (k = 0; k < 32; k = k + 1) x[k] <= 0; end
    else begin pc <= npc; if (we && rd != 0) x[rd] <= rdv; end
  end
endmodule
