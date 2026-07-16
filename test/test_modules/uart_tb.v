`default_nettype none
`timescale 1ns / 1ps

/* Testbench for uart.v — the UART register/buffer glue (8N1).

   A single `uart` instance driven through its register-access port
   (access/addr/we/wdata → rdata) and its serial pins. The cocotb test uses the
   reusable models in test/common/uart_model.py: UartRxModel samples `tx`
   (golden reference for transmitted bytes) and UartTxModel drives `rx`
   (received bytes, with stop=0 for framing errors). */
module uart_tb ();

  // Dump the signals to a VCD file. You can view it with gtkwave or surfer.
  initial begin
    $dumpfile("waves/uart_tb.vcd");
    $dumpvars(0, uart_tb);
    #1;
  end

  reg         clk;
  reg         rst_n;
  reg         access;
  reg  [3:0]  addr;
  reg         we;
  reg  [31:0] wdata;
  reg         rx;

  wire [31:0] rdata;
  wire        tx;
  wire        rx_valid_o;

  uart uut (
      .clk        (clk),
      .rst_n      (rst_n),
      .access     (access),
      .addr       (addr),
      .we         (we),
      .wdata      (wdata),
      .rdata      (rdata),
      .tx         (tx),
      .rx         (rx),
      .rx_valid_o (rx_valid_o)
  );

  // Probe for the RX-complete strobe: the collision tests (4a/4b) must align a
  // bus access to the exact cycle uart_rx delivers a byte to the glue.
  wire dbg_rx_strobe = uut.u_rx.strobe;

endmodule
