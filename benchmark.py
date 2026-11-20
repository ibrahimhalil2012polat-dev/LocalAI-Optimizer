import ctypes
import json
import time
import threading
import http.client
import socket
import psutil

from pathlib import Path
from datetime import datetime
from compatibility import api, estimate_kv_cache

GB = 1024 ** 3
MB = 1024 ** 2
CONTEXT = 2048


class PerformanceInfo(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", ctypes.c_ulong),
        ("ProcessCount", ctypes.c_ulong),
        ("ThreadCount", ctypes.c_ulong),
    ]


def commit_available():
    try:
        info = PerformanceInfo()
        info.cb = ctypes.sizeof(info)
        success = ctypes.windll.psapi.GetPerformanceInfo(
            ctypes.byref(info), info.cb
        )
        if success:
            return max(
                0, (info.CommitLimit - info.CommitTotal) * info.PageSize
            )
    except Exception:
        pass
    return None


def memory_limits(total):
    reserve = max(512 * MB, min(1.5 * GB, total * 0.05))
    critical = max(256 * MB, min(512 * MB, total * 0.02))
    return reserve, critical


def safety_check(model):
    print("\n=== ADAPTIVE SAFETY CHECK ===")
    name = model["name"]
    size = model.get("size", 0)

    if (
        size <= 0 or name.endswith(":cloud")
        or model.get("remote_host") or model.get("remote_model")
    ):
        print("BLOCKED: Unknown or remote model.")
        return False

    if api("/api/ps").get("models"):
        print("BLOCKED: Other models are loaded.")
        return False

    metadata = api("/api/show", {"model": name})
    info = metadata.get("model_info") or {}
    arch = info.get("general.architecture")
    max_context = info.get(f"{arch}.context_length", 0)

    if max_context and CONTEXT > int(max_context):
        print("BLOCKED: Context exceeds model limit.")
        return False

    kv = estimate_kv_cache(info, CONTEXT)
    if kv is None:
        print("BLOCKED: Unsupported architecture.")
        return False

    ram = psutil.virtual_memory()
    reserve, critical = memory_limits(ram.total)
    overhead = max(GB, size * 0.15)
    required = size + kv + overhead
    remaining = ram.available - required
    commit = commit_available()

    print(f"Available RAM: {ram.available / GB:.3f} GiB")
    print(f"Estimated model RAM: {required / GB:.3f} GiB")
    print(f"Predicted remaining RAM: {remaining / GB:.3f} GiB")
    print(f"Dynamic reserve: {reserve / GB:.3f} GiB")

    if commit is not None:
        print(f"Commit headroom: {commit / GB:.3f} GiB")
        if commit < reserve:
            print("BLOCKED: Low commit headroom.")
            return False

    if remaining < reserve:
        print("BLOCKED: Insufficient estimated memory.")
        print("No benchmark was started.")
        return False

    print("STATUS: ELIGIBLE FOR CONTROLLED TEST")
    return True


