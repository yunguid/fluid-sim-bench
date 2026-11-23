#!/usr/bin/env python3
"""Runs inside the sandbox container. Builds or tests one entry.

    job.py sdk     build the SDK library and the skeleton (/repo/build is writable;
                   this also fills Emscripten's cache, which is read-only afterwards)
    job.py build   compile /job/main.cpp to WebAssembly in /job/web  -> /job/build.json
    job.py run     load /job/web in headless Chromium and test it    -> /job/run.json

The limits here are the test rules in bench/RULES.md. They apply to the
program, never to the model writing it.
"""
import json, math, os, subprocess, sys, time, urllib.request, warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

SDK = "/repo/bench/sdk"
JOB = "/job"
W, H = 1280, 720
BUILD_LIMIT = 300        # seconds to compile
STARTUP_LIMIT = 30       # seconds for the program to start drawing frames
SETTLE = 2               # seconds of frames before the test timeline starts
RECORD = 15              # seconds in the test timeline
DRAG_AT = 5              # second of the timeline when the scripted drag starts
SHOTS = [1, 4, 7.5, 11, 14]   # seconds of the timeline when screenshots are taken
POSTER = 11              # the screenshot used as the entry's still image


def write(name, obj):
    with open(os.path.join(JOB, name), "w") as f:
        json.dump(obj, f, indent=2)


def sdk():
    for target in ([], ["skeleton"]):
        r = subprocess.run(["make", "-s", "-C", SDK, "-j8", *target])
        if r.returncode:
            sys.exit(r.returncode)


def build():
    t0 = time.time()
    env = dict(os.environ, EM_FROZEN_CACHE="1")
    try:
        r = subprocess.run(["make", "-s", "-C", SDK, "app", "ENTRY=/job/main.cpp", "OUT=/job/web"],
                           capture_output=True, text=True, timeout=BUILD_LIMIT, env=env)
        log, ok, why = r.stdout + r.stderr, r.returncode == 0, None
    except subprocess.TimeoutExpired as e:
        log, ok, why = (e.stdout or b"").decode() + (e.stderr or b"").decode(), False, f"build exceeded {BUILD_LIMIT} s"
    open(os.path.join(JOB, "build.log"), "w").write(log)
    size = sum(os.path.getsize(os.path.join(JOB, "web", f)) for f in ("app.js", "app.wasm")
               if os.path.exists(os.path.join(JOB, "web", f)))
    write("build.json", {"ok": ok, "seconds": round(time.time() - t0, 1), "error": why, "bytes": size})


def drag_path():
    cx, cy = W * 0.58, H * 0.55          # clear of the ImGui panel in the top-left
    return [(cx + 380 * math.sin(i / 60 * 2 * math.pi), cy + 180 * math.sin(2 * i / 60 * 2 * math.pi))
            for i in range(61)]


class PageHung(Exception):
    """The page stopped answering: the program is blocking the browser."""


class CDP:
    """Minimal Chrome DevTools Protocol client over one page target."""

    def __init__(self, ws_url):
        from websockets.sync.client import connect
        self.ws = connect(ws_url, max_size=None, open_timeout=10)
        self.next_id, self.console = 0, []

    def call(self, method, timeout=30, **params):
        self.next_id += 1
        my = self.next_id
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        deadline = time.time() + timeout
        while True:
            try:
                msg = json.loads(self.ws.recv(timeout=max(0.1, deadline - time.time())))
            except TimeoutError:
                raise PageHung(f"the page stopped responding for {timeout} s (the program blocks the browser)")
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self.event(msg)

    def event(self, msg):
        m, p = msg.get("method"), msg.get("params", {})
        if m == "Runtime.consoleAPICalled":
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
            self.console.append(f"[{p.get('type')}] {text}")
        elif m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            self.console.append(f"[exception] {d.get('exception', {}).get('description') or d.get('text')}")
        elif m == "Log.entryAdded":
            e = p.get("entry", {})
            self.console.append(f"[browser {e.get('level')}] {e.get('text')}")

    def eval(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("value")

    def mouse(self, kind, x, y, pressed=False):
        self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left" if kind != "mouseMoved" or pressed else "none",
                  buttons=1 if pressed or kind == "mousePressed" else 0, clickCount=1 if kind != "mouseMoved" else 0)

    def shot(self, path):
        import base64
        data = self.call("Page.captureScreenshot", format="jpeg", quality=85)["data"]
        open(path, "wb").write(base64.b64decode(data))


def image_stats(paths):
    """Per-screenshot brightness and contrast, and how much frames change."""
    from PIL import Image, ImageChops, ImageStat
    imgs = {t: Image.open(p).convert("L").resize((320, 180)) for t, p in paths.items()}
    stats = {str(t): {"mean": round(ImageStat.Stat(i).mean[0], 1), "stddev": round(ImageStat.Stat(i).stddev[0], 1)}
             for t, i in imgs.items()}
    def diff(a, b):
        return round(ImageStat.Stat(ImageChops.difference(imgs[a], imgs[b])).mean[0], 2) if a in imgs and b in imgs else None
    return {"frames": stats,
            "change_without_input": diff(1, 4),      # does it move on its own?
            "change_with_input": diff(4, 11)}        # does the drag do anything?


