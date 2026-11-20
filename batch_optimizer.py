import json
import time
import psutil
import socket
import hashlib
import threading
import http.client
import statistics

from pathlib import Path
from datetime import datetime
from compatibility import api, estimate_kv_cache
from benchmark import safety_check, memory_limits, commit_available
from optimizer import wait_until_unloaded

GB = 1024 ** 3
MB = 1024 ** 2
BATCHES = [64, 128, 256, 512]
THREADS = 8
RUNS = 3

PROMPT = "\n".join(
    f"Component {i}: A Python application uses "
    f"functions to process input, validate data, "
    f"and return structured results."
    for i in range(1, 41)
)
PROMPT += "\nSummarize the application in one paragraph."


def test_batch(model, batch):
    if not wait_until_unloaded():
        raise RuntimeError("Model memory not released.")
    if not safety_check(model):
        raise RuntimeError("Safety check blocked test.")

    options = {
        "num_ctx": 2048, "num_predict": 32,
        "num_batch": batch, "num_thread": THREADS,
        "num_gpu": 0, "temperature": 0, "seed": 42
    }
    payload = {
        "model": model["name"], "prompt": PROMPT,
        "stream": True, "keep_alive": 0, "options": options
    }
    metadata = api("/api/show", {"model": model["name"]})
    kv = estimate_kv_cache(metadata.get("model_info") or {}, options["num_ctx"])
    if kv is None:
        raise RuntimeError("Unknown memory requirements.")

    initial = psutil.virtual_memory()
    reserve, critical = memory_limits(initial.total)
    size = model["size"]
    # Experimental extra allowance for larger batches.
    extra = max(0, batch - 128) / 384 * 512 * MB
    required = size + kv + max(GB, size * 0.15) + extra
    if initial.available < required + reserve:
        raise RuntimeError("Insufficient batch memory budget.")

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

    prompt_tokens = result.get("prompt_eval_count", 0)
    prompt_duration = result.get("prompt_eval_duration", 0) / 1e9
    cached = result.get("prompt_eval_cached_count")
    if cached is not None and cached > 0:
        raise RuntimeError("Cached prompt detected.")

    output_tokens = result.get("eval_count", 0)
    output_duration = result.get("eval_duration", 0) / 1e9
    if prompt_tokens <= 0 or prompt_duration <= 0 or output_duration <= 0:
        raise RuntimeError("Invalid benchmark metrics.")

    lowest = min([initial.available] + [s[1] for s in samples])
    return {
        "batch": batch,
        "prompt_tokens": prompt_tokens,
        "prompt_speed": round(prompt_tokens / prompt_duration, 3),
        "generation_speed": round(output_tokens / output_duration, 3),
        "prompt_seconds": round(prompt_duration, 3),
        "first_token_seconds": first_token,
        "total_seconds": round(time.perf_counter() - start, 3),
        "ram_increase_gib": round((initial.available - lowest) / GB, 3),
        "output_hash": hashlib.sha256("".join(output).encode("utf-8")).hexdigest(),
        "cached_tokens": cached,
        "options": options
    }


def main():
    print("LocalAI Optimizer v0.4.0")
    print("=== AUTOMATIC BATCH OPTIMIZER ===")

    models = api("/api/tags").get("models", [])
    for i, model in enumerate(models, 1):
        print(f"{i}. {model['name']}")
    choice = int(input("\nSelect model (0 = exit): "))
    if not 1 <= choice <= len(models):
        return
    model = models[choice - 1]
    print("\nModel:", model["name"])
    print("Threads:", THREADS)
    print("Batch candidates:", BATCHES)
    print("Total tests:", len(BATCHES) * RUNS)
    if input("\nStart optimization? (y/n): ").strip().lower() != "y":
        return

    measurements = []
    failure = None
    try:
        for r in range(RUNS):
            offset = r % len(BATCHES)
            ordered = BATCHES[offset:] + BATCHES[:offset]
            for batch in ordered:
                print("\n" + "=" * 60)
                print(f"Round: {r + 1}/{RUNS}")
                print("Batch:", batch)
                current = next(
                    (
                        m for m in api("/api/tags").get("models", [])
                        if m["name"] == model["name"]
                    ),
                    None
                )
                if current is None or current.get("digest") != model.get("digest"):
                    raise RuntimeError("Model changed.")
                result = test_batch(current, batch)
                result["round"] = r + 1
                measurements.append(result)
                print("Prompt tokens:", result["prompt_tokens"])
                print("Prompt speed:", result["prompt_speed"], "t/s")
                print("Generation:", result["generation_speed"], "t/s")
                print("RAM:", result["ram_increase_gib"], "GiB")
    except (Exception, KeyboardInterrupt) as error:
        failure = str(error) or "Cancelled by user"
        print("Optimization stopped:", failure)

    summaries = []
    for batch in BATCHES:
        runs = [m for m in measurements if m["batch"] == batch]
        if len(runs) != RUNS:
            continue
        speeds = [m["prompt_speed"] for m in runs]
        summaries.append({
            "batch": batch,
            "median_prompt_speed": round(statistics.median(speeds), 3),
            "median_generation_speed": round(statistics.median(
                m["generation_speed"] for m in runs
            ), 3),
            "median_prompt_seconds": round(statistics.median(
                m["prompt_seconds"] for m in runs
            ), 3),
            "maximum_ram_gib": max(m["ram_increase_gib"] for m in runs),
            "variation_percent": round(
                statistics.pstdev(speeds) / statistics.mean(speeds) * 100, 3
            ),
            "output_hashes": [m["output_hash"] for m in runs]
        })
    summaries.sort(key=lambda m: m["median_prompt_speed"], reverse=True)

    print("\n=== BATCH OPTIMIZATION RESULTS ===")
    for m in summaries:
        print(
            f"Batch: {m['batch']} | "
            f"Prompt: {m['median_prompt_speed']} t/s | "
            f"Generation: {m['median_generation_speed']} t/s | "
            f"RAM: {m['maximum_ram_gib']} GiB | "
            f"Variation: {m['variation_percent']}%"
        )
    same_input_length = len({m["prompt_tokens"] for m in measurements}) == 1
    complete = len(summaries) == len(BATCHES) and failure is None and same_input_length
    if complete:
        best = summaries[0]
        baseline = next(m for m in summaries if m["batch"] == 128)
        improvement = (
            best["median_prompt_speed"] / baseline["median_prompt_speed"] - 1
        ) * 100
        same_output = len(set(best["output_hashes"] + baseline["output_hashes"])) == 1
        print("\nFastest observed batch:", best["batch"])
        print(f"Prompt speed improvement: {improvement:.2f}%")
        print("Identical generated output:", same_output)
        print("Recommendation is preliminary.")
    else:
        print("Incomplete comparison. No final recommendation.")

    folder = Path(__file__).resolve().parent / "results"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = folder / f"batch_optimization_{stamp}.json"
    path.write_text(json.dumps({
        "model": model["name"],
        "model_digest": model.get("digest"),
        "threads": THREADS,
        "complete": complete,
        "failure": failure,
        "same_input_length": same_input_length,
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
