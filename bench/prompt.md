Write a real-time 2D fluid simulation in C++17, as one file named `main.cpp`.

## Environment

- Your file is compiled on Linux with exactly this command, and nothing else:

  ```
  g++ -O2 -std=c++17 -Isdk -Isokol -Iimgui main.cpp libbenchsdk.a -lX11 -lXi -lXcursor -lGL -ldl -lpthread -lm
  ```

- `#include "bench.h"` is the only include you need for graphics and UI. It
  includes sokol_app, sokol_gfx (OpenGL 4.1 core backend), sokol_glue,
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
- Shaders are GLSL `#version 410` source strings, with uniforms declared as
  individual `uniform` variables whose names match `glsl_uniforms[].glsl_name`.
- The program runs in a 1280×720 window, on a GPU or a software OpenGL
  renderer. Keep the simulation resolution modest enough to stay interactive.

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
   by people looking at a recording.
6. **Robust.** No crashes, no NaNs blowing up the screen, no sokol validation
   errors, and it must run until the window is closed.

## Output

Reply with one ```cpp code block containing the complete `main.cpp`, and
nothing after it. A short explanation before the code block is fine.

## Reference program

This compiles and runs against the provided headers. It is not a fluid
simulation. It shows the API patterns: a fullscreen-triangle pipeline,
shaders with uniforms and a sampled texture, ping-pong RGBA16F render targets,
mouse input, an ImGui panel, and the swapchain pass.

```cpp
{{SKELETON}}
```
