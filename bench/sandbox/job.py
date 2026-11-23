#!/usr/bin/env python3
"""Runs inside the sandbox container. Builds or runs one entry.

    job.py sdk     build the shared SDK library (/repo/build/sdk is writable)
    job.py build   compile /job/main.cpp into /job/app      -> /job/build.json
    job.py run     run /job/app on a virtual screen, record -> /job/run.json

The limits here are the test rules in bench/RULES.md. They apply to the
program, never to the model writing it.
"""
import json, math, os, subprocess, sys, time

SDK = "/repo/bench/sdk"
JOB = "/job"
W, H = 1280, 720
BUILD_LIMIT = 300        # seconds to compile
STARTUP_LIMIT = 30       # seconds for a window to appear
SETTLE = 2               # seconds of frames before recording starts
RECORD = 15              # seconds recorded
DRAG_AT = 5              # second of the recording when the scripted drag starts
POSTER_AT = 12           # second of the recording used for the still image


def write(name, obj):
    with open(os.path.join(JOB, name), "w") as f:
        json.dump(obj, f, indent=2)


def sdk():
    r = subprocess.run(["make", "-s", "-C", SDK, "-j8"])
    sys.exit(r.returncode)


def build():
    t0 = time.time()
    try:
        r = subprocess.run(["make", "-s", "-C", SDK, "app", "ENTRY=/job/main.cpp", "OUT=/job/app"],
                           capture_output=True, text=True, timeout=BUILD_LIMIT)
        log, ok, why = r.stdout + r.stderr, r.returncode == 0, None
    except subprocess.TimeoutExpired as e:
        log, ok, why = (e.stdout or b"").decode() + (e.stderr or b"").decode(), False, f"build exceeded {BUILD_LIMIT} s"
    open(os.path.join(JOB, "build.log"), "w").write(log)
    write("build.json", {"ok": ok, "seconds": round(time.time() - t0, 1), "error": why})


def drag_path():
    cx, cy = W * 0.58, H * 0.55          # clear of the ImGui panel in the top-left
    return [(cx + 380 * math.sin(i / 60 * 2 * math.pi), cy + 180 * math.sin(2 * i / 60 * 2 * math.pi))
            for i in range(61)]


def run():
    env = dict(os.environ, DISPLAY=":99", LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe")
    res = {"ok": False}
    xvfb = subprocess.Popen(["Xvfb", ":99", "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = rec = None
    try:
        time.sleep(1)
        log = open(os.path.join(JOB, "run.log"), "w")
        app = subprocess.Popen(["/job/app"], cwd=JOB, env=env, stdout=log, stderr=subprocess.STDOUT)
        t0 = time.time()
        while True:
            if app.poll() is not None:
                res.update(error=f"exited at startup with code {app.returncode}", exit_code=app.returncode, stage="startup")
                return
            if subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "."], env=env,
                              capture_output=True).stdout.strip():
                break
            if time.time() - t0 > STARTUP_LIMIT:
                res.update(error=f"no window within {STARTUP_LIMIT} s", stage="startup")
                return
            time.sleep(0.25)
        res["startup_seconds"] = round(time.time() - t0, 2)
        time.sleep(SETTLE)

        rec = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", f"{W}x{H}",
                                "-framerate", "30", "-i", ":99", "-t", str(RECORD), "-c:v", "libx264",
                                "-pix_fmt", "yuv420p", "-crf", "24", "-preset", "veryfast", "/job/capture.mp4"],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t_rec = time.time()
        time.sleep(DRAG_AT)
        pts = drag_path()
        subprocess.run(["xdotool", "mousemove", str(int(pts[0][0])), str(int(pts[0][1])), "mousedown", "1"], env=env)
        for x, y in pts[1:]:
            subprocess.run(["xdotool", "mousemove", str(int(x)), str(int(y))], env=env)
            time.sleep(0.08)
        subprocess.run(["xdotool", "mouseup", "1"], env=env)
        res["drag_window"] = [DRAG_AT, round(time.time() - t_rec, 2)]
        rec.wait(timeout=RECORD + 30)
        if app.poll() is not None:
            res.update(error=f"exited during the recording with code {app.returncode}",
                       exit_code=app.returncode, stage="run")
        else:
            res["ok"] = True
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(POSTER_AT), "-i", "/job/capture.mp4",
                        "-frames:v", "1", "-q:v", "3", "/job/poster.jpg"])
    finally:
        for p in (app, rec, xvfb):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        text = open(os.path.join(JOB, "run.log"), errors="replace").read() if os.path.exists(os.path.join(JOB, "run.log")) else ""
        res["validation_errors"] = text.count("VALIDATE_")
        res["shader_errors"] = text.count("SHADER_COMPILATION") + text.count("LINKING_FAILED")
        write("run.json", res)


if __name__ == "__main__":
    {"sdk": sdk, "build": build, "run": run}[sys.argv[1]]()
