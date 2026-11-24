#!/usr/bin/env python3
"""Run the fluid-sim benchmark: generate, build, and run every model x trial.

    python3 bench/run.py                         # new run named after today, every model, 3 trials
    python3 bench/run.py --run 2026-11-23        # resume or extend a run
    python3 bench/run.py --only glm,kimi         # models whose slug contains any of these
    python3 bench/run.py --stage build           # stop after building (no recordings)
    python3 bench/run.py --budget 100            # optional: start no new API job past $100

The rules this enforces are in bench/RULES.md. In short: the model is never
limited in time or length beyond its provider's maximum; a stalled connection
is an infrastructure failure, retried and never scored; a bad answer is never
retried; the program is built and run in a sandbox under fixed test rules.

Every stage is resumable. Rerunning the same --run continues unfinished jobs
and retries infrastructure failures. Results go to results/<run>/<model>/t<n>/.
Only the Python standard library is used.
"""
import argparse, base64, concurrent.futures as cf, datetime, hashlib, json, os, re
import signal, subprocess, sys, threading, time, traceback, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "bench")
KEYS = os.path.expanduser("~/.config/fluid-bench/keys.env")
IMAGE = "fluid-bench-sandbox"

# Rules (bench/RULES.md). There is deliberately no limit on generation time.
API_IDLE_SECONDS = 900        # silence on an API stream before it counts as stalled
LOCAL_IDLE_SECONDS = 1800     # the same for a local model (large models load slowly)
LOCAL_NUM_CTX = 65536     # local context window: fits the model and its cache on the 4090 (RULES.md)
INFRA_RETRIES = 5             # attempts after an infrastructure failure
INFRA_BACKOFF = [30, 60, 120, 240, 480]


# ---------------------------------------------------------------- helpers

def load_keys():
    if os.path.exists(KEYS):
        for line in open(KEYS):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def build_prompt():
    tpl = open(os.path.join(BENCH, "prompt.md")).read()
    skel = open(os.path.join(BENCH, "sdk", "skeleton.cpp")).read().rstrip()
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


class InfraError(Exception):
    """The request failed for reasons outside the model: network, provider, rate limit."""


# ---------------------------------------------------------------- jobs

class Job:
    def __init__(self, model, provider, trial, run_dir):
        self.model, self.provider, self.trial = model, provider, trial
        self.slug = model["slug"]
        self.dir = os.path.join(run_dir, self.slug, f"t{trial}")
        os.makedirs(self.dir, exist_ok=True)
        self.meta_path = os.path.join(self.dir, "meta.json")
        self.meta = read_json(self.meta_path, {}) or {}
        self.meta.update({"slug": self.slug, "model": model["model"], "provider": model["provider"],
                          "trial": trial, "pin": model.get("pin"),
                          "effort": model.get("effort") or model.get("think")
                          or (f"model default ({model['effort_default']})" if model.get("effort_default") else None)})
        self.state = "queued"
        self.progress = 0          # characters streamed so far

    def save(self):
        write_json(self.meta_path, self.meta)

    def path(self, name):
        return os.path.join(self.dir, name)


# ---------------------------------------------------------------- generation