def run():
    res = {"ok": False}
    shots_dir = os.path.join(JOB, "shots")
    os.makedirs(shots_dir, exist_ok=True)
    procs, cdp = [], None
    try:
        procs.append(subprocess.Popen(["python3", "-m", "http.server", "8000", "--bind", "127.0.0.1",
                                       "-d", os.path.join(JOB, "web")],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        chrome_log = open(os.path.join(JOB, "chrome.log"), "w")
        procs.append(subprocess.Popen([
            "chromium", "--headless", "--no-sandbox", "--disable-dev-shm-usage",
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--no-first-run", "--mute-audio", "--user-data-dir=/tmp/chrome",
            f"--window-size={W},{H}", "--remote-debugging-port=9222", "about:blank"],
            stdout=chrome_log, stderr=subprocess.STDOUT))
        ws = None
        for _ in range(100):
            try:
                pages = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=2))
                ws = next(p["webSocketDebuggerUrl"] for p in pages if p.get("type") == "page")
                break
            except Exception:
                time.sleep(0.2)
        if not ws:
            raise RuntimeError("harness: Chromium did not start")
        cdp = CDP(ws)
        for m in ("Runtime.enable", "Log.enable", "Page.enable"):
            cdp.call(m)
        cdp.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
        cdp.call("Page.navigate", url="http://127.0.0.1:8000/index.html")

        t0 = time.time()
        while True:
            b = cdp.eval("JSON.stringify(window.__bench || null)")
            b = json.loads(b) if b else None
            if b and (b["aborted"] or b["errors"]):
                res.update(error=(b["errors"] or ["aborted"])[0][:300], stage="startup")
                return
            if b and b["appFrames"] >= 3:
                break
            if time.time() - t0 > STARTUP_LIMIT:
                res.update(error=f"no frames within {STARTUP_LIMIT} s", stage="startup")
                return
            time.sleep(0.25)
        res["startup_seconds"] = round(time.time() - t0, 2)
        time.sleep(SETTLE)

        pts = drag_path()
        timeline = [(t, "shot", t) for t in SHOTS]
        timeline += [(DRAG_AT - 0.1, "hover", pts[0]), (DRAG_AT, "press", pts[0])]
        timeline += [(DRAG_AT + 0.08 * i, "move", p) for i, p in enumerate(pts[1:], 1)]
        timeline += [(DRAG_AT + 0.08 * len(pts), "release", pts[-1])]
        timeline.sort(key=lambda e: e[0])
        frames0 = cdp.eval("__bench.appFrames")
        t_rec, shots, drag = time.time(), {}, []
        for at, kind, arg in timeline:
            time.sleep(max(0.0, t_rec + at - time.time()))
            if kind == "shot":
                shots[arg] = os.path.join(shots_dir, f"{arg:g}.jpg")
                cdp.shot(shots[arg])
            elif kind == "hover":
                cdp.mouse("mouseMoved", *arg)
            elif kind == "press":
                cdp.mouse("mousePressed", *arg)
                drag.append(round(time.time() - t_rec, 2))
            elif kind == "move":
                cdp.mouse("mouseMoved", *arg, pressed=True)
            else:
                cdp.mouse("mouseReleased", *arg)
                drag.append(round(time.time() - t_rec, 2))
        time.sleep(max(0.0, t_rec + RECORD - time.time()))
        res["drag_window"] = drag
        frames1 = cdp.eval("__bench.appFrames")
        res["fps_software"] = round((frames1 - frames0) / (time.time() - t_rec), 1)
        time.sleep(1)
        b = json.loads(cdp.eval("JSON.stringify(__bench)"))
        if b["aborted"] or b["errors"]:
            res.update(error=(b["errors"] or ["aborted"])[0][:300], stage="run")
        elif b["appFrames"] <= frames1:
            res.update(error="the program stopped drawing frames", stage="run")
        else:
            res["ok"] = True
        if POSTER in shots:
            import shutil
            shutil.copy(shots[POSTER], os.path.join(JOB, "poster.jpg"))
        res["image"] = image_stats(shots)
    except PageHung as e:
        res.update(error=str(e), stage="startup" if "startup_seconds" not in res else "run")
    except Exception as e:
        res.update(error=f"harness: {e}"[:300], stage="harness")
    finally:
        if cdp:
            try:
                cdp.ws.close()
            except Exception:
                pass
        for p in procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        lines = cdp.console if cdp else []
        open(os.path.join(JOB, "run.log"), "w").write("\n".join(lines) + "\n")
        text = "\n".join(lines)
        res["validation_errors"] = text.count("VALIDATE_")
        res["shader_errors"] = text.count("SHADER_COMPILATION") + text.count("LINKING_FAILED")
        res["console_errors"] = sum(1 for l in lines if l.startswith(("[error]", "[exception]")))
        write("run.json", res)


if __name__ == "__main__":
    {"sdk": sdk, "build": build, "run": run}[sys.argv[1]]()
