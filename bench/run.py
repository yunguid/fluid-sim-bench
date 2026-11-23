#!/usr/bin/env python3
"""Run the fluid-sim benchmark: generate, build, and capture every model x trial.

    python3 bench/run.py                       # new run, every model, 3 trials
    python3 bench/run.py --run 2026-11-23a     # resume or extend a run
    python3 bench/run.py --only glm,kimi       # models whose slug contains any of these
    python3 bench/run.py --stage generate      # stop after generating code

Every stage is resumable: a stage whose output already exists is skipped, so
rerunning the same --run picks up where it stopped. Results go to
results/<run>/<model>/t<n>/. The terminal shows a live table, and the run
reports its state to the terminal with OSC 7501 (Program Status Protocol), so
Rex can show progress on the tab.

Only the Python standard library is used.
"""
import argparse, base64, concurrent.futures as cf, datetime, json, math, os, re
import shutil, signal, subprocess, sys, threading, time, traceback, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "bench")
SDK = os.path.join(BENCH, "sdk")
KEYS = os.path.expanduser("~/.config/fluid-bench/keys.env")
W, H = 1280, 720
CAPTURE_SECONDS = 14
OUTPUT_LIMITS = {}   # OpenRouter model id -> the provider's max output tokens, filled at start


def load_output_limits():
    try:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as r:
            for m in json.load(r)["data"]:
                lim = (m.get("top_provider") or {}).get("max_completion_tokens")
                if lim:
                    OUTPUT_LIMITS[m["id"]] = int(lim)
    except Exception as e:
        print(f"warning: could not read OpenRouter output limits ({e}); sending no max_tokens", file=sys.stderr)


# ---------------------------------------------------------------- utilities

def load_keys():
    if not os.path.exists(KEYS):
        return
    for line in open(KEYS):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def build_prompt():
    tpl = open(os.path.join(BENCH, "prompt.md")).read()
    skel = open(os.path.join(SDK, "skeleton.cpp")).read().rstrip()
    return tpl.replace("{{SKELETON}}", skel)


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


def http_json(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def extract_code(text):
    blocks = re.findall(r"```(?:cpp|c\+\+|cc|cxx|C\+\+)?[ \t]*\n(.*?)```", text, re.S)
    blocks = [b for b in blocks if "sokol_main" in b or "#include" in b] or blocks
    return max(blocks, key=len) if blocks else None


# ---------------------------------------------------------------- status

class Status:
    """Live table on stdout plus OSC 7501 reports for Rex."""

    def __init__(self, jobs):
        self.jobs = jobs
        self.lock = threading.Lock()
        self.tty = sys.stdout.isatty()

    def osc(self, **kv):
        if not self.tty:
            return
        parts = []
        for k, v in kv.items():
            if v is None:
                continue
            if k in ("msg", "title"):
                v = base64.b64encode(str(v).encode()[:180]).decode()
            parts.append(f"{k}={v}")
        sys.stdout.write("\033]7501;" + ":".join(parts) + "\033\\")
        sys.stdout.flush()

    def counts(self):
        c = {}
        for j in self.jobs:
            c[j.state] = c.get(j.state, 0) + 1
        return c

    def render(self, final=False):
        with self.lock:
            done = sum(1 for j in self.jobs if j.state in ("ok", "failed"))
            total = len(self.jobs)
            pct = int(100 * done / max(total, 1))
            c = self.counts()
            failed = c.get("failed", 0)
            summary = f"{done}/{total} done, {c.get('ok', 0)} ok, {failed} failed"
            if final:
                state = "error" if failed == total else "done"
                self.osc(state=state, app="fluid-bench", title="fluid-sim-bench", msg=summary)
            else:
                self.osc(state="working", app="fluid-bench", title="fluid-sim-bench",
                         progress=pct, msg=summary)
            if self.tty:
                sys.stdout.write("\033[H\033[2J")
            print(f"fluid-sim-bench  run {RUN}  {summary}  ({pct}%)\n")
            by_model = {}
            for j in self.jobs:
                by_model.setdefault(j.slug, []).append(j)
            for slug, js in by_model.items():
                cells = "  ".join(f"t{j.trial}:{(j.state if j.state != 'generating' else f'gen {j.progress // 1000}k'):<10}" for j in js)
                cost = sum(j.meta.get("cost") or 0 for j in js)
                print(f"  {slug:<28} {cells}  ${cost:.3f}")
            sys.stdout.flush()


# ---------------------------------------------------------------- jobs

class Job:
    def __init__(self, model, provider, trial, run_dir):
        self.model, self.provider, self.trial = model, provider, trial
        self.slug = model["slug"]
        self.dir = os.path.join(run_dir, self.slug, f"t{trial}")
        os.makedirs(self.dir, exist_ok=True)
        self.meta_path = os.path.join(self.dir, "meta.json")
        self.meta = read_json(self.meta_path, {}) or {}
        self.meta.update({"slug": self.slug, "model": model["model"],
                          "provider": model["provider"], "trial": trial})
        self.state = "queued"
        self.progress = 0          # characters streamed so far

    def save(self):
        write_json(self.meta_path, self.meta)

    def path(self, name):
        return os.path.join(self.dir, name)


# A request fails only when the connection goes silent for this long. There is
# no limit on total generation time: a model may think as long as it wants.
API_IDLE_SECONDS = 900
LOCAL_IDLE_SECONDS = 1800


def stream_lines(url, body, headers, idle):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=idle) as r:   # timeout applies to each read
        for raw in r:
            yield raw.decode("utf-8", "replace").strip()


