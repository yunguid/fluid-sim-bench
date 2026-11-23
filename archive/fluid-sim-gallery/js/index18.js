<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Raw‑WebGL2 Fluid Sim • navier–stokes</title>
<style>
  html,body{margin:0;height:100%;background:#000;font-family:monospace}
  canvas{width:100%;height:100%;display:block}
  #hint{position:fixed;left:8px;bottom:6px;color:#aaa;font-size:12px;user-select:none}
</style>
</head>
<body>
<canvas id="c"></canvas><div id="hint">LMB = force · Shift+LMB = ink</div>

<!-- ───── vertex shader shared by every pass ───── -->
<script id="vs" type="x-shader/x-vertex">
#version 300 es
precision highp float;
layout(location=0) in vec2 a_pos;
out vec2 v_uv;
void main(){
  v_uv = 0.5*(a_pos+1.0);
  gl_Position = vec4(a_pos,0,1);
}
</script>

<!-- shorthand macro to grab 4‑neighbour samples -->
<script id="lib-bilinear" type="plain/glsl">
#define S(T,xy)   texture(T,(xy)).r
#define L(T)      S(T,v_uv-vec2(px.x,0.0))
#define R(T)      S(T,v_uv+vec2(px.x,0.0))
#define B(T)      S(T,v_uv-vec2(0.0,px.y))
#define T_(T)     S(T,v_uv+vec2(0.0,px.y))
</script>

<!-- advection (3rd‑order Runge–Kutta) -->
<script id="fs-advect" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out vec2 o;
uniform sampler2D u_vel;uniform sampler2D u_src;uniform vec2 px;uniform float dt;
vec2 sampleVel(vec2 uv){return texture(u_vel,uv).xy;}
void main(){
  vec2 p=v_uv;                      // RK3 back‑trace
  vec2 k1=sampleVel(p);
  vec2 k2=sampleVel(p-0.5*dt*k1*px);
  vec2 k3=sampleVel(p-0.75*dt*k2*px);
  vec2 back = p - dt*((2.0/9.0)*k1 + (1.0/3.0)*k2 + (4.0/9.0)*k3)*px;
  o = texture(u_src,back).xy;
}
</script>

<!-- scalar advection (for dye) -->
<script id="fs-advect-scalar" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out vec4 o;
uniform sampler2D u_vel;uniform sampler2D u_src;uniform vec2 px;uniform float dt;
vec2 vel(vec2 uv){return texture(u_vel,uv).xy;}
void main(){
  vec2 p=v_uv;
  vec2 v1=vel(p);
  vec2 v2=vel(p-0.5*dt*v1*px);
  vec2 v3=vel(p-0.75*dt*v2*px);
  vec2 back=p-dt*((2.0/9.0)*v1+(1.0/3.0)*v2+(4.0/9.0)*v3)*px;
  o = texture(u_src,back)*0.995;            // slight decay
}
</script>

<!-- divergence -->
<script id="fs-div" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out float o;
uniform sampler2D u_vel;uniform vec2 px;
#pragma import(lib-bilinear)
void main(){ o = 0.5*((R(u_vel)-L(u_vel))/px.x + (T_(u_vel)-B(u_vel))/px.y); }
</script>

<!-- Jacobi pressure -->
<script id="fs-jacobi" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out float o;
uniform sampler2D u_p;uniform sampler2D u_b;uniform vec2 px;uniform float alpha;uniform float beta;
#pragma import(lib-bilinear)
void main(){
  float l=L(u_p), r=R(u_p), b=B(u_p), t=T_(u_p);
  float d=S(u_b,v_uv);
  o = (l+r+b+t + alpha*d)*beta;
}
</script>

<!-- subtract gradient -->
<script id="fs-grad" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out vec2 o;
uniform sampler2D u_vel;uniform sampler2D u_p;uniform vec2 px;
#pragma import(lib-bilinear)
void main(){
  float l=L(u_p), r=R(u_p), b=B(u_p), t=T_(u_p);
  vec2 v = texture(u_vel,v_uv).xy;
  o = v - 0.5*vec2(r-l,t-b);
}
</script>

<!-- curl -->
<script id="fs-curl" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out float o;
uniform sampler2D u_vel;uniform vec2 px;
void main(){
  float t = texture(u_vel,v_uv+vec2(0,px.y)).x;
  float b = texture(u_vel,v_uv-vec2(0,px.y)).x;
  float r = texture(u_vel,v_uv+vec2(px.x,0)).y;
  float l = texture(u_vel,v_uv-vec2(px.x,0)).y;
  o = r-l - (t-b);
}
</script>

<!-- vorticity confinement -->
<script id="fs-vort" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out vec2 o;
uniform sampler2D u_vel;uniform sampler2D u_curl;uniform vec2 px;uniform float eps;
void main(){
  float c=S(u_curl,v_uv);
  float cl=abs(S(u_curl,v_uv-vec2(px.x,0))), cr=abs(S(u_curl,v_uv+vec2(px.x,0)));
  float cb=abs(S(u_curl,v_uv-vec2(0,px.y))), ct=abs(S(u_curl,v_uv+vec2(0,px.y)));
  vec2 grad=vec2(cr-cl,ct-cb);
  vec2 N=normalize(grad)+1e-5;
  vec2 v=texture(u_vel,v_uv).xy+eps*vec2(N.y,-N.x)*c;
  o = v;
}
</script>

<!-- splat velocity -->
<script id="fs-inject-vel" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out vec2 o;
uniform sampler2D u_vel;uniform vec2 point;uniform vec2 force;uniform float radius;
void main(){
  vec2 v=texture(u_vel,v_uv).xy;
  float d=distance(v_uv,point);
  float k=exp(-pow(d/radius,2.0));
  o = v + force*k;
}
</script>

<!-- splat dye -->
<script id="fs-inject-dye" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out vec4 o;
uniform sampler2D u_dye;uniform vec2 point;uniform float radius;
void main(){
  vec4 col=texture(u_dye,v_uv);
  float d=distance(v_uv,point);
  float k=exp(-pow(d/radius,2.0));
  vec3 ink=vec3(0.9,0.1,1.0);                       // magenta‑blue
  o = mix(col,vec4(ink,1.0),k);
}
</script>

<!-- bloom threshold -->
<script id="fs-bright" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out vec4 o;
uniform sampler2D u_src;
void main(){
  vec3 c=texture(u_src,v_uv).rgb;
  float l=max(max(c.r,c.g),c.b);
  o = vec4(c*step(1.1,l),0.0);
}
</script>

<!-- gaussian blur (single pass horizontal / vertical) -->
<script id="fs-blur" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out vec4 o;
uniform sampler2D u_tex;uniform vec2 dir;uniform vec2 px;
void main(){
  vec3 s=vec3(0);
  float w[5]=float[](0.227,0.194,0.121,0.054,0.016);
  for(int i=-4;i<=4;i++){
    s+=texture(u_tex,v_uv+dir*float(i)*px).rgb*w[abs(i)];
  }
  o=vec4(s,1);
}
</script>

<!-- final composite -->
<script id="fs-final" type="x-shader/x-fragment">
#version 300 es
precision highp float; in vec2 v_uv; out vec4 o;
uniform sampler2D u_scene;uniform sampler2D u_bloom;
void main(){
  vec3 scene=texture(u_scene,v_uv).rgb;
  vec3 bloom=texture(u_bloom,v_uv).rgb*1.4;
  vec3 col=scene+bloom;                 // simple additive bloom
  // filmic tonemap
  col = col/(col+vec3(1.0));
  o=vec4(pow(col,vec3(0.454)),1);       // gamma 2.2
}
</script>

<script type="module">
const gl = document.getElementById('c').getContext('webgl2',{alpha:false});
if(!gl) throw 'WebGL2 unavailable';
if(!gl.getExtension('EXT_color_buffer_float')) throw 'no float RT support';

// resize
let W=0,H=0,simW=0,simH=0;resize();addEventListener('resize',resize);
function resize(){
  W=innerWidth;H=innerHeight;simW=W>>1;simH=H>>1;
  document.getElementById('c').width=W;document.getElementById('c').height=H;
  gl.viewport(0,0,W,H);
  initFBOs();
}

// geometry
const quad=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,quad);
gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);
const vao=gl.createVertexArray();gl.bindVertexArray(vao);
gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,2,gl.FLOAT,false,0,0);