def benchmark(model):
    name = model["name"]

    if not safety_check(model):
        return
    if input("\nStart benchmark? (y/n): ").strip().lower() != "y":
        return
    if not safety_check(model):
        return

    options = {
        "num_ctx": CONTEXT,
        "num_predict": 128,
        "num_batch": 128,
        "num_gpu": 0,
        "temperature": 0,
        "seed": 42
    }
    payload = {
        "model": name,
        "prompt": "Explain Python functions with a simple example.",
        "stream": True,
        "keep_alive": 0,
        "options": options
    }

    samples = []
    stop = threading.Event()
    cancelled = threading.Event()
    reasons = []
    initial_ram = psutil.virtual_memory()
    baseline = initial_ram.available
    reserve, critical = memory_limits(initial_ram.total)

    connection = http.client.HTTPConnection(
        "127.0.0.1", 11434, timeout=180
    )
    connection.connect()
    active_socket = connection.sock

    def cancel(reason):
        reasons.append(reason)
        cancelled.set()
        print(f"\nSAFETY STOP: {reason}", flush=True)
        try:
            active_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def monitor():
        critical_samples = 0
        warned = False
        psutil.cpu_percent(interval=None)

        try:
            while not stop.is_set():
                now = time.monotonic()
                ram = psutil.virtual_memory()
                commit = commit_available()
                disk = psutil.disk_io_counters()

                sample = {
                    "time": now,
                    "available": ram.available,
                    "commit_available": commit,
                    "cpu": psutil.cpu_percent(interval=None),
                    "disk_bytes": (
                        disk.read_bytes + disk.write_bytes if disk else None
                    )
                }
                samples.append(sample)

                low_physical = ram.available < critical
                low_commit = commit is not None and commit < critical

                if low_physical or low_commit:
                    critical_samples += 1
                else:
                    critical_samples = 0

                if critical_samples >= 3:
                    cancel("Critical memory pressure.")
                    return

                if len(samples) >= 6:
                    old = samples[-6]
                    elapsed = now - old["time"]
                    falling = (old["available"] - ram.available) / max(elapsed, 0.01)
                    predicted = ram.available - falling * 2

                    if (
                        falling > 128 * MB
                        and ram.available < reserve
                        and predicted < critical
                    ):
                        cancel("Rapid memory depletion.")
                        return

                    if (
                        ram.available < reserve
                        and falling > 64 * MB
                        and not warned
                    ):
                        print("\nNOTICE: Increasing memory pressure.", flush=True)
                        warned = True

                stop.wait(0.5)

        except Exception as error:
            cancel(f"Memory monitor failed: {error}")

    worker = threading.Thread(target=monitor, daemon=True)
    first_token = None
    result = None
    start = time.perf_counter()

    print("\n=== BENCHMARK STARTED ===")
    worker.start()

    try:
        connection.request(
            "POST", "/api/generate",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        with connection.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(f"Ollama HTTP error: {response.status}")

            for line in response:
                if cancelled.is_set():
                    break
                if not line.strip():
                    continue

                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])

                content = (
                    (chunk.get("thinking") or "") + (chunk.get("response") or "")
                )
                if content and first_token is None:
                    first_token = time.perf_counter() - start

                print(content, end="", flush=True)

                if chunk.get("done"):
                    result = chunk
                    break

    except Exception as error:
        if not cancelled.is_set():
            reasons.append(str(error))

    finally:
        stop.set()
        worker.join()
        connection.close()

    elapsed = time.perf_counter() - start
    lowest_ram = min([baseline] + [s["available"] for s in samples])

    commits = [
        s["commit_available"] for s in samples
        if s["commit_available"] is not None
    ]
    disk_samples = [
        s["disk_bytes"] for s in samples if s["disk_bytes"] is not None
    ]
    disk_activity = (
        max(0, disk_samples[-1] - disk_samples[0])
        if len(disk_samples) >= 2 else None
    )

    completed = result is not None and not cancelled.is_set()
    result = result or {}
    tokens = result.get("eval_count", 0)
    duration = result.get("eval_duration", 0) / 1e9

    report = {
        "model": name,
        "status": "COMPLETED" if completed else "INCOMPLETE",
        "reasons": reasons,
        "tokens": tokens,
        "tokens_per_second": round(tokens / duration, 3)
            if completed and duration > 0 else None,
        "first_token_seconds": round(first_token, 3)
            if first_token is not None else None,
        "total_seconds": round(elapsed, 3),
        "load_seconds": round(result.get("load_duration", 0) / 1e9, 3)
            if completed else None,
        "lowest_available_ram_gib": round(lowest_ram / GB, 3),
        "ram_increase_gib": round((baseline - lowest_ram) / GB, 3),
        "lowest_commit_headroom_gib": round(min(commits) / GB, 3)
            if commits else None,
        "disk_activity_mib": round(disk_activity / MB, 3)
            if disk_activity is not None else None,
        "peak_cpu_percent": max([0] + [s["cpu"] for s in samples]),
        "options": options
    }

    print("\n\n=== BENCHMARK RESULTS ===")
    for key, value in report.items():
        print(f"{key}: {value}")

    folder = Path(__file__).resolve().parent / "results"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = folder / f"adaptive_benchmark_{stamp}.json"
    path.write_text(json.dumps(report, indent=4), encoding="utf-8")
    print("Report saved:", path)

    if not completed:
        print("Check running models with: ollama ps")


def main():
    print("LocalAI Optimizer - Adaptive Safety Engine")
    try:
        models = api("/api/tags").get("models", [])
        for i, model in enumerate(models, 1):
            print(f"{i}. {model['name']}")
        print("0. Exit")
        choice = int(input("\nSelect model: "))
        if 1 <= choice <= len(models):
            benchmark(models[choice - 1])
    except Exception as error:
        print("Operation failed:", error)


if __name__ == "__main__":
    main()
