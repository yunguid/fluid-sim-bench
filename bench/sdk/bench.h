// bench.h: the single include every benchmark entry uses.
// It pulls in sokol (OpenGL 4.1 core backend) and Dear ImGui. The
// implementations are compiled once, in sokol_impl.cpp, so an entry must NOT
// define SOKOL_IMPL itself.
#pragma once
#ifndef SOKOL_GLCORE
#define SOKOL_GLCORE
#endif
#include "imgui.h"
#include "sokol_app.h"
#include "sokol_gfx.h"
#include "sokol_glue.h"
#include "sokol_log.h"
#include "sokol_time.h"
#include "sokol_imgui.h"
