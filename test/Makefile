# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = rf.v alu.v project.v

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Select testbench: `make test_<name>` uses <name>_tb.v and test_<name>.py;
# plain `make` uses tb.v and test.py (default behaviour).
ifdef CUSTOM_TB
VERILOG_SOURCES += $(PWD)/test_modules/$(CUSTOM_TB)_tb.v
TOPLEVEL = $(CUSTOM_TB)_tb
COCOTB_TEST_MODULES = test_$(CUSTOM_TB)
else
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb
COCOTB_TEST_MODULES = test
endif

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim

# Pattern rule: `make test_<name> [options]` re-invokes make with CUSTOM_TB set.
# Extra variables (GATES, FST, …) are forwarded automatically via MAKEFLAGS.
test_%:
	$(MAKE) CUSTOM_TB=$*
