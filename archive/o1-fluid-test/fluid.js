// =======================================================
// Initialization
// =======================================================
const canvas = document.getElementById('glcanvas');
resizeCanvas();
window.addEventListener('resize', resizeCanvas);

function resizeCanvas() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
}

const gl = canvas.getContext('webgl2', { alpha: false });
if (!gl) {
    alert('WebGL2 not supported');
}

// Check for float textures
if (!gl.getExtension('EXT_color_buffer_float')) {
    alert('FLOAT color buffer not supported');
}

// Simulation resolution (internal)
const N = 256; 
let viscosity = 0.01;
let diffusion = 0.001;

document.getElementById('viscosity').addEventListener('input', e => {
    viscosity = parseFloat(e.target.value);
});
document.getElementById('diffusion').addEventListener('input', e => {
    diffusion = parseFloat(e.target.value);
});
document.getElementById('reset').addEventListener('click', resetSimulation);

// Mouse interaction for adding forces
let mouseDown = false;
let mouseX = 0, mouseY = 0;
canvas.addEventListener('mousedown', () => { mouseDown = true; });
canvas.addEventListener('mouseup', () => { mouseDown = false; });
canvas.addEventListener('mousemove', e => {
  const rect = canvas.getBoundingClientRect();
  mouseX = (e.clientX - rect.left) / rect.width;
  mouseY = 1.0 - (e.clientY - rect.top) / rect.height;
});

// Utility: create shaders, programs, buffers, textures
function createShader(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.error(gl.getShaderInfoLog(s));
        gl.deleteShader(s);
        return null;
    }
    return s;
}

function createProgram(gl, vsSrc, fsSrc) {
    const vs = createShader(gl, gl.VERTEX_SHADER, vsSrc);
    const fs = createShader(gl, gl.FRAGMENT_SHADER, fsSrc);
    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        console.error(gl.getProgramInfoLog(prog));
        return null;
    }
    return prog;
}

// Fullscreen quad
const quadVBO = gl.createBuffer();
gl.bindBuffer(gl.ARRAY_BUFFER, quadVBO);
gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -1,-1, 1,-1, -1,1, 1,1
]), gl.STATIC_DRAW);

function createTexture(width, height, internalFormat=gl.RGBA16F, format=gl.RGBA, type=gl.FLOAT) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, internalFormat, width, height, 0, format, type, null);
    return tex;
}

// Velocity: RG16F
function createVelocityTexture() {
    return createTexture(N, N, gl.RG16F, gl.RG, gl.FLOAT);
}

// Dye: RGBA16F
function createDyeTexture() {
    return createTexture(N, N, gl.RGBA16F, gl.RGBA, gl.FLOAT);
}

// Pressure: R16F
function createPressureTexture() {
    return createTexture(N, N, gl.R16F, gl.RED, gl.FLOAT);
}

// State Textures
const velTexA = createVelocityTexture();
const velTexB = createVelocityTexture();

const dyeTexA = createDyeTexture();
const dyeTexB = createDyeTexture();

const pressureTexA = createPressureTexture();
const pressureTexB = createPressureTexture();

function createFBO(tex) {
    const fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    return {fbo, tex};
}

let velFBOA = createFBO(velTexA);
let velFBOB = createFBO(velTexB);

let dyeFBOA = createFBO(dyeTexA);
let dyeFBOB = createFBO(dyeTexB);

let pressureFBOA = createFBO(pressureTexA);
let pressureFBOB = createFBO(pressureTexB);

function swapFBOs(a,b) {
    const tmp = a;
    a = b;
    b = tmp;
    return [a,b];
}

// -------------------------------------------------------
// Shaders
// -------------------------------------------------------
const vsQuad = `#version 300 es
precision highp float;
layout(location=0) in vec2 aPos;
void main() {
    gl_Position = vec4(aPos, 0.0, 1.0);
}`;

// Initialize velocity field (swirl)
const fsInitVel = `#version 300 es
precision highp float;
out vec2 FragColor;
uniform vec2 resolution;
void main() {
    vec2 uv = gl_FragCoord.xy / resolution;
    vec2 center = vec2(0.5, 0.5);
    vec2 r = uv - center;
    // swirl velocity
    vec2 v = vec2(-r.y, r.x)*0.5;
    FragColor = v;
}`;

