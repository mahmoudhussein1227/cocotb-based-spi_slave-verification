"""Author: Mahmoud Hussein
Project: Cocotb-Based SPI Slave and Single-Port RAM Verification
Description: Test an SPI slave and its single-port RAM using Cocotb.
"""

import os
import cocotb
from cocotb.triggers import RisingEdge, FallingEdge, Timer , gather , select
from cocotb.queue import Queue , QueueEmpty
from cocotb.types import LogicArray, Range
from enum import Enum
import random
from cocotb_coverage.coverage import CoverPoint , CoverCross , coverage_section , coverage_db

_last_accepted_cmd = None


def cmd_transition(addr, cmd, abort):
    """Return the previous/current command pair for completed transactions."""
    global _last_accepted_cmd
    if abort:
        return None

    current_cmd = int(cmd)
    if _last_accepted_cmd is None:
        _last_accepted_cmd = current_cmd
        return None

    transition = (_last_accepted_cmd, current_cmd)
    _last_accepted_cmd = current_cmd
    return transition


def reset_cmd_transition():
    """Clear command history so transitions do not span separate tests."""
    global _last_accepted_cmd
    _last_accepted_cmd = None


# Command labels and all possible previous-to-current command transitions.
cmd_names = ("WRITE_ADDR", "WRITE_DATA", "READ_ADDR", "READ_DATA")
cmd_transition_bins = [
    (previous_cmd, current_cmd)
    for previous_cmd in range(4)
    for current_cmd in range(4)
]
cmd_transition_labels = [
    f"{cmd_names[previous_cmd]} -> {cmd_names[current_cmd]}"
    for previous_cmd, current_cmd in cmd_transition_bins
]

# Functional coverage for addresses, commands, aborts, and command transitions.
@coverage_section(
    CoverPoint(
            name="spi.addr",
            vname="addr",
            xf= lambda addr , cmd , abort : int(addr) if int(cmd) in [0 , 2] else None, # samples only when the read/write addr cmd is delivered
            bins= [x for x in range(256)]
        ),
    CoverPoint(
            name="spi.command",
            vname="cmd",
            xf= lambda addr, cmd , abort: int(cmd) ,
            bins= [0 , 1 , 2 , 3],
            bins_labels=list(cmd_names) 
        ),
    CoverPoint(
            name="spi.abort",
            vname="abort",
            bins=[True , False],
            bins_labels=["ABORTED" , "COMPLETED"]
        ),
    CoverPoint(
            name="spi.cmd_transitions",
            xf=cmd_transition,
            bins=cmd_transition_bins,
            bins_labels=cmd_transition_labels,
        ),
    CoverCross(
            name="spi.addr_cmd_cross",
            items=["spi.addr" , "spi.command"],
            ign_bins=[(None , "WRITE_DATA") , (None , "READ_DATA")]
    ),
    CoverCross(
            name="spi.cmd_abort_cross",
            items=["spi.command" , "spi.abort"],
            #ign_bins=[(None , 1) , (None , 3)]
    )
    
)
def sample_mem(addr , cmd , abort):
    """Sample one observed transaction in the coverage model."""
    pass


# Testbench timing parameters, expressed in nanoseconds.
T_CLK = 10
T_FREE = 500
# Number of system-clock cycles the driver waits for queued work.
TIME_OUT = 10

# Software reference RAM: 256 byte locations, initially cleared.
ram = {}
for i in range(256):
    ram[i] = 0


async def gen_clk(dut):
    """Generate the free-running system clock used by the DUT."""
    while True:
        dut.clk.value = 0
        await Timer(T_CLK /2 , "ns")
        dut.clk.value = 1
        await Timer(T_CLK /2 , "ns")


class spi_cmd(Enum):
    """The four two-bit SPI command encodings."""

    WRITE_ADDR = LogicArray("00" , Range(1 , "downto" , 0))
    WRITE_DATA = LogicArray("01" , Range(1 , "downto" , 0))
    READ_ADDR  = LogicArray("10" , Range(1 , "downto" , 0))
    READ_DATA  = LogicArray("11" , Range(1 , "downto" , 0))


