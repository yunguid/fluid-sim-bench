# Builds the shared SDK library once, then links any entry against it.
#
#   make -C bench/sdk                                    # build/sdk/libbenchsdk.a
#   make -C bench/sdk app ENTRY=/abs/main.cpp OUT=/abs/web
#
# WebAssembly + WebGL2 through Emscripten. An entry builds to OUT/app.js and
# OUT/app.wasm; OUT/index.html is the fixed page from bench/web/shell.html.

ROOT     := $(abspath ../..)
BUILD    := $(ROOT)/build/sdk
TP       := $(ROOT)/third_party
CXX      := em++
AR       := emar
CXXFLAGS := -O2 -std=c++17
INCLUDES := -I$(CURDIR) -I$(TP)/sokol -I$(TP)/imgui
LDFLAGS  := -sMIN_WEBGL_VERSION=2 -sMAX_WEBGL_VERSION=2 -sALLOW_MEMORY_GROWTH=1 \
            -sSTACK_SIZE=4MB -sENVIRONMENT=web -sEXPORTED_RUNTIME_METHODS=ccall
SHELL_HTML := $(ROOT)/bench/web/shell.html

IMGUI_SRC := imgui.cpp imgui_draw.cpp imgui_tables.cpp imgui_widgets.cpp
OBJS := $(BUILD)/sokol_impl.o $(patsubst %.cpp,$(BUILD)/%.o,$(IMGUI_SRC))
LIB  := $(BUILD)/libbenchsdk.a

.PHONY: all app skeleton clean
all: $(LIB)

$(BUILD)/sokol_impl.o: sokol_impl.cpp bench.h | $(BUILD)
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c $< -o $@

$(BUILD)/%.o: $(TP)/imgui/%.cpp | $(BUILD)
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c $< -o $@

$(LIB): $(OBJS)
	$(AR) rcs $@ $^

$(BUILD):
	mkdir -p $@

app: $(LIB)
	mkdir -p $(OUT)
	$(CXX) $(CXXFLAGS) $(INCLUDES) $(ENTRY) $(LIB) $(LDFLAGS) -o $(OUT)/app.js
	cp $(SHELL_HTML) $(OUT)/index.html

skeleton: $(LIB)
	$(MAKE) app ENTRY=$(CURDIR)/skeleton.cpp OUT=$(BUILD)/skeleton

clean:
	rm -rf $(BUILD)