def stream_openai(job, url, body, headers):
    """OpenAI-style SSE stream (OpenRouter). Returns (text, reasoning, final_chunk_info)."""
    text, reasoning, info = [], [], {}
    for line in stream_lines(url, dict(body, stream=True), headers, API_IDLE_SECONDS):
        if not line.startswith("data:"):
            continue                       # ": OPENROUTER PROCESSING" keep-alives, blank lines
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        if "error" in chunk:
            raise RuntimeError(json.dumps(chunk["error"])[:500])
        for ch in chunk.get("choices", []):
            d = ch.get("delta") or {}
            if d.get("content"):
                text.append(d["content"])
            if d.get("reasoning"):
                reasoning.append(d["reasoning"])
            if ch.get("finish_reason"):
                info["finish_reason"] = ch["finish_reason"]
        if chunk.get("usage"):
            info["usage"] = chunk["usage"]
        if chunk.get("provider"):
            info["provider"] = chunk["provider"]
        job.progress = sum(map(len, text)) + sum(map(len, reasoning))
    return "".join(text), "".join(reasoning), info


def stream_ollama(job, url, body):
    text, reasoning, final = [], [], {}
    for line in stream_lines(url, dict(body, stream=True), {}, LOCAL_IDLE_SECONDS):
        if not line:
            continue
        chunk = json.loads(line)
        if chunk.get("error"):
            raise RuntimeError(chunk["error"][:500])
        msg = chunk.get("message") or {}
        if msg.get("content"):
            text.append(msg["content"])
        if msg.get("thinking"):
            reasoning.append(msg["thinking"])
        job.progress = sum(map(len, text)) + sum(map(len, reasoning))
        if chunk.get("done"):
            final = chunk
    return "".join(text), "".join(reasoning), final


def ollama_context_length(base_url, model):
    """The model's full trained context window, from /api/show."""
    try:
        info = http_json(base_url + "/api/show", {"model": model}, {}, timeout=60).get("model_info", {})
        return max(int(v) for k, v in info.items() if k.endswith(".context_length"))
    except Exception:
        return 131072


def generate(job, prompt):
    if os.path.exists(job.path("main.cpp")):
        return True
    job.state = "generating"
    for k in ("stage", "error", "exit_code", "status", "run_ok", "build_ok"):
        job.meta.pop(k, None)   # clear a previous failed attempt
    p, m = job.provider, job.model
    t0 = time.time()
    last_err = None
    for attempt in range(3):
        try:
            if p["kind"] == "openai":
                body = {"model": m["model"], "messages": [{"role": "user", "content": prompt}],
                        "usage": {"include": True}}
                if m["model"] in OUTPUT_LIMITS:
                    body["max_tokens"] = OUTPUT_LIMITS[m["model"]]
                hdr = {"Authorization": "Bearer " + os.environ[p["key_env"]], "X-Title": "fluid-sim-bench"}
                text, reasoning, info = stream_openai(job, p["base_url"] + "/chat/completions", body, hdr)
                u = info.get("usage") or {}
                job.meta.update({"prompt_tokens": u.get("prompt_tokens"),
                                 "completion_tokens": u.get("completion_tokens"),
                                 "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                                 "cost": u.get("cost"),
                                 "finish_reason": info.get("finish_reason"),
                                 "max_tokens": body.get("max_tokens"),
                                 "served_by": info.get("provider")})
            else:
                ctx = ollama_context_length(p["base_url"], m["model"])
                body = {"model": m["model"], "messages": [{"role": "user", "content": prompt}],
                        "options": {"num_ctx": ctx, "num_predict": -1}}
                text, reasoning, r = stream_ollama(job, p["base_url"] + "/api/chat", body)
                job.meta.update({"prompt_tokens": r.get("prompt_eval_count"),
                                 "completion_tokens": r.get("eval_count"), "cost": 0.0,
                                 "num_ctx": ctx, "finish_reason": r.get("done_reason"),
                                 "tokens_per_s": round(r["eval_count"] / (r["eval_duration"] / 1e9), 1)
                                 if r.get("eval_duration") else None})
            break
        except Exception as e:  # dropped connections, 5xx, rate limits
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(15 * (attempt + 1))
    else:
        job.meta.update({"stage": "generate", "error": last_err})
        return False
    if reasoning:
        open(job.path("reasoning.md"), "w").write(reasoning)
    job.meta["gen_seconds"] = round(time.time() - t0, 1)
    open(job.path("response.md"), "w").write(text)
    code = extract_code(text)
    if not code:
        cut = job.meta.get("finish_reason") in ("length", "max_tokens")
        job.meta.update({"stage": "generate",
                         "error": "truncated: hit the output token limit" if cut else "no C++ code block in the response"})
        return False
    open(job.path("main.cpp"), "w").write(code)
    job.meta["lines"] = code.count("\n") + 1
    return True


