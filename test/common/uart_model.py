"""Reusable UART models for cocotb benches (8N1, LSB-first).

Deliberately decoupled from any one module's tests: ``uart_rx`` uses the
transmitter here now; ``uart.v`` and future integration benches can reuse the
same ``UartTxModel``, and a symmetric ``UartRxModel`` (line sampler) can be
added alongside later.

UART is an identity channel, so a transmitter that bit-bangs a known byte onto
the serial line *is* the golden reference for a receiver: whatever byte the
model sent is what a correct receiver must report.

Timing convention matches ``docs/microarchitecture/uart_rx.md``: baud =
sysclk/(div+1), so each bit level is held for ``div + 1`` sysclk cycles, driven
off the DUT clock via ``ClockCycles``. The line idles high.
"""

from cocotb.triggers import ClockCycles, RisingEdge


class UartTxModel:
    """Drives an 8N1 serial line (idle-high) one frame at a time.

    Parameters
    ----------
    line : the cocotb signal handle to drive (e.g. ``dut.rx``).
    clk  : the DUT clock handle; bit periods are measured in its cycles.
    """

    def __init__(self, line, clk):
        self.line = line
        self.clk = clk

    async def idle(self, div, bits=1):
        """Hold the line in the idle (high) state for ``bits`` bit periods."""
        self.line.value = 1
        await ClockCycles(self.clk, bits * (div + 1))

    async def send_frame(self, byte, div, *, stop=1, glitch=None):
        """Transmit one 8N1 frame, LSB-first.

        start(0) -> d0..d7 (LSB first) -> stop.

        Parameters
        ----------
        byte   : 0..255, the data byte to send.
        div    : UART_DIV; each bit level is held ``div + 1`` sysclk cycles.
        stop   : stop-bit level (1 = well-framed; 0 = force a framing error).
        glitch : ``None`` or a ``(bit_name, level)`` pair injecting a single
                 one-sysclk pulse of ``level`` at the *center* of the named bit.
                 ``bit_name`` is ``"start"``, ``"d0"``..``"d7"``, or ``"stop"``.
                 Models line noise at the exact instant the receiver samples,
                 so a single-sample receiver mis-reads it and a majority-vote
                 receiver does not.
        """
        # Ordered levels for the 10 bit periods of the frame.
        bits = [("start", 0)]
        for i in range(8):
            bits.append((f"d{i}", (byte >> i) & 1))
        bits.append(("stop", stop & 1))

        for name, level in bits:
            self.line.value = level
            period = div + 1
            if glitch is not None and glitch[0] == name:
                # Center of the bit is at cycle (div+1)//2. Drive the nominal
                # level up to the center, flip for exactly one cycle, restore.
                mid = period // 2
                await ClockCycles(self.clk, mid)
                self.line.value = glitch[1]
                await ClockCycles(self.clk, 1)
                self.line.value = level
                await ClockCycles(self.clk, period - mid - 1)
            else:
                await ClockCycles(self.clk, period)

        # Return to idle so back-to-back frames have a clean high gap.
        self.line.value = 1


class UartRxModel:
    """Samples an 8N1 serial line (idle-high) one frame at a time.

    The symmetric partner of ``UartTxModel``: where the transmitter *is* the
    golden reference for a receiver DUT, this sampler *is* the golden reference
    for a transmitter DUT. Whatever byte a correct ``uart_tx`` puts on the wire,
    a correct sampler reads back — no separate golden function is needed.

    Timing convention matches ``docs/microarchitecture/uart_tx.md``: bit period
    = ``div + 1`` sysclk cycles. Sampling anchors on the exact start-bit falling
    edge (idle-high -> low), then samples each bit at its center, so alignment
    has no drift even at small ``div``.

    Parameters
    ----------
    line : the cocotb signal handle to sample (e.g. ``dut.tx``).
    clk  : the DUT clock handle; bit periods are measured in its cycles.
    """

    def __init__(self, line, clk):
        self.line = line
        self.clk = clk

    async def recv_frame(self, div):
        """Wait for one 8N1 frame and return ``(byte, stop)``, LSB-first.

        Blocks until the line falls (start bit), then samples d0..d7 (LSB first)
        and the stop bit at their bit-centers. ``stop`` is the sampled stop-bit
        level (1 = well-framed). Call once per frame; the line is expected to be
        idle-high before the frame begins.
        """
        period = div + 1

        # Anchor on the exact falling edge: the first posedge where the line was
        # high last cycle and is low now is cycle 0 of the start bit.
        prev = int(self.line.value)
        while True:
            await RisingEdge(self.clk)
            cur = int(self.line.value)
            if prev == 1 and cur == 0:
                break
            prev = cur

        # From cycle 0 of the start bit, step to its center, then +period to each
        # data-bit center, then +period to the stop-bit center.
        await ClockCycles(self.clk, period // 2)      # ~ mid start bit
        byte = 0
        for i in range(8):
            await ClockCycles(self.clk, period)
            byte |= (int(self.line.value) & 1) << i   # LSB first
        await ClockCycles(self.clk, period)
        stop = int(self.line.value) & 1
        return byte, stop