class spi_transaction:
    """A command, its associated byte, and transaction control options."""

    def __init__(self ):
        """Create a transaction with default command, data, and control values."""
        self.cmd  =  LogicArray("00" , Range(1 , "downto" , 0))
        # For READ_DATA, data is the byte expected from the slave.
        self.data =  LogicArray("00000000" , Range(7 , "downto" , 0))
        # True requests that the driver deassert chip select before completion.
        self.abort = False
        # True adds extra SCLK cycles after the command and data.
        self.extended = False

    def randomize(self):
        """Choose random command, byte, abort, and extended-transfer values."""
        self.cmd    =  LogicArray(random.randint(0  , 3) , Range(1 , "downto" , 0))
        self.data   = LogicArray(random.randint(0 , 255) , Range(7 , "downto" , 0))
        self.abort  = random.choice([True , False])
        self.extended = random.choice([True , False])

    def convert2string(self):
        """Format the command and data byte for the simulation log."""
        result = f"command is {spi_cmd(self.cmd)} , data  : {self.data}"
        return result


class spi_master_driver:
    """Drive SPI transactions onto the DUT pins."""

    def __init__(self ,dut):
        """Keep the DUT handle and create the driver's pending-item queue."""
        self.dut = dut
        self.pending_items = Queue()

    async def drive_sclk(self):
        """Continuously generate SCLK at one tenth of the system-clock rate."""
        while True:
            self.dut.sclk.value = 0
            await Timer((T_CLK * 10)/2 , "ns")
            self.dut.sclk.value = 1
            await Timer((T_CLK * 10)/2 , "ns")

    async def wait_cycles(self , num_cycles : int):
        """Wait for the requested number of rising system-clock edges."""
        dut = self.dut
        for _ in range(num_cycles):
            await RisingEdge(dut.clk)

    async def drv_abort(self , access_type):
        """End an active transaction at a randomized SCLK bit position."""
        dut = self.dut
        while dut.ss_n.value == 0 :
            if access_type == spi_cmd.READ_DATA.value:
                # Allow the abort point to fall within the read response phase.
                rand_bit_pos = random.randint(11,17)
            else:
                rand_bit_pos = random.randint(1,9)
            for _ in range(rand_bit_pos):
                await FallingEdge(dut.sclk)
            dut.ss_n.value = 1
            break

    async def run(self):
        """Drive queued transactions and stop after the queue stays empty."""
        while True :
            # Race waiting for another item against the configured idle timeout.
            result = await select(self.pending_items.get() , self.wait_cycles(TIME_OUT))
            if result[0] == 1:
                break
            dut  = self.dut
            # The queue-wait coroutine returns the transaction as its result.
            item = result[1]
            string = "driving : " + item.convert2string()
            cocotb.log.info(string)
            # Assert chip select, then start the independent SCLK generator.
            dut.ss_n.value = 0
            await self.wait_cycles(2)
            drv_sclk_task = cocotb.start_soon(self.drive_sclk())

            # Run the abort coroutine concurrently when this item requests it.
            if item.abort:
                drive_abort = cocotb.start_soon(self.drv_abort(item.cmd))

            # Shift the command and data bytes onto MOSI, most-significant bit first.
            for i in range(len(item.cmd) - 1, -1, -1):
                dut.mosi.value = item.cmd[i]
                await FallingEdge(dut.sclk)
            for i in range(len(item.data) - 1, -1, -1):
                dut.mosi.value = item.data[i]
                await FallingEdge(dut.sclk)
            # READ_DATA needs eight additional clocks for the slave's response.
            if item.cmd == spi_cmd.READ_DATA.value:
                for _ in range(8):
                    await FallingEdge(dut.sclk)

            # Optionally extend the transaction before returning pins to idle.
            if item.extended : 
                for _ in range(5):
                    await FallingEdge(dut.sclk)
            drv_sclk_task.cancel()
            dut.sclk.value = 0
            dut.mosi.value = 0
            dut.ss_n.value = 1
            await Timer(T_FREE , "ns")