def build(job):
    exe = job.path("app")
    if os.path.exists(exe) and job.meta.get("build_ok"):
        return True
    job.state = "building"
    t0 = time.time()
    try:
        r = subprocess.run(["make", "-s", "-C", SDK, "app", f"ENTRY={job.path('main.cpp')}", f"OUT={exe}"],
                           capture_output=True, text=True, timeout=300)
        log, ok = r.stdout + r.stderr, r.returncode == 0
    except subprocess.TimeoutExpired:
        log, ok = "build timed out after 300 s", False
    open(job.path("build.log"), "w").write(log)
    job.meta.update({"build_ok": ok, "build_seconds": round(time.time() - t0, 1),
                     "build_errors": log.count("error:"), "build_warnings": log.count("warning:")})
    if not ok:
        job.meta.update({"stage": "build", "error": (re.findall(r".*error:.*", log) or [log[-300:]])[0][:300]})
    return ok


# The same scripted mouse path for every entry: drags through the middle of the
# window, clear of the ImGui panel in the top-left corner.
def drag_script():
    pts = []
    cx, cy = W * 0.58, H * 0.55
    for i in range(60):                       # a figure-eight
        a = i / 60 * 6.283
        pts.append((cx + 380 * math.sin(a), cy + 180 * math.sin(2 * a)))
    return pts


def capture(job, display):
    if os.path.exists(job.path("capture.mp4")) and job.meta.get("run_ok") is not None:
        return job.meta["run_ok"]
    job.state = "capturing"
    env = dict(os.environ, DISPLAY=f":{display}", LIBGL_ALWAYS_SOFTWARE="1",
               GALLIUM_DRIVER="llvmpipe", LP_NUM_THREADS="6")
    xvfb = subprocess.Popen(["Xvfb", f":{display}", "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = rec = None
    try:
        time.sleep(1.0)
        runlog = open(job.path("run.log"), "w")
        app = subprocess.Popen([job.path("app")], cwd=job.dir, env=env, stdout=runlog, stderr=subprocess.STDOUT)
        t_start = time.time()
        while time.time() - t_start < 30:          # wait for a visible window
            if app.poll() is not None:
                job.meta.update({"run_ok": False, "stage": "run", "exit_code": app.returncode,
                                 "error": f"exited at startup with code {app.returncode}"})
                return False
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "."], env=env,
                               capture_output=True, text=True)
            if r.stdout.strip():
                break
            time.sleep(0.5)
        else:
            job.meta.update({"run_ok": False, "stage": "run", "error": "no window within 30 s"})
            return False
        job.meta["startup_seconds"] = round(time.time() - t_start, 1)
        time.sleep(2.0)                             # let the first frames render
        rec = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", f"{W}x{H}",
                                "-framerate", "30", "-i", f":{display}", "-t", str(CAPTURE_SECONDS),
                                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", "-preset", "veryfast",
                                job.path("capture.mp4")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(3.0)                              # let the automatic splats show first
        pts = drag_script()
        x0, y0 = pts[0]
        subprocess.run(["xdotool", "mousemove", str(int(x0)), str(int(y0)), "mousedown", "1"], env=env)
        for x, y in pts[1:]:
            subprocess.run(["xdotool", "mousemove", str(int(x)), str(int(y))], env=env)
            time.sleep(0.07)
        subprocess.run(["xdotool", "mouseup", "1"], env=env)
        rec.wait(timeout=CAPTURE_SECONDS + 30)
        crashed = app.poll() is not None
        job.meta.update({"run_ok": not crashed})
        if crashed:
            job.meta.update({"stage": "run", "exit_code": app.returncode,
                             "error": f"exited during capture with code {app.returncode}"})
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "10", "-i", job.path("capture.mp4"),
                        "-frames:v", "1", job.path("poster.jpg")])
        return not crashed
    finally:
        for p in (app, rec):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        xvfb.terminate()
        log = open(job.path("run.log")).read() if os.path.exists(job.path("run.log")) else ""
        job.meta["validation_errors"] = log.count("VALIDATE_")
        job.meta["gl_errors"] = log.count("GL_") + log.count("SHADER_COMPILATION")