// compile / link
function compile(id){
  const src=document.getElementById(id).text.replace(/#pragma import\\((.*?)\\)/g,
    (_,lib)=>document.getElementById(lib).text);
  const sh=gl.createShader(id=='vs'?gl.VERTEX_SHADER:gl.FRAGMENT_SHADER);
  gl.shaderSource(sh,src);gl.compileShader(sh);
  if(!gl.getShaderParameter(sh,gl.COMPILE_STATUS)) throw gl.getShaderInfoLog(sh);
  return sh;
}
function prog(fs){const p=gl.createProgram();
  gl.attachShader(p,compile('vs'));gl.attachShader(p,compile(fs));
  gl.linkProgram(p);if(!gl.getProgramParameter(p,gl.LINK_STATUS)) throw gl.getProgramInfoLog(p);
  return p;
}

// create textures / fbos
const RG16F=gl.RG16F, R16F=gl.R16F, RGBA16F=gl.RGBA16F, FLO=gl.FLOAT;
const linear=(t)=>{gl.texParameteri(3553,10241,9729);gl.texParameteri(3553,10240,9729);
                   gl.texParameteri(3553,10242,33071);gl.texParameteri(3553,10243,33071);return t;}
const tex=(w,h,ifmt,fmt,type)=>{const t=gl.createTexture();
  gl.bindTexture(3553,linear(t));gl.texImage2D(3553,0,ifmt,w,h,0,fmt,type,null);return t;}