class spi_monitor:
    """Observe the SPI pins and send completed transactions to the model."""

    def __init__(self , dut ):
        """Store the DUT handle and create a default software model."""
        self.dut = dut
        self.model = model()

    async def wait_transaction_start(self):
        """Wait for chip select to become active (low)."""
        dut = self.dut
        if dut.ss_n.value == 1:
            await FallingEdge(dut.ss_n)

    async def collect_transaction(self , item):
        """Decode the command and capture its data or readback byte."""
        dut = self.dut
        # Commands are transmitted most-significant bit first.
        for i in range(2):
            await RisingEdge(dut.sclk)
            item.cmd[1-i] = dut.mosi.value

        if item.cmd == spi_cmd.READ_DATA.value:
            # Ignore the master's dummy byte before sampling the slave response.
            for _ in range(8):
                await RisingEdge(dut.sclk)
            # Sample MISO shortly after each rising SCLK edge.
            for i in range(8):
                await RisingEdge(dut.sclk)
                await Timer(1 , "ns")
                item.data[7-i] = dut.miso.value
        else :# For write/address commands, capture the master's MOSI data byte.
            for i in range(8):
                await RisingEdge(dut.sclk)
                item.data[7-i] = dut.mosi.value
        cocotb.log.info(f"item Monitored : {item.convert2string()}")

    async def wait_transaction_end(self):
        """Wait for chip select to return high if it is still active."""
        dut = self.dut
        if dut.ss_n.value != 1:
            await RisingEdge(dut.ss_n)

    async def wait_cycles(self , num_cycles):
        """Wait for the requested number of rising system-clock edges."""
        dut = self.dut
        for _ in range(num_cycles):
            await RisingEdge(dut.clk)

    async def run(self):
        """Monitor transactions until the bus remains idle past its timeout."""
        while True:
            item = spi_transaction()
            res = await select(self.wait_transaction_start() , Timer(6*T_FREE , "ns"))
            if res[0] == 1:
                cocotb.log.info("bus is idle for more than 10 clock cycles")
                break
            else:
                # If chip select rises before sampling finishes, discard this item.
                result  = await select(self.collect_transaction(item) , self.wait_transaction_end())
                if result[0] == 1:
                    item.abort = True
                    cocotb.log.info("Transaction aborted and the command is discarded")
                else:
                    # Queue and apply only fully collected transactions.
                    await self.model.mon_queue.put(item)
                    await self.model.run()
                    await self.wait_transaction_end()
                    cocotb.log.info("Transaction is collected completely")

                # Include aborted as well as completed transactions in coverage.
                sample_mem(item.data , item.cmd , item.abort)


class model :
    """Predict SPI RAM behavior and check bytes returned by the DUT."""

    def __init__(self):
        """Initialize the remembered write/read addresses and input queue."""
        self.write_addr = 0
        self.read_addr  = 0
        self.mon_queue  = Queue()

    async def run(self):
        """Apply one monitored command to the software RAM reference model."""
        output_data = 0
        item_mon = await self.mon_queue.get()
        if item_mon.cmd == spi_cmd.WRITE_ADDR.value:
            self.write_addr = item_mon.data
        elif item_mon.cmd == spi_cmd.WRITE_DATA.value:
            ram[int(self.write_addr)] = item_mon.data
        elif item_mon.cmd == spi_cmd.READ_ADDR.value:
            self.read_addr = item_mon.data
        elif item_mon.cmd == spi_cmd.READ_DATA.value:
            output_data = ram[int(self.read_addr)]
            # Compare the DUT's serial readback against the modeled RAM byte.
            assert output_data == item_mon.data, (
                f"data read from RAM address {self.read_addr} is incorrect: "
                f"expected {output_data}, got {item_mon.data}"
            )

    async def model_initialized(self):
        """Reset the model's remembered addresses after a DUT reset."""
        self.write_addr = 0
        self.read_addr  = 0
        

async def reset(dut):
    """Apply active-low reset and verify MISO remains low during reset."""
    reset_cmd_transition()
    dut.rst_n.value = 1
    await Timer(10 , "ns")
    dut.rst_n.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
        assert dut.miso.value == 0 , (f"the miso must be 0 after the reset got({dut.miso.value})")
    dut.rst_n.value = 1
    for _ in range(3):
        await RisingEdge(dut.clk)


async def wait_sclk_cycles(dut , num_cycles):
    """Wait for the requested number of falling SCLK edges."""
    for _ in range(num_cycles):
        await FallingEdge(dut.sclk)


async def end_transaction(dut):
    """Wait until chip select is deasserted."""
    while True:
        await RisingEdge(dut.ss_n)
        break


async def insert_reset(dut):
    """Assert reset at a random point during an active SPI transaction."""
    while dut.ss_n.value != 0:
        await RisingEdge(dut.clk)
    reset_pos = random.randint(1 , 9)
    for _ in range(reset_pos):
        await RisingEdge(dut.sclk)

    await reset(dut)

@cocotb.test()
async def test_random(dut):
    """Run a long sequence of randomized commands and transaction options."""
    num_transactions = 1000
    cocotb.start_soon(gen_clk(dut))

    # Connect the driver, monitor, and a shared reference model.
    driver        = spi_master_driver(dut)
    monitor       = spi_monitor(dut)
    slave_model   = model()
    monitor.model = slave_model

    # Begin from idle bus levels and reset the DUT before testing.
    dut.ss_n.value = 1
    dut.sclk.value = 0
    dut.mosi.value = 0
    await reset(dut)
    for _ in range(num_transactions):
        item = spi_transaction()
        item.randomize()
        await driver.pending_items.put(item)

    # Run pin-level driving and monitoring concurrently.
    await gather(driver.run() , monitor.run())

    await Timer(T_FREE , "ns")

    cocotb.log.info("END OF THE TEST")


