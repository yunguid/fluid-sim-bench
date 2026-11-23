// bench.h: the single include every benchmark entry uses.
// It pulls in sokol (WebGL2 / GLES3 backend, built with Emscripten) and
// Dear ImGui. The implementations are compiled once, in sokol_impl.cpp, so an
// entry must NOT define SOKOL_IMPL itself.
#pragma once
#ifndef SOKOL_GLES3
#define SOKOL_GLES3
#endif
#include "imgui.h"
#include "sokol_app.h"
#include "sokol_gfx.h"
#include "sokol_glue.h"
#include "sokol_log.h"
#include "sokol_time.h"
#include "sokol_imgui.h"
