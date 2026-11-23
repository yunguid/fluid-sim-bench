# fluid-sim-bench

One prompt, many models: every model writes a real-time fluid simulation
in C/C++ against the same libraries ([sokol](https://github.com/floooh/sokol)
and [Dear ImGui](https://github.com/ocornut/imgui)). Each result is compiled,
run, captured, and shown side by side in a gallery.

Runs on a single RTX 4090 box, with API models (Claude, GPT, Gemini, Grok) and
local models (served on the GPU) through the same queue.

## Layout

| Path | Contents |
| --- | --- |
| `third_party/` | Vendored sokol and Dear ImGui headers that every entry builds against |
| `archive/` | Earlier fluid-sim experiments, kept as they were |

The prompt, model registry, job runner, results and gallery are still being built.

## Archive

| Folder | What it was |
| --- | --- |
| `fluid-sim/` | WebGL fluid sim (`script.js`) plus single-file HTML attempts (`1.html`, `o3.html`) |
| `fluid-sim-gallery/` | gpu-io fluid gallery, 18 variants; was [yunguid/fluid-sim-gallery](https://github.com/yunguid/fluid-sim-gallery) |
| `gpu-io-fork/` | Fork of gpu-io with fluid example variants; was [yunguid/gpu-io-lry](https://github.com/yunguid/gpu-io-lry) |
| `fluid-sim-o3/` | o3 attempt, plus `gemini-3-fluid-sim/` |
| `fluid-sim-gpt5/` | GPT-5 single-file attempt |
| `o1-fluid-test/` | o1 attempt |
| `codex-max-fluid-sim/` | Codex Max single-file attempt |
