// skeleton.cpp: reference program for the vendored sokol + Dear ImGui API.
//
// It is NOT a fluid simulation. It shows every API pattern an entry needs:
// a fullscreen-triangle pipeline, GLSL ES 3.00 shaders with a uniform block and a
// sampled texture, ping-pong RGBA16F render targets with attachment and
// texture views, mouse input, an ImGui panel, and the swapchain pass. It runs
// in the browser on WebGL2, and the canvas can be any size.
// The program fades a dye texture and paints into it with the mouse.
#include "bench.h"
#include <cmath>

static const int SIM_W = 512, SIM_H = 288;

static const char* VS = R"(#version 300 es
layout(location=0) in vec2 pos;
out vec2 uv;
void main() {
    uv = pos * 0.5 + 0.5;
    gl_Position = vec4(pos, 0.0, 1.0);
}
)";

// Offscreen pass: read the previous texture, fade it, add a gaussian splat.
static const char* FS_PAINT = R"(#version 300 es
precision highp float;
precision highp sampler2D;
uniform sampler2D src;
uniform vec4 splat;   // xy: point in uv space, z: radius, w: strength
uniform vec4 color;   // rgb: splat color, a: fade factor
in vec2 uv;
out vec4 frag;
void main() {
    vec4 c = texture(src, uv) * color.a;
    vec2 d = uv - splat.xy;
    d.x *= float(textureSize(src, 0).x) / float(textureSize(src, 0).y);
    c.rgb += color.rgb * splat.w * exp(-dot(d, d) / splat.z);
    frag = c;
}
)";

// Swapchain pass: draw the texture to the screen.
static const char* FS_SHOW = R"(#version 300 es
precision highp float;
precision highp sampler2D;
uniform sampler2D src;
in vec2 uv;
out vec4 frag;
void main() {
    vec3 c = texture(src, uv).rgb;
    frag = vec4(c / (1.0 + c), 1.0);   // simple tone map
}
)";

struct PaintParams { float splat[4]; float color[4]; };

static struct {
    sg_buffer vbuf;
    sg_image  img[2];
    sg_view   att[2];   // color-attachment views, to render into img[i]
    sg_view   tex[2];   // texture views, to sample img[i]
    sg_sampler smp;
    sg_pipeline paint_pip, show_pip;
    int cur = 0;
    float mouse_x = 0, mouse_y = 0;
    bool mouse_down = false;
    float radius = 0.002f, fade = 0.99f;
    float col[3] = { 0.2f, 0.6f, 1.0f };
} S;

static sg_shader make_shader(const char* fs, bool with_uniforms) {
    sg_shader_desc d = {};
    d.vertex_func.source = VS;
    d.fragment_func.source = fs;
    d.attrs[0].glsl_name = "pos";
    if (with_uniforms) {
        d.uniform_blocks[0].stage = SG_SHADERSTAGE_FRAGMENT;
        d.uniform_blocks[0].size = sizeof(PaintParams);
        d.uniform_blocks[0].glsl_uniforms[0] = { SG_UNIFORMTYPE_FLOAT4, 0, "splat" };
        d.uniform_blocks[0].glsl_uniforms[1] = { SG_UNIFORMTYPE_FLOAT4, 0, "color" };
    }
    d.views[0].texture.stage = SG_SHADERSTAGE_FRAGMENT;
    d.views[0].texture.image_type = SG_IMAGETYPE_2D;
    d.views[0].texture.sample_type = SG_IMAGESAMPLETYPE_FLOAT;
    d.samplers[0].stage = SG_SHADERSTAGE_FRAGMENT;
    d.samplers[0].sampler_type = SG_SAMPLERTYPE_FILTERING;
    d.texture_sampler_pairs[0].stage = SG_SHADERSTAGE_FRAGMENT;
    d.texture_sampler_pairs[0].view_slot = 0;
    d.texture_sampler_pairs[0].sampler_slot = 0;
    d.texture_sampler_pairs[0].glsl_name = "src";
    return sg_make_shader(&d);
}

