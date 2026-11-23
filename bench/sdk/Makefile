# Builds the shared SDK library once, then links any entry against it.
#
#   make -C bench/sdk                                  # build/libbenchsdk.a
#   make -C bench/sdk app ENTRY=/abs/main.cpp OUT=/abs/app
#
# Linux, OpenGL 4.1 core through GLX/X11.

ROOT     := $(abspath ../..)
BUILD    := $(ROOT)/build/sdk
TP       := $(ROOT)/third_party
CXX      ?= g++
CXXFLAGS ?= -O2 -std=c++17
INCLUDES := -I$(CURDIR) -I$(TP)/sokol -I$(TP)/imgui
LIBS     := -lX11 -lXi -lXcursor -lGL -ldl -lpthread -lm

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
	ar rcs $@ $^

$(BUILD):
	mkdir -p $@

app: $(LIB)
	$(CXX) $(CXXFLAGS) $(INCLUDES) $(ENTRY) $(LIB) $(LIBS) -o $(OUT)

skeleton: $(LIB)
	$(CXX) $(CXXFLAGS) $(INCLUDES) skeleton.cpp $(LIB) $(LIBS) -o $(BUILD)/skeleton

clean:
	rm -rf $(BUILD)
