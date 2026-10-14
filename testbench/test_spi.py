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
    global _last_accepted_cmd
    _last_accepted_cmd = None


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

# coverage model
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
    pass


# testbench parameters
# clock period
T_CLK = 10
# bus free time
T_FREE = 500
# timeout for the driver to wait for a transaction from the pending_items fifo in number of clk cycles
TIME_OUT = 10

# ram model
ram = {}
for i in range(256):
    ram[i] = 0

# coroutine to generate the clk signal
async def gen_clk(dut):
    while True:
        dut.clk.value = 0
        await Timer(T_CLK /2 , "ns")
        dut.clk.value = 1
        await Timer(T_CLK /2 , "ns")

# command types
class spi_cmd(Enum):
    WRITE_ADDR = LogicArray("00" , Range(1 , "downto" , 0))
    WRITE_DATA = LogicArray("01" , Range(1 , "downto" , 0))
    READ_ADDR  = LogicArray("10" , Range(1 , "downto" , 0))
    READ_DATA  = LogicArray("11" , Range(1 , "downto" , 0))


# transaction class
class spi_transaction:
    def __init__(self ):
        # the command {00 write addr , 01 write data , 10 read mem_addr , 11 dummy byte + data read}
        self.cmd  =  LogicArray("00" , Range(1 , "downto" , 0))
        # data byte read/written
        self.data =  LogicArray("00000000" , Range(7 , "downto" , 0))
        # abort flag to indicate if the transaction is aborted or not
        self.abort = False
        self.extended = False

    def randomize(self):
        self.cmd    =  LogicArray(random.randint(0  , 3) , Range(1 , "downto" , 0))
        self.data   = LogicArray(random.randint(0 , 255) , Range(7 , "downto" , 0))
        self.abort  = random.choice([True , False])
        self.extended = random.choice([True , False])

    def convert2string(self):
        result = f"command is {spi_cmd(self.cmd)} , data  : {self.data}"
        return result

# driver class
class spi_master_driver:
    def __init__(self ,dut):
        self.dut = dut
        # items to be driven by the driver
        self.pending_items = Queue()
    # coroutine to drive the sclk signal
    async def drive_sclk(self):
        while True:
            # sclk is 1/10 of the clk frequency (clk must be > 8 times faster than the sclk)
            self.dut.sclk.value = 0
            await Timer((T_CLK * 10)/2 , "ns")
            self.dut.sclk.value = 1
            await Timer((T_CLK * 10)/2 , "ns")
    # coroutine to wait for a number of clk cycles
    async def wait_cycles(self , num_cycles : int):
        dut = self.dut
        for _ in range(num_cycles):
            await RisingEdge(dut.clk)
    #coroutine to raise ss_n during the transaction
    async def drv_abort(self , access_type):
        dut = self.dut
        while dut.ss_n.value == 0 :
            if access_type == spi_cmd.READ_DATA.value:
                # abort the transaction in the randomly durting read_data byte
                rand_bit_pos = random.randint(11,17)
            else:
                rand_bit_pos = random.randint(1,9)
            for _ in range(rand_bit_pos):
                await FallingEdge(dut.sclk)
            dut.ss_n.value = 1
            break

    # main driver coroutine to drive the spi transaction on the bus
    async def run(self):
        while True :
            # wait to get from the pending_items fifo within 10 clock cycles(TIME_OUT)
            result = await select(self.pending_items.get() , self.wait_cycles(TIME_OUT))
            if result[0] == 1:
                #cocotb.log.info(result[0])
                break
            dut  = self.dut
            # gets trasaction object 
            item = result[1]
            string = "driving : " + item.convert2string()
            cocotb.log.info(string)
            # start the spi transaction by pulling the ss_n low
            dut.ss_n.value = 0
            await self.wait_cycles(2)
            # start the sclk task to generate the sclk pulses
            drv_sclk_task = cocotb.start_soon(self.drive_sclk())
            # decide if we want to abort the running transaction or not
            if item.abort:
                # pass the type of the command to the aborting coroutine
                drive_abort = cocotb.start_soon(self.drv_abort(item.cmd))
            #serialze the CMD/data on the mosi MSB first
            for i in range(len(item.cmd) - 1, -1, -1):
                dut.mosi.value = item.cmd[i]
                await FallingEdge(dut.sclk)
            for i in range(len(item.data) - 1, -1, -1):
                dut.mosi.value = item.data[i]
                await FallingEdge(dut.sclk)
            # wait for the data read from the slave
            if item.cmd == spi_cmd.READ_DATA.value:
                for _ in range(8):
                    await FallingEdge(dut.sclk)
            #end the transaction and drive the bus to idle values
            if item.extended : 
                for _ in range(5):
                    await FallingEdge(dut.sclk)
            drv_sclk_task.cancel()
            dut.sclk.value = 0
            dut.mosi.value = 0
            #await self.wait_cycles(2)
            dut.ss_n.value = 1
            await Timer(T_FREE , "ns")


