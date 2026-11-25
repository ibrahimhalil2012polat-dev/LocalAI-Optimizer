import json
import time
import psutil
import socket
import hashlib
import threading
import http.client
import statistics
from adaptive_search import finalists

from pathlib import Path
from datetime import datetime
from compatibility import api
from benchmark import safety_check, memory_limits, commit_available

GB = 1024 ** 3
MB = 1024 ** 2
RUNS = 3
PROMPT = "Explain Python functions with a simple example."

BASE_OPTIONS = {
    "num_ctx": 2048,
    "num_predict": 128,
    "num_batch": 128,
    "num_gpu": 0,
    "temperature": 0,
    "seed": 42
}


def get_thread_candidates():
    logical = psutil.cpu_count() or 1
    physical = psutil.cpu_count(logical=False) or logical
    candidates = {max(1, physical // 2), physical, logical}
    return [None] + sorted(candidates)


def wait_until_unloaded():
    for _ in range(30):
        if not api("/api/ps").get("models", []):
            return True
        time.sleep(1)
    return False


def test_configuration(model, threads):
    if not wait_until_unloaded():
        raise RuntimeError("Model memory not released.")
    if not safety_check(model):
        raise RuntimeError("Safety check blocked the test.")

    options = BASE_OPTIONS.copy()
    if threads is not None:
        options["num_thread"] = threads

    payload = {
        "model": model["name"],
        "prompt": PROMPT,
        "stream": True,
        "keep_alive": 0,
        "options": options
    }
    initial = psutil.virtual_memory()
    reserve, critical = memory_limits(initial.total)

    samples = []
    stop = threading.Event()
    cancelled = threading.Event()

    connection = http.client.HTTPConnection(
        "127.0.0.1", 11434, timeout=180
    )
    connection.connect()
    active_socket = connection.sock

    def cancel(reason):
        cancelled.set()
        print("\nSAFETY STOP:", reason)
        try:
            active_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def monitor():
        critical_count = 0
        try:
            while not stop.is_set():
                available = psutil.virtual_memory().available
                commit = commit_available()
                now = time.monotonic()
                samples.append((now, available))

                if available < critical or (
                    commit is not None and commit < critical
                ):
                    critical_count += 1
                else:
                    critical_count = 0

                if critical_count >= 3:
                    cancel("Critical memory pressure.")
                    return

                if len(samples) >= 6:
                    old_time, old_ram = samples[-6]
                    rate = (old_ram - available) / max(now - old_time, 0.01)
                    if (
                        rate > 128 * MB
                        and available < reserve
                        and available - rate * 2 < critical
                    ):
                        cancel("Rapid memory depletion.")
                        return

                stop.wait(0.5)
        except Exception as error:
            cancel(str(error))

    worker = threading.Thread(target=monitor, daemon=True)
    result = None
    output = []
    first_token = None
    start = time.perf_counter()
    worker.start()

    try:
        connection.request(
            "POST", "/api/generate",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with connection.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP error {response.status}")
            for line in response:
                if cancelled.is_set():
                    break
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])
                content = (
                    (chunk.get("thinking") or "")
                    + (chunk.get("response") or "")
                )
                if content:
                    output.append(content)
                    if first_token is None:
                        first_token = time.perf_counter() - start
                if chunk.get("done"):
                    result = chunk
                    break
    finally:
        stop.set()
        worker.join()
        connection.close()

    if cancelled.is_set() or result is None:
        raise RuntimeError("Incomplete or cancelled test.")

    tokens = result.get("eval_count", 0)
    duration = result.get("eval_duration", 0) / 1e9
    if tokens <= 0 or duration <= 0:
        raise RuntimeError("Invalid performance metrics.")

    lowest = min([initial.available] + [s[1] for s in samples])
    output_hash = hashlib.sha256("".join(output).encode("utf-8")).hexdigest()

    return {
        "threads": threads,
        "tokens": tokens,
        "tokens_per_second": round(tokens / duration, 3),
        "ram_increase_gib": round((initial.available - lowest) / GB, 3),
        "first_token_seconds": first_token,
        "output_hash": output_hash,
        "options": options
    }


def label(threads):
    return "Default" if threads is None else str(threads)


def main():
    print("LocalAI Optimizer v0.4.0")
    print("=== AUTOMATIC THREAD OPTIMIZER ===")

    models = api("/api/tags").get("models", [])
    for i, model in enumerate(models, 1):
        print(f"{i}. {model['name']}")
    choice = int(input("\nSelect model (0 = exit): "))
    if not 1 <= choice <= len(models):
        return
    model = models[choice - 1]
    candidates = get_thread_candidates()

    print("\nModel:", model["name"])
    print("Thread candidates:", candidates)
    print("Repetitions:", RUNS)
    mode = input("Mode: [1] Adaptive (default), [2] Full: ").strip()
    if mode not in ("", "1", "2"):
        return
    adaptive = mode != "2"
    print("Maximum tests:", len(candidates) * RUNS)
    if input("\nStart optimization? (y/n): ").lower().strip() != "y":
        return

    measurements = []
    failure = None
    active = list(candidates)
    screened_out = []
    try:
        for round_number in range(RUNS):
            if adaptive and round_number == 2:
                active = finalists(candidates, measurements, "threads", "tokens_per_second", None)
                screened_out = [c for c in candidates if c not in active]
                print("Third-round tests skipped:", screened_out)
            offset = round_number % len(active)
            ordered = active[offset:] + active[:offset]
            for threads in ordered:
                print("\n" + "=" * 60)
                print(f"Round: {round_number + 1}/{RUNS}")
                print("Threads:", label(threads))
                current = next(
                    (
                        m for m in api("/api/tags").get("models", [])
                        if m["name"] == model["name"]
                    ),
                    None
                )
                if current is None or current.get("digest") != model.get("digest"):
                    raise RuntimeError("Model changed.")
                result = test_configuration(current, threads)
                result["round"] = round_number + 1
                measurements.append(result)
                print("Speed:", result["tokens_per_second"], "t/s")
                print("RAM increase:", result["ram_increase_gib"], "GiB")
    except (Exception, KeyboardInterrupt) as error:
        failure = str(error) or "Cancelled by user"
        print("\nOptimization stopped:", failure)

    summaries = []
    for threads in candidates:
        runs = [r for r in measurements if r["threads"] == threads]
        if len(runs) != RUNS:
            continue
        speeds = [r["tokens_per_second"] for r in runs]
        summaries.append({
            "threads": threads,
            "median_speed": round(statistics.median(speeds), 3),
            "variation_percent": round(
                statistics.pstdev(speeds) / statistics.mean(speeds) * 100, 3
            ),
            "maximum_ram_gib": max(r["ram_increase_gib"] for r in runs),
            "token_counts": [r["tokens"] for r in runs],
            "output_hashes": [r["output_hash"] for r in runs]
        })
    summaries.sort(key=lambda r: r["median_speed"], reverse=True)

    print("\n=== OPTIMIZATION RESULTS ===")
    for summary in summaries:
        print(
            f"Threads: {label(summary['threads'])} | "
            f"Speed: {summary['median_speed']} t/s | "
            f"RAM: {summary['maximum_ram_gib']} GiB | "
            f"Variation: {summary['variation_percent']}%"
        )

    complete = len(summaries) == len(active) and failure is None
    print("Tests performed:", len(measurements))
    if screened_out:
        print("Adaptive screening is preliminary; full comparison is available.")
    if complete:
        best = summaries[0]
        baseline = next(r for r in summaries if r["threads"] is None)
        improvement = (best["median_speed"] / baseline["median_speed"] - 1) * 100
        print("\nFastest observed threads:", label(best["threads"]))
        print(f"Observed improvement: {improvement:.2f}%")
        same_output = len(set(best["output_hashes"] + baseline["output_hashes"])) == 1
        print("Identical generated output:", same_output)
        print("Recommendation is preliminary.")
    else:
        print("Incomplete comparison. No final recommendation.")

    folder = Path(__file__).resolve().parent / "results"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = folder / f"thread_optimization_{stamp}.json"
    path.write_text(json.dumps({
        "model": model["name"],
        "model_digest": model.get("digest"),
        "complete": complete,
        "comparison_mode": "adaptive" if adaptive else "full",
        "screened_out": screened_out,
        "tests_performed": len(measurements),
        "maximum_tests": len(candidates) * RUNS,
        "failure": failure,
        "summaries": summaries,
        "measurements": measurements
    }, indent=4), encoding="utf-8")
    print("\nResults saved:", path)
    print("No permanent model settings were changed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Operation failed:", error)
