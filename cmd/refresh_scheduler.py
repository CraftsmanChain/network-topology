"""Independent per-environment refresh clocks with bounded concurrency."""
import fcntl
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

from multi_env_refresh import ROOT, dump_json, load_json, read_registry, refresh_enabled, resolve_path


def positive_seconds(value, fallback, minimum=1):
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return fallback


def epoch(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def due_at(spec, root):
    schedule = load_json(root / "refresh-schedule.json", {})
    finished = epoch(schedule.get("finished_at"))
    interval = positive_seconds(spec.get("topology_refresh_sec"), 300, 30)
    if finished:
        delay = interval if schedule.get("success") else positive_seconds(spec.get("refresh_retry_sec"), min(60, interval), 30)
        return finished + delay
    status = load_json(root / "status.json", {})
    return epoch(status.get("checked_at")) + interval if status.get("checked_at") else 0


def enabled_specs(registry, registry_dir):
    seen_codes, seen_roots = set(), set()
    result = []
    for spec in registry.get("environments") or []:
        code = str(spec.get("code") or "").strip()
        if not code or not spec.get("data_root") or not refresh_enabled(spec):
            continue
        root = resolve_path(registry_dir, spec["data_root"]).resolve()
        # Aliases sharing one data directory must never become duplicate writers.
        if code in seen_codes or root in seen_roots:
            continue
        seen_codes.add(code)
        seen_roots.add(root)
        result.append((spec, root))
    return result


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        process.wait()


class Scheduler:
    def __init__(self, max_workers=2, clock=time.time, spawn=subprocess.Popen):
        self.max_workers = max(1, max_workers)
        self.clock = clock
        self.spawn = spawn
        self.jobs = {}

    def tick(self, registry, registry_path):
        now = self.clock()
        for root, job in list(self.jobs.items()):
            process = job["process"]
            result = process.poll()
            timed_out = result is None and now - job["started"] >= job["timeout"]
            if timed_out:
                stop_process(process)
                result = 124
            if result is None:
                continue
            dump_json(root / "refresh-schedule.json", {
                "env": job["code"], "started_at": iso(job["started"]),
                "finished_at": iso(now), "duration_sec": round(now - job["started"], 2),
                "success": result == 0, "exit_code": result, "timed_out": timed_out,
            })
            print(f"[SCHEDULER] completed env={job['code']} exit={result} duration={now - job['started']:.1f}s", flush=True)
            del self.jobs[root]

        pending = [(due_at(spec, root), spec, root) for spec, root in enabled_specs(registry, registry_path.parent)
                   if root not in self.jobs]
        pending.sort(key=lambda item: (item[0], item[1]["code"]))
        for due, spec, root in pending:
            if len(self.jobs) >= self.max_workers or due > now:
                break
            root.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["ENV_REGISTRY"] = str(registry_path)
            env["PYTHONUNBUFFERED"] = "1"
            try:
                process = self.spawn([sys.executable, str(ROOT / "cmd/multi_env_refresh.py"), "--env", spec["code"]],
                                     cwd=str(ROOT), env=env, start_new_session=True)
            except OSError as error:
                dump_json(root / "refresh-schedule.json", {"env": spec["code"], "finished_at": iso(now), "success": False, "exit_code": 127})
                print(f"[SCHEDULER] launch failed env={spec['code']}: {error}", flush=True)
                continue
            self.jobs[root] = {"process": process, "started": now, "code": spec["code"],
                               "timeout": positive_seconds(spec.get("refresh_timeout_sec"), 1800, 60)}
            print(f"[SCHEDULER] started env={spec['code']} interval={positive_seconds(spec.get('topology_refresh_sec'), 300, 30)}s", flush=True)

    def close(self):
        for job in self.jobs.values():
            stop_process(job["process"])
        self.jobs.clear()


def run_scheduler():
    registry_path, _ = read_registry()
    lock_path = registry_path.parent / ".refresh-scheduler.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[ERROR] A refresh scheduler is already running", flush=True)
            return 1
        scheduler = Scheduler(positive_seconds(os.environ.get("TOPOLOGY_REFRESH_WORKERS"), 2))
        stopped = False

        def stop(_signum, _frame):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        tick_seconds = positive_seconds(os.environ.get("TOPOLOGY_REFRESH_TICK_SEC"), 10)
        try:
            while not stopped:
                # Re-read the registry each tick so per-environment settings are live.
                registry_path, registry = read_registry()
                scheduler.tick(registry, registry_path)
                for _ in range(tick_seconds):
                    if stopped:
                        break
                    time.sleep(1)
        finally:
            scheduler.close()
    return 0