const fb=(tex)=>{const f=gl.createFramebuffer();gl.bindFramebuffer(36160,f);
  gl.framebufferTexture2D(36160,36064,3553,tex,0);return f;}

let vel={},velFB={},curl,div,p={},pFB={},dye={},dyeFB={},bright,brightFB,blurX,blurXFB,blurY,blurYFB;
function makePair(w,h,ifmt,fmt){
  const a=tex(w,h,ifmt,fmt,FLO),b=tex(w,h,ifmt,fmt,FLO);
  return {read:a,write:b}, {read:fb(a),write:fb(b)};
}
function initFBOs(){
  [vel,velFB]=makePair(simW,simH,RG16F,gl.RG);
  curl   = tex(simW,simH,R16F,gl.RED,FLO);curlFB = fb(curl);
  div    = tex(simW,simH,R16F,gl.RED,FLO);divFB  = fb(div);
  [p,pFB]=makePair(simW,simH,R16F,gl.RED);
  [dye,dyeFB]=makePair(simW,simH,RGBA16F,gl.RGBA);
  bright = tex(simW,simH,RGBA16F,gl.RGBA,FLO);brightFB=fb(bright);
  blurX  = tex(simW,simH,RGBA16F,gl.RGBA,FLO);blurXFB =fb(blurX);
  blurY  = tex(simW,simH,RGBA16F,gl.RGBA,FLO);blurYFB =fb(blurY);
}

// swap helpers
const swap=(obj)=>{[obj.read,obj.write]=[obj.write,obj.read]};
const swapFB=(obj)=>{[obj.read,obj.write]=[obj.write,obj.read]};

// programs
const Padvect      = prog('fs-advect');
const PadvectS     = prog('fs-advect-scalar');
const Pdiv         = prog('fs-div');
const Pjacobi      = prog('fs-jacobi');
const Pgrad        = prog('fs-grad');
const Pcurl        = prog('fs-curl');
const Pvort        = prog('fs-vort');
const PinjVel      = prog('fs-inject-vel');
const PinjDye      = prog('fs-inject-dye');
const Pbright      = prog('fs-bright');
const Pblur        = prog('fs-blur');
const Pfinal       = prog('fs-final');

