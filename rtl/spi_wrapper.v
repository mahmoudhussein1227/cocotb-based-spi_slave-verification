module spi_wrapper (
    input  wire clk,
    input  wire rst_n,
    input  wire sclk,
    input  wire ss_n,
    input  wire mosi,
    output wire miso
);
    wire       rx_valid, tx_valid;
    wire [9:0] rx_data;
    wire [7:0] tx_data;

    spi_slave u_spi (.clk(clk), .rst_n(rst_n), .sclk(sclk), .ss_n(ss_n),
                     .mosi(mosi), .miso(miso),
                     .rx_valid(rx_valid), .rx_data(rx_data),
                     .tx_valid(tx_valid), .tx_data(tx_data));

    spi_ram  u_ram (.clk(clk), .rst_n(rst_n), .rx_valid(rx_valid),
                    .din(rx_data), .tx_valid(tx_valid), .dout(tx_data));
endmodule