@cocotb.test()
async def test_write_read_random_abort(dut):
    """Write and read every RAM address, randomly aborting each command type."""
    ram_depth = 256
    abort_transaction = False
    cocotb.start_soon(gen_clk(dut))

    driver = spi_master_driver(dut)
    monitor = spi_monitor(dut)
    slave_model = model()
    monitor.model = slave_model

    dut.ss_n.value = 1
    dut.sclk.value = 0
    dut.mosi.value = 0
    await reset(dut)

    # Exercise every RAM location with a reproducible, address-based data byte.
    for address in range(ram_depth):
        data_to_write = (address * 37 + 0xA5) & 0xFF
        for command, data in (
            (spi_cmd.WRITE_ADDR, address),
            (spi_cmd.WRITE_DATA, data_to_write),
            (spi_cmd.READ_ADDR, address),
            (spi_cmd.READ_DATA, 0),
        ):
            # Randomize abort behavior while keeping command and data predictable.
            abort_transaction = random.choice([True, False])
            item = spi_transaction()
            item.abort = abort_transaction
            item.cmd = command.value
            item.data = LogicArray(data, Range(7, "downto", 0))
            await driver.pending_items.put(item)

    await gather(driver.run(), monitor.run())

    await Timer(T_FREE , "ns")
    cocotb.log.info("END OF THE TEST")

@cocotb.test()
async def test_extended(dut):
    """Exercise randomized transactions with extra trailing SCLK cycles."""
    cocotb.start_soon(gen_clk(dut))

    # Set up the driver, monitor, and shared reference model.
    driver = spi_master_driver(dut)
    monitor = spi_monitor(dut)
    slave_model = model()
    monitor.model = slave_model

    dut.ss_n.value = 1
    dut.sclk.value = 0
    dut.mosi.value = 0
    await reset(dut)

    # Queue extended, non-aborted transfers with randomized commands and data.
    for _ in range(100):
        item = spi_transaction()
        item.cmd    =  LogicArray(random.randint(0  , 3) , Range(1 , "downto" , 0))
        item.data   = LogicArray(random.randint(0 , 255) , Range(7 , "downto" , 0))
        item.extended = True
        item.abort = False
        await driver.pending_items.put(item)

    await gather(driver.run(), monitor.run())
    await Timer(T_FREE , "ns")
    cocotb.log.info("END OF THE TEST")

@cocotb.test()
async def test_reset_ongoing(dut):
    """Reset during an active read, clear pending work, and verify a later read."""
    cocotb.start_soon(gen_clk(dut))
    driver = spi_master_driver(dut)
    monitor = spi_monitor(dut)
    slave_model = model()
    monitor.model = slave_model

    dut.ss_n.value = 1
    #dut.sclk.value = 0
    dut.mosi.value = 0
    await reset(dut)

    # Queue reads so reset can interrupt one of the active transactions.
    for _ in range(10):
        item = spi_transaction()
        item.cmd = spi_cmd.READ_DATA.value
        item.extended = False
        item.abort = False
        await driver.pending_items.put(item)
    
    # Compete the driver and monitor against a reset injected mid-transaction.
    result = await select(driver.run() , monitor.run() , insert_reset(dut))
    if result[0] == 2:
        # Reset interrupted the transfer: reset model state and discard stale work.
        await slave_model.model_initialized()
        dut.ss_n.value = 1
        while not driver.pending_items.empty():
            try:
                driver.pending_items.get_nowait()
            except QueueEmpty:
                break
           
        await Timer(T_FREE , "ns")
        # Issue a fresh read after reset and let driver/monitor finish normally.
        item_2 = spi_transaction()
        item_2.cmd = spi_cmd.READ_DATA.value
        item_2.extended = False
        item_2.abort = False
        await driver.pending_items.put(item_2)

        await gather(driver.run() , monitor.run())

        await Timer(T_FREE , "ns")
        cocotb.log.info("END OF THE TEST")    


@cocotb.test()
async def report(dut):
    """Export the collected functional coverage database as YAML."""
    #coverage_db.report_coverage(cocotb.log.info, bins=True)
    coverage_db.export_to_yaml(
        filename=os.environ.get("SPI_COVERAGE_FILE", "coverage.yaml")
    )
