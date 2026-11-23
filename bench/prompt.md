Write a real-time 2D fluid simulation in C++17, as one file named `main.cpp`.

## Environment

- Your program runs in a web browser. Your file is compiled to WebAssembly
  with Emscripten, with exactly this command and nothing else:

  ```
  em++ -O2 -std=c++17 -Isdk -Isokol -Iimgui main.cpp libbenchsdk.a \
       -sMIN_WEBGL_VERSION=2 -sMAX_WEBGL_VERSION=2 -sALLOW_MEMORY_GROWTH=1 \
       -sSTACK_SIZE=4MB -sENVIRONMENT=web -o app.js
  ```

- `#include "bench.h"` is the only include you need for graphics and UI. It
  includes sokol_app, sokol_gfx (WebGL2 backend, `SOKOL_GLES3`), sokol_glue,
  sokol_log, sokol_time, Dear ImGui and sokol_imgui. The implementations are
  already compiled into `libbenchsdk.a`. Do **not** define `SOKOL_IMPL`, and do
  not include any other third-party library. The C++ standard library is fine.
- The sokol version is recent and differs from older versions you may know:
  textures and render targets are bound through `sg_view` objects made with
  `sg_make_view`, shaders bind textures through `views[]`,
  `samplers[]` and `texture_sampler_pairs[]`, and ImGui is drawn with
  `simgui_flush()` outside any pass followed by `simgui_draw()` inside the
  swapchain pass. The reference program below compiles and runs against the
  exact headers used. Follow its API usage precisely.
- Shaders are GLSL ES 3.00 source strings: they start with `#version 300 es`,
  fragment shaders declare `precision highp float;` (and
  `precision highp sampler2D;` when they sample float textures), and uniforms
  are individual `uniform` variables whose names match
  `glsl_uniforms[].glsl_name`.
- WebGL2 limits apply: `RGBA16F` textures can be rendered to and sampled
  with linear filtering; 32-bit float textures cannot be linearly filtered.
- The program runs in a page that gives it a canvas filling the browser
  window, which can be any size and can change size while running. Read
  `sapp_width()` and `sapp_height()` every frame. sokol drives the frame loop:
  there are no threads, the program must never block, and there is no file
  system or network.

## Requirements

1. **Physics.** Simulate incompressible flow: advect velocity and dye, enforce
   a divergence-free velocity field with a pressure solve, and keep it stable at
   any frame rate. Vorticity confinement, buoyancy, or other extras are welcome.
2. **Input.** Dragging with the left mouse button pushes the fluid in the drag
   direction and adds colorful dye at the cursor.
3. **Never empty.** Without any input, the scene must still move: start with
   several splats and keep adding occasional automatic ones.
4. **Controls.** An ImGui panel with at least: FPS, viscosity or velocity
   dissipation, dye dissipation, pressure iterations, splat radius, and a
   Reset button.
5. **Looks.** Make it beautiful. Color, shading, and motion quality are judged
   by people running it in their browser and playing with it.
6. **Robust.** No crashes, no NaNs blowing up the screen, no sokol validation
   errors, and it must keep running until the page is closed.

## How your program is tested

- It is built with the command above, which must finish within 5 minutes.
- It is loaded in headless Chromium in a sandbox with no network, in a
  1280×720 window, with software WebGL2 (SwiftShader). Frame rates there are
  far lower than on a real GPU, so the simulation must stay usable at low
  frame rates and should scale its work sensibly.
- It must start drawing frames within 30 seconds of the page loading. After 2
  more seconds, a 15-second test begins and screenshots are taken. The
  program must still be drawing frames at the end, with no errors.
- At second 5 of the test the left mouse button is held down and the cursor
  traces a figure-eight through the middle of the window for about 5 seconds,
  then released. The ImGui panel should stay near the top-left so it does not
  catch this drag.
- Afterwards, people run every entry live in their own browsers, on real
  GPUs and at many window sizes, and compare them side by side.

## Output

Reply with one ```cpp code block containing the complete `main.cpp`, and
nothing after it. A short explanation before the code block is fine.

## Reference program

This compiles and runs in the browser against the provided headers. It is not a fluid
simulation. It shows the API patterns: a fullscreen-triangle pipeline,
shaders with uniforms and a sampled texture, ping-pong RGBA16F render targets,
mouse input, an ImGui panel, and the swapchain pass.

```cpp
{{SKELETON}}
```