def finish(job, ok):
    job.state = "ok" if ok else "failed"
    job.meta["status"] = job.state
    job.meta["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    job.save()


def write_index(run_dir, jobs, prompt):
    entries = [dict(j.meta, dir=os.path.relpath(j.dir, run_dir)) for j in jobs]
    write_json(os.path.join(run_dir, "index.json"),
               {"run": RUN, "updated": datetime.datetime.now().isoformat(timespec="seconds"),
                "prompt": prompt, "entries": entries})
    runs_path = os.path.join(os.path.dirname(run_dir), "runs.json")
    runs = read_json(runs_path, []) or []
    if RUN not in runs:
        write_json(runs_path, sorted(runs + [RUN], reverse=True))


# ---------------------------------------------------------------- main

def main():
    global RUN
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=datetime.date.today().isoformat())
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--stage", choices=["generate", "build", "capture"], default="capture")
    ap.add_argument("--capture-workers", type=int, default=3)
    a = ap.parse_args()
    RUN = a.run

    load_keys()
    load_output_limits()
    cfg = json.load(open(os.path.join(BENCH, "models.json")))
    prompt = build_prompt()
    run_dir = os.path.join(ROOT, "results", RUN)
    os.makedirs(run_dir, exist_ok=True)
    open(os.path.join(run_dir, "prompt.md"), "w").write(prompt)

    only = [s for s in a.only.split(",") if s]
    models = [m for m in cfg["models"] if not only or any(s in m["slug"] for s in only)]
    jobs = [Job(m, cfg["providers"][m["provider"]], t, run_dir)
            for m in models for t in range(1, a.trials + 1)]
    for j in jobs:
        if j.meta.get("status") == "ok":
            j.state = "ok"
    status = Status(jobs)

    subprocess.run(["make", "-s", "-C", SDK, "-j8"], check=True)

    displays = list(range(91, 91 + a.capture_workers))
    disp_lock = threading.Lock()
    post = cf.ThreadPoolExecutor(a.capture_workers)

    def after_generate(job):
        try:
            if a.stage == "generate":
                return finish(job, True)
            if not build(job):
                return finish(job, False)
            if a.stage == "build":
                return finish(job, True)
            with disp_lock:
                d = displays.pop()
            try:
                ok = capture(job, d)
            finally:
                with disp_lock:
                    displays.append(d)
            finish(job, ok)
        except Exception:
            job.meta.update({"error": traceback.format_exc()[-500:]})
            finish(job, False)

    def run_one(job):
        if job.state == "ok":
            return
        try:
            if not generate(job, prompt):
                return finish(job, False)
            job.state = "queued-cap"
            job.save()
            post.submit(after_generate, job)
        except Exception:
            job.meta.update({"stage": "generate", "error": traceback.format_exc()[-500:]})
            finish(job, False)

    pools = {name: cf.ThreadPoolExecutor(p.get("concurrency", 4)) for name, p in cfg["providers"].items()}
    futures = [pools[j.model["provider"]].submit(run_one, j) for j in jobs]  # ollama jobs stay grouped by model

    stop = threading.Event()
    def ticker():
        while not stop.is_set():
            status.render()
            write_index(run_dir, jobs, prompt)
            stop.wait(3)
    threading.Thread(target=ticker, daemon=True).start()

    def on_int(*_):
        status.osc(state="idle", app="fluid-bench", title="fluid-sim-bench")
        os._exit(130)
    signal.signal(signal.SIGINT, on_int)

    cf.wait(futures)
    for p in pools.values():
        p.shutdown(wait=True)
    post.shutdown(wait=True)
    stop.set()
    write_index(run_dir, jobs, prompt)
    status.render(final=True)


if __name__ == "__main__":
    main()
