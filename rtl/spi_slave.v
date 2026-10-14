// SPI slave, mode 0 (CPOL=0, CPHA=0), MSB first.
// Everything runs in the system clock domain: SCLK/SS_n/MOSI are synchronised
// and SCLK edges are detected, so clk must be >= 8x faster than SCLK (see RTL spec, TOP-003).
//
// Frame (SS_n low):  10 bits in = {cmd[1:0], byte[7:0]}
//   cmd 00 : byte = write address
//   cmd 01 : byte = write data
//   cmd 10 : byte = read  address
//   cmd 11 : byte ignored -> slave then shifts out 8 bits on MISO
module spi_slave (
    input  wire       clk,
    input  wire       rst_n,
    // SPI pins
    input  wire       sclk,
    input  wire       ss_n,
    input  wire       mosi,
    output reg        miso,
    // to RAM
    output reg        rx_valid,
    output reg [9:0]  rx_data,
    // from RAM
    input  wire       tx_valid,
    input  wire [7:0] tx_data
);
    localparam RX = 3'd0, WAIT_TX = 3'd1, TX = 3'd2, DONE = 3'd3;

    reg [2:0] state;
    reg [3:0] rx_cnt;
    reg [8:0] rx_shift;
    reg [7:0] tx_shift;
    reg [2:0] tx_cnt;

    // ---- synchronisers + edge detect ----
    reg [1:0] sclk_s, ss_s, mosi_s;
    reg       sclk_d;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sclk_s <= 2'b00; ss_s <= 2'b11; mosi_s <= 2'b00; sclk_d <= 1'b0;
        end else begin
            sclk_s <= {sclk_s[0], sclk};
            ss_s   <= {ss_s[0],   ss_n};
            mosi_s <= {mosi_s[0], mosi};
            sclk_d <= sclk_s[1];
        end
    end
    wire sclk_rise = ( sclk_s[1] & ~sclk_d);
    wire sclk_fall = (~sclk_s[1] &  sclk_d);
    wire ss_i      = ss_s[1];
    wire mosi_i    = mosi_s[1];

    wire [9:0] word = {rx_shift, mosi_i};   // valid on the 10th rising edge

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= RX; rx_cnt <= 0; rx_shift <= 0; rx_valid <= 0; rx_data <= 0;
            tx_shift <= 0; tx_cnt <= 0; miso <= 1'b0;
        end else begin
            rx_valid <= 1'b0;                 // 1-cycle pulse

            if (ss_i) begin                   // not selected: reset frame
                state <= RX; rx_cnt <= 0; tx_cnt <= 0; miso <= 1'b0;
            end else begin
                case (state)
                    RX: if (sclk_rise) begin
                        rx_shift <= word[8:0];
                        rx_cnt   <= rx_cnt + 1'b1;
                        if (rx_cnt == 4'd9) begin
                            rx_cnt   <= 0;
                            rx_valid <= 1'b1;
                            rx_data  <= word;
                            state    <= (word[9:8] == 2'b11) ? WAIT_TX : DONE;
                        end
                    end

                    WAIT_TX: if (tx_valid) begin   // RAM answered
                        tx_shift <= tx_data;
                        tx_cnt   <= 0;
                        state    <= TX;
                    end

                    TX: if (sclk_fall) begin       // change MISO on falling edge
                        miso     <= tx_shift[7];
                        tx_shift <= {tx_shift[6:0], 1'b0};
                        tx_cnt   <= tx_cnt + 1'b1;
                        if (tx_cnt == 3'd7) state <= DONE;
                    end

                    DONE: ;                        // wait for SS_n to go high
                    default: state <= RX;
                endcase
            end
        end
    end
endmodule
