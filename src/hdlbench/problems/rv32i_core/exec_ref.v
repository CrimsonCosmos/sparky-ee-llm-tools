module rv32i_exec(input [31:0] instr, input [31:0] pc, input [31:0] rs1, input [31:0] rs2, input [31:0] mem_rdata,
  output reg rd_we, output reg [31:0] rd_val, output reg [31:0] next_pc, output reg [31:0] mem_addr, output reg [31:0] mem_wdata,
  output reg [3:0] mem_wstrb, output reg illegal, output reg misaligned);
  wire [6:0] op = instr[6:0]; wire [2:0] f3 = instr[14:12]; wire [6:0] f7 = instr[31:25];
  wire [31:0] immI = {{20{instr[31]}}, instr[31:20]}, immS = {{20{instr[31]}}, instr[31:25], instr[11:7]};
  wire [31:0] immB = {{19{instr[31]}}, instr[31], instr[7], instr[30:25], instr[11:8], 1'b0};
  wire [31:0] immU = {instr[31:12], 12'b0}, immJ = {{11{instr[31]}}, instr[31], instr[19:12], instr[20], instr[30:21], 1'b0};
  wire signed [31:0] s1 = rs1, s2 = rs2;
  wire [31:0] pc4 = pc + 32'd4;
  wire [31:0] sra_r = s1 >>> rs2[4:0];      // computed on its own: inside ?: with an unsigned operand the shift would silently become logical
  wire [31:0] srai_r = s1 >>> instr[24:20];
  reg [31:0] b, h, shifted; reg lt_s, lt_u, eq, taken;
  reg [31:0] addr;
  always @* begin
    rd_we = 0; rd_val = 32'bx; next_pc = pc4; mem_addr = 32'bx; mem_wdata = 32'bx; mem_wstrb = 4'b0; illegal = 0; misaligned = 0;
    lt_s = (s1 < s2); lt_u = (rs1 < rs2); eq = (rs1 == rs2); taken = 0; addr = 0; b = 0; h = 0; shifted = 0;
    if (op[1:0] != 2'b11) illegal = 1;
    else case (op[6:2])
      5'b01101: begin rd_we = 1; rd_val = immU; end
      5'b00101: begin rd_we = 1; rd_val = pc + immU; end
      5'b11011: begin rd_we = 1; rd_val = pc4; next_pc = pc + immJ; end
      5'b11001: if (f3 == 3'b000) begin rd_we = 1; rd_val = pc4; next_pc = (rs1 + immI) & 32'hFFFFFFFE; end else illegal = 1;
      5'b11000: begin
        case (f3)
          3'b000: taken = eq; 3'b001: taken = !eq; 3'b100: taken = lt_s; 3'b101: taken = !lt_s; 3'b110: taken = lt_u; 3'b111: taken = !lt_u;
          default: illegal = 1;
        endcase
        if (!illegal && taken) next_pc = pc + immB;
      end
      5'b00000: begin
        addr = rs1 + immI; mem_addr = addr; b = mem_rdata >> (8 * addr[1:0]); h = mem_rdata >> (8 * addr[1:0]);
        case (f3)
          3'b000: begin rd_we = 1; rd_val = {{24{b[7]}}, b[7:0]}; end
          3'b100: begin rd_we = 1; rd_val = {24'b0, b[7:0]}; end
          3'b001: if (addr[0]) begin misaligned = 1; rd_val = 32'bx; end else begin rd_we = 1; rd_val = {{16{h[15]}}, h[15:0]}; end
          3'b101: if (addr[0]) begin misaligned = 1; rd_val = 32'bx; end else begin rd_we = 1; rd_val = {16'b0, h[15:0]}; end
          3'b010: if (addr[1:0] != 0) misaligned = 1; else begin rd_we = 1; rd_val = mem_rdata; end
          default: illegal = 1;
        endcase
        if (illegal) mem_addr = 32'bx;
        if (misaligned) rd_we = 0;
      end
      5'b01000: begin
        addr = rs1 + immS; mem_addr = addr;
        case (f3)
          3'b000: begin mem_wstrb = 4'b0001 << addr[1:0]; end
          3'b001: if (addr[0]) misaligned = 1; else mem_wstrb = 4'b0011 << addr[1:0];
          3'b010: if (addr[1:0] != 0) misaligned = 1; else mem_wstrb = 4'b1111;
          default: illegal = 1;
        endcase
        if (illegal) mem_addr = 32'bx;
        else if (!misaligned) mem_wdata = rs2 << (8 * addr[1:0]);
      end
      5'b00100: begin
        rd_we = 1;
        case (f3)
          3'b000: rd_val = rs1 + immI; 3'b010: rd_val = {31'b0, s1 < $signed(immI)}; 3'b011: rd_val = {31'b0, rs1 < immI};
          3'b100: rd_val = rs1 ^ immI; 3'b110: rd_val = rs1 | immI; 3'b111: rd_val = rs1 & immI;
          3'b001: if (f7 == 7'b0000000) rd_val = rs1 << instr[24:20]; else illegal = 1;
          3'b101: if (f7 == 7'b0000000) rd_val = rs1 >> instr[24:20]; else if (f7 == 7'b0100000) rd_val = srai_r; else illegal = 1;
        endcase
        if (illegal) begin rd_we = 0; rd_val = 32'bx; end
      end
      5'b01100: begin
        rd_we = 1;
        if (f7 != 7'b0000000 && !(f7 == 7'b0100000 && (f3 == 3'b000 || f3 == 3'b101))) illegal = 1;
        else case (f3)
          3'b000: rd_val = (f7 == 7'b0100000) ? rs1 - rs2 : rs1 + rs2;
          3'b001: rd_val = rs1 << rs2[4:0]; 3'b010: rd_val = {31'b0, lt_s}; 3'b011: rd_val = {31'b0, lt_u};
          3'b100: rd_val = rs1 ^ rs2; 3'b101: rd_val = (f7 == 7'b0100000) ? sra_r : (rs1 >> rs2[4:0]);
          3'b110: rd_val = rs1 | rs2; 3'b111: rd_val = rs1 & rs2;
        endcase
        if (illegal) begin rd_we = 0; rd_val = 32'bx; end
      end
      default: illegal = 1;
    endcase
    if (illegal) begin rd_we = 0; mem_wstrb = 0; misaligned = 0; next_pc = pc4; rd_val = 32'bx; mem_addr = 32'bx; mem_wdata = 32'bx; end
    if (!rd_we) rd_val = 32'bx;
    if (mem_wstrb == 0) mem_wdata = 32'bx;
  end
endmodule