def stream_lines(url, body, headers, idle):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=idle) as r:   # timeout applies to each read
            for raw in r:
                yield raw.decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise InfraError(f"HTTP {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise InfraError(f"{type(e).__name__}: {e}")


def merge_details(acc, items):
    """Merge streamed reasoning_details fragments by index, as OpenRouter sends them."""
    for it in items or []:
        i = it.get("index", len(acc))
        cur = acc.setdefault(i, {})
        for k, v in it.items():
            if k in ("text", "summary", "data") and isinstance(v, str):
                cur[k] = cur.get(k, "") + v
            elif v is not None:
                cur[k] = v


def stream_openai(job, url, body, headers):
    text, reasoning, info, t0 = [], [], {}, time.time()
    details = {}
    for line in stream_lines(url, dict(body, stream=True), headers, API_IDLE_SECONDS):
        if not line.startswith("data:"):
            continue                       # keep-alive comments and blank lines
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        if "error" in chunk:
            raise InfraError(json.dumps(chunk["error"])[:500])
        for ch in chunk.get("choices", []):
            d = ch.get("delta") or {}
            if (d.get("content") or d.get("reasoning")) and "ttft" not in info:
                info["ttft"] = round(time.time() - t0, 2)
            if d.get("content"):
                text.append(d["content"])
            if d.get("reasoning"):
                reasoning.append(d["reasoning"])
            if d.get("reasoning_details"):
                merge_details(details, d["reasoning_details"])
            if ch.get("finish_reason"):
                info["finish_reason"] = ch["finish_reason"]
                if ch["finish_reason"] == "error":
                    raise InfraError("provider ended the stream with an error")
        if chunk.get("usage"):
            info["usage"] = chunk["usage"]
        if chunk.get("provider"):
            info["provider"] = chunk["provider"]
        job.progress = sum(map(len, text)) + sum(map(len, reasoning))
    if "finish_reason" not in info:
        raise InfraError("stream ended without a finish reason")
    info["reasoning_details"] = [details[k] for k in sorted(details)]
    return "".join(text), "".join(reasoning), info


def stream_ollama(job, url, body):
    text, reasoning, final, t0, ttft = [], [], {}, time.time(), None
    for line in stream_lines(url, dict(body, stream=True), {}, LOCAL_IDLE_SECONDS):
        if not line:
            continue
        chunk = json.loads(line)
        if chunk.get("error"):
            raise InfraError(chunk["error"][:500])
        msg = chunk.get("message") or {}
        if (msg.get("content") or msg.get("thinking")) and ttft is None:
            ttft = round(time.time() - t0, 2)
        if msg.get("content"):
            text.append(msg["content"])
        if msg.get("thinking"):
            reasoning.append(msg["thinking"])
        job.progress = sum(map(len, text)) + sum(map(len, reasoning))
        if chunk.get("done"):
            final = chunk
    if not final:
        raise InfraError("stream ended before the model finished")
    final["ttft"] = ttft
    return "".join(text), "".join(reasoning), final


_ollama_lock = threading.Lock()
_ollama_loaded = None


def ollama_prepare(base_url, model):
    """Load the model and read its full context window before timing starts."""
    global _ollama_loaded
    with _ollama_lock:
        info = http_json(base_url + "/api/show", {"model": model}, {}, timeout=120).get("model_info", {})
        ctx = max([int(v) for k, v in info.items() if k.endswith(".context_length")] or [131072])
        ctx = min(ctx, LOCAL_NUM_CTX)
        if _ollama_loaded != model:
            http_json(base_url + "/api/generate", {"model": model, "keep_alive": "60m",
                                                   "options": {"num_ctx": ctx}}, {}, timeout=LOCAL_IDLE_SECONDS)
            _ollama_loaded = model
        return ctx


def request_turn(job, messages):
    """One response. Returns a dict: text, reasoning, finish, usage numbers, and the
    assistant message to send back if the conversation must continue."""
    p, m = job.provider, job.model
    if p["kind"] == "openai":
        body = {"model": m["model"], "messages": messages, "usage": {"include": True}}
        if m.get("max_tokens"):
            body["max_tokens"] = m["max_tokens"]
        if m.get("effort"):
            body["reasoning"] = {"effort": m["effort"]}
        if m.get("pin"):
            body["provider"] = {"order": [m["pin"]], "allow_fallbacks": False}
        hdr = {"Authorization": "Bearer " + os.environ[p["key_env"]], "X-Title": "fluid-sim-bench"}
        text, reasoning, info = stream_openai(job, p["base_url"] + "/chat/completions", body, hdr)
        u = info.get("usage") or {}
        back = {"role": "assistant", "content": text}
        if info.get("reasoning_details"):
            back["reasoning_details"] = info["reasoning_details"]
        return {"text": text, "reasoning": reasoning, "finish": info.get("finish_reason"), "back": back,
                "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
                "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                "cost": u.get("cost") or 0, "served_by": info.get("provider"), "ttft": info.get("ttft"),
                "max_tokens": body.get("max_tokens")}
    ctx = ollama_prepare(p["base_url"], m["model"])
    body = {"model": m["model"], "messages": messages, "keep_alive": "60m",
            "options": {"num_ctx": ctx, "num_predict": -1}}
    if m.get("think") is not None and job.meta.get("effort") is not None:   # None: no thinking mode
        body["think"] = m["think"]
    try:
        text, reasoning, r = stream_ollama(job, p["base_url"] + "/api/chat", body)
    except InfraError as e:
        if "does not support thinking" not in str(e):
            raise
        body.pop("think", None)                    # the model has no thinking mode
        job.meta["effort"] = None
        text, reasoning, r = stream_ollama(job, p["base_url"] + "/api/chat", body)
    back = {"role": "assistant", "content": text}
    if reasoning:
        back["thinking"] = reasoning
    return {"text": text, "reasoning": reasoning, "finish": r.get("done_reason"), "back": back,
            "prompt_tokens": r.get("prompt_eval_count"), "completion_tokens": r.get("eval_count"),
            "cost": 0.0, "num_ctx": ctx, "ttft": r.get("ttft"),
            "tokens_per_s": round(r["eval_count"] / (r["eval_duration"] / 1e9), 1) if r.get("eval_duration") else None}


CONTINUE = ("You reached the output limit of a single response. Continue exactly where you stopped. "
            "Do not repeat anything you already wrote. If you stopped inside the code block, continue "
            "the code from the exact character where it ended, without opening a new code block.")
NO_PROGRESS_TURNS = 3   # continuations in a row with no new output at all -> stuck


def stitch(parts):
    """Join a continued answer. Drop a fence a continuation reopened mid-code."""
    out = parts[0] if parts else ""
    for nxt in parts[1:]:
        inside = out.count("```") % 2 == 1
        if inside:
            nxt = re.sub(r"^\s*```(?:cpp|c\+\+|cc|cxx)?[ \t]*\n", "", nxt, count=1)
        out += nxt
    return out


def generate(job, prompt):
    """Returns "ok", "failed" (the model's answer is unusable) or "infra-error".

    There is no length limit: when a response stops at the output limit, the
    conversation continues until the model finishes on its own."""
    if os.path.exists(job.path("main.cpp")):
        return "ok"
    job.state = "generating"
    for k in ("stage", "error", "exit_code", "run_ok", "build_ok", "infra_attempts", "turns"):
        job.meta.pop(k, None)
    messages = [{"role": "user", "content": prompt}]
    texts, reasonings, turns = [], [], []
    no_progress = 0
    t_start = time.time()
    while True:
        attempts = []
        for attempt in range(INFRA_RETRIES):
            try:
                t0 = time.time()
                r = request_turn(job, messages)
                r["seconds"] = round(time.time() - t0, 1)
                break
            except InfraError as e:
                attempts.append(str(e)[:300])
                job.meta["infra_attempts"] = attempts
                job.save()
                job.state = f"retry {attempt + 1}"
                time.sleep(INFRA_BACKOFF[attempt])
        else:
            job.meta.update({"stage": "generate", "error": attempts[-1]})
            return "infra-error"
        job.state = "generating"
        texts.append(r["text"]); reasonings.append(r["reasoning"])
        turns.append({k: r.get(k) for k in ("finish", "seconds", "ttft", "prompt_tokens", "completion_tokens",
                                             "reasoning_tokens", "cost", "served_by", "tokens_per_s")})
        tot = lambda k: sum(t.get(k) or 0 for t in turns)
        job.meta.update({"turns": len(turns), "turn_log": turns, "cost": tot("cost"),
                         "prompt_tokens": tot("prompt_tokens"), "completion_tokens": tot("completion_tokens"),
                         "reasoning_tokens": tot("reasoning_tokens") or None, "finish_reason": r["finish"],
                         "ttft_seconds": turns[0].get("ttft"), "served_by": r.get("served_by"),
                         "max_tokens": r.get("max_tokens"), "num_ctx": r.get("num_ctx"),
                         "tokens_per_s": r.get("tokens_per_s"), "gen_seconds": round(time.time() - t_start, 1)})
        job.save()
        if r["finish"] not in ("length", "max_tokens"):
            break                                   # the model finished on its own
        no_progress = no_progress + 1 if not (r["text"].strip() or r["reasoning"].strip()) and r["completion_tokens"] in (0, None) else 0
        if no_progress >= NO_PROGRESS_TURNS:
            job.meta.update({"stage": "generate", "error": f"stuck: {no_progress} continuations produced nothing"})
            break
        messages += [r["back"], {"role": "user", "content": CONTINUE}]

    text, reasoning = stitch(texts), "\n\n---- continued ----\n\n".join(x for x in reasonings if x)
    open(job.path("response.md"), "w").write(text)
    if reasoning:
        open(job.path("reasoning.md"), "w").write(reasoning)
    if job.meta.get("error"):
        return "failed"
    code = extract_code(text)
    if not code:
        job.meta.update({"stage": "generate",
                         "error": "empty response" if not text.strip() else "no C++ code block in the response"})
        return "failed"
    open(job.path("main.cpp"), "w").write(code)
    job.meta["lines"] = code.count("\n") + 1
    return "ok"


# ---------------------------------------------------------------- sandbox

def sandbox(job_dir, stage, timeout, writable_build=False):
    cmd = ["docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges", "--read-only", "--tmpfs", "/tmp:rw,size=1g",
           "--pids-limit", "1024", "--memory", "8g", "--cpus", "8", "--user", "1000:1000",
           "-v", f"{ROOT}/third_party:/repo/third_party:ro",
           "-v", f"{ROOT}/bench:/repo/bench:ro",
           "-v", f"{ROOT}/build:/repo/build:{'rw' if writable_build else 'ro'}"]
    if job_dir:
        cmd += ["-v", f"{job_dir}:/job:rw"]
    cmd += [IMAGE, "python3", "/repo/bench/sandbox/job.py", stage]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def build(job):
    if job.meta.get("build_ok"):
        return True
    job.state = "building"
    r = sandbox(job.dir, "build", timeout=420)
    res = read_json(job.path("build.json"))
    if res is None:
        raise InfraError(f"sandbox failed: {r.stderr[-300:]}")
    log = open(job.path("build.log"), errors="replace").read()
    job.meta.update({"build_ok": res["ok"], "build_seconds": res["seconds"], "wasm_bytes": res.get("bytes"),
                     "build_errors": log.count("error:"), "build_warnings": log.count("warning:")})
    if not res["ok"]:
        first = (re.findall(r".*error:.*", log) or [res.get("error") or log[-300:]])[0]
        job.meta.update({"stage": "build", "error": first[:300]})
    return res["ok"]


def run_app(job):
    if job.meta.get("run_ok") is not None:
        return job.meta["run_ok"]
    job.state = "running"
    r = sandbox(job.dir, "run", timeout=180)
    res = read_json(job.path("run.json"))
    if res is None:
        raise InfraError(f"sandbox failed: {r.stderr[-300:]}")
    if res.get("stage") == "harness":
        raise InfraError(res.get("error"))
    img = res.get("image") or {}
    job.meta.update({"run_ok": res["ok"], "startup_seconds": res.get("startup_seconds"),
                     "fps_software": res.get("fps_software"), "console_errors": res.get("console_errors"),
                     "validation_errors": res.get("validation_errors"), "shader_errors": res.get("shader_errors"),
                     "change_without_input": img.get("change_without_input"),
                     "change_with_input": img.get("change_with_input")})
    if not res["ok"]:
        job.meta.update({"stage": res.get("stage", "run"), "error": res.get("error")})
    return res["ok"]


# ---------------------------------------------------------------- status

class Status:
    """Live table on stdout plus OSC 7501 reports for Rex."""

    def __init__(self, jobs):
        self.jobs, self.lock, self.tty = jobs, threading.Lock(), sys.stdout.isatty()

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

    def render(self, spent, final=False):
        with self.lock:
            st = [j.meta.get("status") for j in self.jobs]
            settled = sum(1 for s in st if s in ("ok", "failed") or (s or "").endswith("-done"))
            total = len(self.jobs)
            pct = int(100 * settled / max(total, 1))
            summary = (f"{settled}/{total} settled, {st.count('ok')} ok, {st.count('failed')} failed, "
                       f"{st.count('infra-error')} infra, ${spent:.2f}")
            if final:
                self.osc(state="done", app="fluid-bench", title="fluid-sim-bench", msg=summary)
            else:
                self.osc(state="working", app="fluid-bench", title="fluid-sim-bench", progress=pct, msg=summary)
            if self.tty:
                sys.stdout.write("\033[H\033[2J")
            print(f"fluid-sim-bench  run {RUN}  {summary}  ({pct}%)\n")
            by_model = {}
            for j in self.jobs:
                by_model.setdefault(j.slug, []).append(j)
            for slug, js in by_model.items():
                cells = "  ".join(f"t{j.trial}:" + (f"gen {j.progress // 1000}k" if j.state == "generating"
                                                     else j.state)[:11].ljust(11) for j in js)
                cost = sum(j.meta.get("cost") or 0 for j in js)
                print(f"  {slug:<28} {cells}  ${cost:.3f}")
            sys.stdout.flush()


# ---------------------------------------------------------------- main

def git_commit():
    r = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "bench", "third_party"],
                           capture_output=True, text=True).stdout.strip()
    return r.stdout.strip() + ("+dirty" if dirty else "")