static void init() {
    sg_desc gd = {};
    gd.environment = sglue_environment();
    gd.logger.func = slog_func;
    sg_setup(&gd);

    simgui_desc_t id = {};
    id.logger.func = slog_func;
    simgui_setup(&id);

    // One big triangle that covers the screen.
    const float verts[] = { -1.f, -1.f,  3.f, -1.f,  -1.f, 3.f };
    sg_buffer_desc bd = {};
    bd.data = SG_RANGE(verts);
    S.vbuf = sg_make_buffer(&bd);

    for (int i = 0; i < 2; i++) {
        sg_image_desc imd = {};
        imd.usage.color_attachment = true;
        imd.width = SIM_W;
        imd.height = SIM_H;
        imd.pixel_format = SG_PIXELFORMAT_RGBA16F;
        imd.sample_count = 1;
        S.img[i] = sg_make_image(&imd);

        sg_view_desc av = {};
        av.color_attachment.image = S.img[i];
        S.att[i] = sg_make_view(&av);

        sg_view_desc tv = {};
        tv.texture.image = S.img[i];
        S.tex[i] = sg_make_view(&tv);
    }

    sg_sampler_desc sd = {};
    sd.min_filter = SG_FILTER_LINEAR;
    sd.mag_filter = SG_FILTER_LINEAR;
    sd.wrap_u = SG_WRAP_CLAMP_TO_EDGE;
    sd.wrap_v = SG_WRAP_CLAMP_TO_EDGE;
    S.smp = sg_make_sampler(&sd);

    // Offscreen pipeline: renders into an RGBA16F target, no depth buffer.
    sg_pipeline_desc pd = {};
    pd.shader = make_shader(FS_PAINT, true);
    pd.layout.attrs[0].format = SG_VERTEXFORMAT_FLOAT2;
    pd.colors[0].pixel_format = SG_PIXELFORMAT_RGBA16F;
    pd.depth.pixel_format = SG_PIXELFORMAT_NONE;
    pd.sample_count = 1;
    S.paint_pip = sg_make_pipeline(&pd);

    // Swapchain pipeline: default formats come from the environment.
    sg_pipeline_desc sp = {};
    sp.shader = make_shader(FS_SHOW, false);
    sp.layout.attrs[0].format = SG_VERTEXFORMAT_FLOAT2;
    S.show_pip = sg_make_pipeline(&sp);
}

static void frame() {
    simgui_frame_desc_t fd = { sapp_width(), sapp_height(), sapp_frame_duration(), sapp_dpi_scale() };
    simgui_new_frame(&fd);

    ImGui::SetNextWindowPos(ImVec2(10, 10), ImGuiCond_Once);
    ImGui::Begin("Controls", nullptr, ImGuiWindowFlags_AlwaysAutoResize);
    ImGui::Text("%.1f FPS", ImGui::GetIO().Framerate);
    ImGui::SliderFloat("radius", &S.radius, 0.0002f, 0.01f, "%.4f");
    ImGui::SliderFloat("fade", &S.fade, 0.9f, 1.0f);
    ImGui::ColorEdit3("color", S.col);
    ImGui::End();

    // Offscreen pass: read tex[cur], write att[1-cur], then swap.
    const int src = S.cur, dst = 1 - S.cur;
    PaintParams p = {};
    p.splat[0] = S.mouse_x / (float)sapp_width();
    p.splat[1] = 1.0f - S.mouse_y / (float)sapp_height();   // window y is down, uv y is up
    p.splat[2] = S.radius;
    p.splat[3] = S.mouse_down ? 1.0f : 0.0f;
    p.color[0] = S.col[0]; p.color[1] = S.col[1]; p.color[2] = S.col[2];
    p.color[3] = S.fade;

    sg_pass op = {};
    op.action.colors[0].load_action = SG_LOADACTION_DONTCARE;
    op.attachments.colors[0] = S.att[dst];
    sg_begin_pass(&op);
    sg_apply_pipeline(S.paint_pip);
    sg_bindings ob = {};
    ob.vertex_buffers[0] = S.vbuf;
    ob.views[0] = S.tex[src];
    ob.samplers[0] = S.smp;
    sg_apply_bindings(&ob);
    sg_apply_uniforms(0, SG_RANGE(p));
    sg_draw(0, 3, 1);
    sg_end_pass();
    S.cur = dst;

    // ImGui must be flushed outside any pass, then drawn inside one.
    simgui_flush();

    sg_pass sp = {};
    sp.action.colors[0].load_action = SG_LOADACTION_CLEAR;
    sp.action.colors[0].clear_value = { 0, 0, 0, 1 };
    sp.swapchain = sglue_swapchain();
    sg_begin_pass(&sp);
    sg_apply_pipeline(S.show_pip);
    sg_bindings sb = {};
    sb.vertex_buffers[0] = S.vbuf;
    sb.views[0] = S.tex[S.cur];
    sb.samplers[0] = S.smp;
    sg_apply_bindings(&sb);
    sg_draw(0, 3, 1);
    simgui_draw();
    sg_end_pass();
    sg_commit();
}

static void event(const sapp_event* e) {
    if (simgui_handle_event(e)) return;   // ImGui wants this event
    switch (e->type) {
        case SAPP_EVENTTYPE_MOUSE_DOWN: S.mouse_down = true; break;
        case SAPP_EVENTTYPE_MOUSE_UP:   S.mouse_down = false; break;
        case SAPP_EVENTTYPE_MOUSE_MOVE: S.mouse_x = e->mouse_x; S.mouse_y = e->mouse_y; break;
        default: break;
    }
}

static void cleanup() {
    simgui_shutdown();
    sg_shutdown();
}

sapp_desc sokol_main(int, char**) {
    sapp_desc d = {};
    d.init_cb = init;
    d.frame_cb = frame;
    d.event_cb = event;
    d.cleanup_cb = cleanup;
    d.width = 1280;
    d.height = 720;
    d.window_title = "skeleton";
    d.logger.func = slog_func;
    return d;
}