// common uniforms setter
function uni(pairs,prog){
  pairs.forEach(([name,val])=>{
    const loc=gl.getUniformLocation(prog,name);
    if(loc<0)return;
    if(typeof val=='number')gl.uniform1f(loc,val);
    else if(val.length==2)  gl.uniform2fv(loc,val);
    else if(val.length==3)  gl.uniform3fv(loc,val);
    else if(val.length==4)  gl.uniform4fv(loc,val);
  });
}

// render pass
function pass(prog,outFB,texBindings,uniforms=[]){
  gl.useProgram(prog);gl.bindFramebuffer(36160,outFB);
  let unit=0;
  texBindings.forEach(([name,tex])=>{
    const loc=gl.getUniformLocation(prog,name);
    gl.activeTexture(33984+unit);gl.bindTexture(3553,tex);
    gl.uniform1i(loc,unit++);});
  uni(uniforms,prog);
  gl.drawArrays(5,0,4);
}

// pointer
const pt={x:0,y:0,px:0,py:0,down:false,ink:false};
canvas.addEventListener('pointerdown',e=>{pt.down=true;pt.ink=e.shiftKey;set(e)});
addEventListener('pointerup',()=>pt.down=false);
canvas.addEventListener('pointermove',set);
function set(e){const r=canvas.getBoundingClientRect();pt.px=pt.x;pt.py=pt.y;
  pt.x=(e.clientX-r.left)/r.width;pt.y=1-(e.clientY-r.top)/r.height}

// constants
const px=[1/simW,1/simH], dt=1.0/60, jacobiIter=40;

// main loop
function step(){
  gl.bindVertexArray(vao);

  // advect velocity
  pass(Padvect,velFB.write,[['u_vel',vel.read],['u_src',vel.read]],[['px',px],['dt',dt]]);
  swap(vel);swapFB(velFB);

  // vorticity
  pass(Pcurl,curlFB,[['u_vel',vel.read]],[['px',px]]);
  pass(Pvort,velFB.write,[['u_vel',vel.read],['u_curl',curl]],[['px',px],['eps',30.0]]);
  swap(vel);swapFB(velFB);

  // divergence
  pass(Pdiv,divFB,[['u_vel',vel.read]],[['px',px]]);

  // pressure solve
  gl.bindFramebuffer(36160,pFB.read);gl.clearBufferfv(0,0,[0,0,0,0]);
  for(let i=0;i<jacobiIter;i++){
    pass(Pjacobi,pFB.write,[['u_p',p.read],['u_b',div]],[['px',px],['alpha',-1],['beta',0.25]]);
    swap(p);swapFB(pFB);
  }

  // projection
  pass(Pgrad,velFB.write,[['u_vel',vel.read],['u_p',p.read]],[['px',px]]);
  swap(vel);swapFB(velFB);

  // mouse forces
  if(pt.down){
    if(pt.ink){
      pass(PinjDye,dyeFB.write,[['u_dye',dye.read]],[['point',[pt.x,pt.y]],['radius',0.03]]);
      swap(dye);swapFB(dyeFB);
    }else{
      const f=[(pt.x-pt.px)*600,(pt.y-pt.py)*600];
      pass(PinjVel,velFB.write,[['u_vel',vel.read]],[['point',[pt.x,pt.y]],['force',f],['radius',0.02]]);
      swap(vel);swapFB(velFB);
    }
  }

  // advect dye
  pass(PadvectS,dyeFB.write,[['u_vel',vel.read],['u_src',dye.read]],[['px',px],['dt',dt]]);
  swap(dye);swapFB(dyeFB);

  // bloom pipeline
  pass(Pbright,brightFB,[['u_src',dye.read]]);
  pass(Pblur,blurXFB,[['u_tex',bright]],[['dir',[1,0]],['px',[1/simW,1/simH]]]);
  pass(Pblur,blurYFB,[['u_tex',blurX]],[['dir',[0,1]],['px',[1/simW,1/simH]]]);

  // composite
  pass(Pfinal,null,[['u_scene',dye.read],['u_bloom',blurY]]);

  requestAnimationFrame(step);
}
requestAnimationFrame(step);
</script>
</body>
</html>

