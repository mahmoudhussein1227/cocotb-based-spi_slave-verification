// version 2 
// Single-port synchronous RAM with the SPI command decoder in front of it.
// One memory port (either a read or a write per cycle). Separate write and
// read address registers: cmd 00 loads wr_addr, cmd 10 loads rd_addr, so an
// aborted or later address frame never disturbs the other address.
module spi_ram #(
    parameter DEPTH  = 256,
    parameter ADDR_W = 8
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       rx_valid,
    input  wire [9:0] din,        // {cmd[1:0], byte[7:0]}
    output reg        tx_valid,
    output reg  [7:0] dout
);
    reg [7:0]        mem [0:DEPTH-1];
    reg [ADDR_W-1:0] wr_addr;
    reg [ADDR_W-1:0] rd_addr;

    integer i;
    initial for (i = 0; i < DEPTH; i = i + 1) mem[i] = 8'h00;  // sim only

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_addr <= 0; rd_addr <= 0; dout <= 0; tx_valid <= 0;
        end else begin
            tx_valid <= 1'b0;
            if (rx_valid) begin
                case (din[9:8])
                    2'b00: wr_addr <= din[ADDR_W-1:0];   // set write address
                    2'b01: mem[wr_addr] <= din[7:0];      // write data
                    2'b10: rd_addr <= din[ADDR_W-1:0];   // set read address
                    2'b11: begin                          // read data
                        dout     <= mem[rd_addr];
                        tx_valid <= 1'b1;
                    end
                endcase
            end
        end
    end
endmodule