class spi_monitor:
    def __init__(self , dut ):
        self.dut = dut
        self.model = model()
    # coroutine to wait the start of the spi transaction
    async def wait_transaction_start(self):
        dut = self.dut
        if dut.ss_n.value == 1:
            await FallingEdge(dut.ss_n)
            # transaction starts
    # coroutine to collect the spi transaction
    async def collect_transaction(self , item):
        dut = self.dut
        # create the item
        #item = spi_transaction()
        # collect the cmd 
        for i in range(2):
            await RisingEdge(dut.sclk)
            item.cmd[1-i] = dut.mosi.value
        # check for the cmd type
        if item.cmd == spi_cmd.READ_DATA.value:
            # wait for the dummy mosi byte
            for _ in range(8):
                await RisingEdge(dut.sclk)
            # collect the data read from the slave
            for i in range(8):
                await RisingEdge(dut.sclk)
                await Timer(1 , "ns")
                item.data[7-i] = dut.miso.value
        else :# other commands rather than readData 
            for i in range(8):
                await RisingEdge(dut.sclk)
                item.data[7-i] = dut.mosi.value
        cocotb.log.info(f"item Monitored : {item.convert2string()}")

    # coroutine to wait for the deassertion of the ss_n
    async def wait_transaction_end(self):
        dut = self.dut
        if dut.ss_n.value != 1:
            await RisingEdge(dut.ss_n)
            # transaction ends
    #coroutine to wait some amount of clk cycles
    async def wait_cycles(self , num_cycles):
        dut = self.dut
        for _ in range(num_cycles):
            await RisingEdge(dut.clk)
    # main coroutine 
    async def run(self):
        while True:
            item = spi_transaction()
            res = await select(self.wait_transaction_start() , Timer(6*T_FREE , "ns"))
            if res[0] == 1:
                cocotb.log.info("bus is idle for more than 10 clock cycles")
                break
            else:
                result  = await select(self.collect_transaction(item) , self.wait_transaction_end())
                if result[0] == 1:
                    item.abort = True
                    cocotb.log.info("Transaction aborted and the command is discarded")
                else:
                    # push the collected item to scb , model and coverage components
                    await self.model.mon_queue.put(item)
                    # run the model
                    await self.model.run()
                    await self.wait_transaction_end()
                    cocotb.log.info("Transaction is collected completely")
                sample_mem(item.data , item.cmd , item.abort)

# model component
class model :
    def __init__(self):
        # ram model
        self.write_addr = 0
        self.read_addr  = 0
        # to store the bus inputs
        self.mon_queue  = Queue()
        # initialize all the location to be zero

    # main model coroutine
    async def run(self):
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
            assert output_data == item_mon.data, (
                f"data read from RAM address {self.read_addr} is incorrect: "
                f"expected {output_data}, got {item_mon.data}"
            )
    async def model_initialized(self):
        self.write_addr = 0
        self.read_addr  = 0
        

# coroutine to reset the DUT
async def reset(dut):
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


#coroutine to wait for a number of sclk cycles
async def wait_sclk_cycles(dut , num_cycles):
    for _ in range(num_cycles):
        await FallingEdge(dut.sclk)

# coroutine to wait for the end of the transaction by waiting for the ss_n to go high
async def end_transaction(dut):
    while True:
        await RisingEdge(dut.ss_n)
        break

async def insert_reset(dut):
    while dut.ss_n.value != 0:
        await RisingEdge(dut.clk)
    reset_pos = random.randint(1 , 9)
    for _ in range(reset_pos):
        await RisingEdge(dut.sclk)

    await reset(dut)

@cocotb.test()
async def test_random(dut):
    num_transactions = 1000
    cocotb.start_soon(gen_clk(dut))
    # creating the driver and monitor
    driver        = spi_master_driver(dut)
    monitor       = spi_monitor(dut)
    slave_model   = model()
    monitor.model = slave_model

    # initialize the dut signals to idle values
    dut.ss_n.value = 1
    dut.sclk.value = 0
    dut.mosi.value = 0
    await reset(dut)
    for _ in range(num_transactions):
        # creating / the spi transaction(s)
        item = spi_transaction()
        # randomize it
        item.randomize()
        # push it the driver pending item
        await driver.pending_items.put(item)

    # pin level activity
    await gather(driver.run() , monitor.run())

    await Timer(T_FREE , "ns")

    cocotb.log.info("END OF THE TEST")


@cocotb.test()
async def test_write_read_random_abort(dut):
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

    for address in range(ram_depth):
        data_to_write = (address * 37 + 0xA5) & 0xFF
        for command, data in (
            (spi_cmd.WRITE_ADDR, address),
            (spi_cmd.WRITE_DATA, data_to_write),
            (spi_cmd.READ_ADDR, address),
            (spi_cmd.READ_DATA, 0),
        ):
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
    cocotb.start_soon(gen_clk(dut))

    driver = spi_master_driver(dut)
    monitor = spi_monitor(dut)
    slave_model = model()
    monitor.model = slave_model

    dut.ss_n.value = 1
    dut.sclk.value = 0
    dut.mosi.value = 0
    await reset(dut)

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
    cocotb.start_soon(gen_clk(dut))
    driver = spi_master_driver(dut)
    monitor = spi_monitor(dut)
    slave_model = model()
    monitor.model = slave_model

    dut.ss_n.value = 1
    #dut.sclk.value = 0
    dut.mosi.value = 0
    await reset(dut)


    # pend 10 items
    for _ in range(10):
        item = spi_transaction()
        item.cmd = spi_cmd.READ_DATA.value
        item.extended = False
        item.abort = False
        await driver.pending_items.put(item)
    
    result = await select(driver.run() , monitor.run() , insert_reset(dut))
    if result[0] == 2:
        await slave_model.model_initialized()
        dut.ss_n.value = 1
        while not driver.pending_items.empty():
            try:
                driver.pending_items.get_nowait()
            except QueueEmpty:
                break
           
        await Timer(T_FREE , "ns")
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
    #coverage_db.report_coverage(cocotb.log.info, bins=True)
    coverage_db.export_to_yaml(
        filename=os.environ.get("SPI_COVERAGE_FILE", "coverage.yaml")
    )