// Initialize dye field (circular gradient)
const fsInitDye = `#version 300 es
precision highp float;
out vec4 FragColor;
uniform vec2 resolution;
void main() {
    vec2 uv = gl_FragCoord.xy / resolution;
    vec2 center = vec2(0.5,0.5);
    float dist = distance(uv, center);
    float intensity = exp(-20.0*dist*dist);
    // Slightly orange-ish color so we see contrast
    FragColor = vec4(intensity, intensity*0.5, intensity*0.2, 1.0);
}`;

// Advection for velocity (RG)
const fsAdvectVel = `#version 300 es
precision highp float;
out vec2 FragColor;
uniform sampler2D uField;
uniform sampler2D uVelocity;
uniform float dt;
uniform float dx;
uniform vec2 resolution;
void main() {
    vec2 uv = gl_FragCoord.xy / resolution;
    vec2 v = texture(uVelocity, uv).xy;
    vec2 prevPos = uv - dt * v / dx;
    FragColor = texture(uField, prevPos).xy;
}`;

// Advection for dye (RGBA)
const fsAdvectDye = `#version 300 es
precision highp float;
out vec4 FragColor;
uniform sampler2D uField;
uniform sampler2D uVelocity;
uniform float dt;
uniform float dx;
uniform vec2 resolution;
void main() {
    vec2 uv = gl_FragCoord.xy / resolution;
    vec2 v = texture(uVelocity, uv).xy;
    vec2 prevPos = uv - dt * v / dx;
    FragColor = texture(uField, prevPos);
}`;

// Render shader
// Here we use the screen size to map gl_FragCoord to [0,1] UV coordinates.
const fsRender = `#version 300 es
precision highp float;
out vec4 FragColor;
uniform sampler2D uDye;
uniform vec2 resolution; // This is now the screen resolution

void main() {
    vec2 uv = gl_FragCoord.xy / resolution;
    FragColor = texture(uDye, uv);
}`;

// Create programs
const initVelProg = createProgram(gl, vsQuad, fsInitVel);
const initDyeProg = createProgram(gl, vsQuad, fsInitDye);
const advectVelProg = createProgram(gl, vsQuad, fsAdvectVel);
const advectDyeProg = createProgram(gl, vsQuad, fsAdvectDye);
const renderProg = createProgram(gl, vsQuad, fsRender);

function drawQuad() {
    gl.enableVertexAttribArray(0);
    gl.bindBuffer(gl.ARRAY_BUFFER, quadVBO);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
}

// Initialize fields
function initField(prog, targetFBO) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, targetFBO.fbo);
    gl.viewport(0,0,N,N);
    gl.useProgram(prog);
    gl.uniform2f(gl.getUniformLocation(prog,"resolution"), N, N);
    drawQuad();
}

function initFields() {
    initField(initVelProg, velFBOA);
    initField(initDyeProg, dyeFBOA);
}

function resetSimulation() {
    initFields();
}

// Advect field
function advectField(prog, fieldFBOA, fieldFBOB, velTex) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, fieldFBOB.fbo);
    gl.viewport(0,0,N,N);
    gl.useProgram(prog);
    gl.uniform1f(gl.getUniformLocation(prog,"dt"), 0.016);
    gl.uniform1f(gl.getUniformLocation(prog,"dx"), 1.0);
    gl.uniform2f(gl.getUniformLocation(prog,"resolution"), N, N);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, fieldFBOA.tex);
    gl.uniform1i(gl.getUniformLocation(prog,"uField"),0);

    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, velTex.tex);
    gl.uniform1i(gl.getUniformLocation(prog,"uVelocity"),1);

    drawQuad();
}

function renderToScreen() {
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0,0,canvas.width,canvas.height);
    gl.useProgram(renderProg);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, dyeFBOA.tex);
    gl.uniform1i(gl.getUniformLocation(renderProg,"uDye"),0);

    // Pass canvas size here so uv maps full screen to [0,1]
    gl.uniform2f(gl.getUniformLocation(renderProg,"resolution"), canvas.width, canvas.height);

    drawQuad();
}

// Setup
resetSimulation();

let lastTime = performance.now();

// Main loop
function step() {
    const now = performance.now();
    const dt = (now - lastTime)*0.001;
    lastTime = now;

    // Advect velocity
    advectField(advectVelProg, velFBOA, velFBOB, velFBOA);
    [velFBOA, velFBOB] = swapFBOs(velFBOA, velFBOB);

    // Advect dye
    advectField(advectDyeProg, dyeFBOA, dyeFBOB, velFBOA);
    [dyeFBOA, dyeFBOB] = swapFBOs(dyeFBOA, dyeFBOB);

    // Render
    renderToScreen();

    requestAnimationFrame(step);
}

requestAnimationFrame(step);