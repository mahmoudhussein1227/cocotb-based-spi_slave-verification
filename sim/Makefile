# This file is public domain, it can be freely copied without restrictions.
# SPDX-License-Identifier: CC0-1.0

# Makefile
# The simulator we want to run our tests with.
SIM = questa

# The top-level language of the design (verilog or vhdl).
TOPLEVEL_LANG = verilog

# List of Verilog source files.
VERILOG_SOURCES = ../rtl/spi_wrapper.v ../rtl/spi_slave.v ../rtl/spi_ram.v

# The name of the toplevel module/entity in your design.
COCOTB_TOPLEVEL = spi_wrapper

# The Python modules which contain your testcases.
COCOTB_TEST_MODULES = test_spi
export PYTHONPATH := $(abspath ../testbench);$(PYTHONPATH)

# Run regressions headlessly by default. Override with GUI=1 for interactive use.
GUI ?= 0
WAVES ?= 0
ifeq ($(GUI),1)
VSIM_ARGS += -onfinish stop -do {add wave sim:/spi_wrapper/mosi sim:/spi_wrapper/miso sim:/spi_wrapper/sclk sim:/spi_wrapper/ss_n sim:/spi_wrapper/clk sim:/spi_wrapper/rst_n ; run -all}
endif

CUSTOM_SIM_DEPS += Makefile

# Includes cocotb's Makefiles which define the build and simulation flows.
include $(shell cocotb-config --makefiles)/Makefile.sim

# Keep relative source paths in Questa scripts; cygpath's absolute paths break
# when the workspace path contains spaces.
to_tcl_path = $(1)