def write_manifest(run_dir, cfg, models, prompt, args):
    image = subprocess.run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
                           capture_output=True, text=True).stdout.strip()
    path = os.path.join(run_dir, "manifest.json")
    man = read_json(path, {}) or {}
    man.setdefault("sessions", []).append({
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(), "rules_sha256": sha256(os.path.join(BENCH, "RULES.md")),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
        "sandbox_image": image[:19], "trials": args.trials, "budget_usd": args.budget,
        "models": models})
    write_json(path, man)


def write_index(run_dir, jobs, prompt):
    entries = [dict(j.meta, dir=os.path.relpath(j.dir, run_dir)) for j in jobs]
    write_json(os.path.join(run_dir, "index.json"),
               {"run": RUN, "updated": datetime.datetime.now().isoformat(timespec="seconds"),
                "prompt": prompt, "entries": entries})
    runs_path = os.path.join(os.path.dirname(run_dir), "runs.json")
    runs = read_json(runs_path, []) or []
    if RUN not in runs:
        write_json(runs_path, sorted(runs + [RUN], reverse=True))


def main():
    global RUN
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=datetime.date.today().isoformat())
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--stage", choices=["generate", "build", "run"], default="run")
    ap.add_argument("--budget", type=float, default=None, help="optional USD cap; no new API job starts past it")
    ap.add_argument("--run-workers", type=int, default=3)
    ap.add_argument("--retry-failed", action="store_true", help="also redo jobs the model failed (normally never)")
    a = ap.parse_args()
    RUN = a.run

    load_keys()
    cfg = json.load(open(os.path.join(BENCH, "models.json")))
    prompt = build_prompt()
    run_dir = os.path.join(ROOT, "results", RUN)
    os.makedirs(run_dir, exist_ok=True)
    open(os.path.join(run_dir, "prompt.md"), "w").write(prompt)

    only = [s for s in a.only.split(",") if s]
    models = [m for m in cfg["models"] if not only or any(s in m["slug"] for s in only)]
    write_manifest(run_dir, cfg, models, prompt, a)
    jobs = [Job(m, cfg["providers"][m["provider"]], t, run_dir) for m in models for t in range(1, a.trials + 1)]
    for j in jobs:
        s = j.meta.get("status")
        if s == "ok" or (s == "failed" and not a.retry_failed):
            j.state = s
    status = Status(jobs)
    spent_lock = threading.Lock()
    spent = lambda: sum(j.meta.get("cost") or 0 for j in jobs)

    os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
    r = sandbox(None, "sdk", timeout=900, writable_build=True)
    if r.returncode != 0:
        sys.exit(f"building the SDK in the sandbox failed:\n{r.stdout[-2000:]}{r.stderr[-2000:]}")

    def finish(job, status_value, state=None):
        job.meta["status"] = status_value
        job.meta["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        job.state = state or status_value
        job.save()

    post = cf.ThreadPoolExecutor(a.run_workers)

    def after_generate(job):
        try:
            if a.stage == "generate":
                return finish(job, "generate-done", "generated")
            if not build(job):
                return finish(job, "failed")
            if a.stage == "build":
                return finish(job, "build-done", "built")
            finish(job, "ok" if run_app(job) else "failed")
        except InfraError as e:
            job.meta.update({"error": str(e)[:300]})
            finish(job, "infra-error")
        except Exception:
            job.meta.update({"error": traceback.format_exc()[-500:]})
            finish(job, "infra-error")

    def run_one(job):
        if job.state in ("ok", "failed"):
            return
        try:
            if job.provider["kind"] == "openai" and not os.path.exists(job.path("main.cpp")):
                with spent_lock:
                    if a.budget is not None and spent() >= a.budget:
                        return finish(job, "budget-skip")
            g = generate(job, prompt)
            if g != "ok":
                return finish(job, g)
            job.state = "queued-run"
            job.save()
            post.submit(after_generate, job)
        except Exception:
            job.meta.update({"stage": "generate", "error": traceback.format_exc()[-500:]})
            finish(job, "infra-error")

    pools = {name: cf.ThreadPoolExecutor(p.get("concurrency", 4)) for name, p in cfg["providers"].items()}
    futures = [pools[j.model["provider"]].submit(run_one, j) for j in jobs]  # local jobs stay grouped by model

    stop = threading.Event()
    def ticker():
        while not stop.is_set():
            status.render(spent())
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
    status.render(spent(), final=True)


if __name__ == "__main__":
    main()
