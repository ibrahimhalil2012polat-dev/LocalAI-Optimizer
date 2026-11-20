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
from benchmark import memory_limits, commit_available
from optimizer import wait_until_unloaded

GB = 1024 ** 3
MB = 1024 ** 2
CONTEXTS = [1024, 2048, 4096, 8192]
THREADS = 8
BATCH = 256

PROMPT = "\n".join(
    f"Module {i}: This Python module validates "
    f"input and returns structured output."
    for i in range(1, 16)
)
PROMPT += "\nExplain how these modules work together."


def preflight(model, context, batch):
    name = model["name"]
    size = model.get("size", 0)
    if (
        size <= 0 or name.endswith(":cloud")
        or model.get("remote_host") or model.get("remote_model")
    ):
        return False, "Unsupported or remote model."
    if api("/api/ps").get("models", []):
        return False, "Other models are loaded."

    metadata = api("/api/show", {"model": name})
    info = metadata.get("model_info") or {}
    arch = info.get("general.architecture")
    maximum = info.get(f"{arch}.context_length", 0)
    if maximum and context > int(maximum):
        return False, "Context exceeds model limit."

    kv = estimate_kv_cache(info, context)
    if kv is None:
        return False, "Unsupported architecture."

    ram = psutil.virtual_memory()
    reserve, _ = memory_limits(ram.total)
    overhead = max(GB, size * 0.15)
    batch_extra = max(0, batch - 128) / 384 * 512 * MB
    required = size + kv + overhead + batch_extra
    remaining = ram.available - required
    commit = commit_available()

    print("\n=== CONTEXT SAFETY CHECK ===")
    print("Context:", context)
    print(f"KV cache: {kv / GB:.3f} GiB")
    print(f"Estimated RAM: {required / GB:.3f} GiB")
    print(f"Available RAM: {ram.available / GB:.3f} GiB")
    print(f"Predicted remaining: {remaining / GB:.3f} GiB")
    print(f"Reserve: {reserve / GB:.3f} GiB")

    if commit is not None and commit < reserve:
        return False, "Low commit headroom."
    if remaining < reserve:
        return False, "Insufficient estimated RAM."
    return True, "Eligible for controlled test."


def test_context(model, context):
    options = {
        "num_ctx": context, "num_batch": BATCH,
        "num_thread": THREADS, "num_predict": 32,
        "num_gpu": 0, "temperature": 0, "seed": 42
    }
    payload = {
        "model": model["name"], "prompt": PROMPT,
        "stream": True, "keep_alive": 0, "options": options
    }
    initial = psutil.virtual_memory()
    reserve, critical = memory_limits(initial.total)

    samples = []
    reasons = []
    stop = threading.Event()
    cancelled = threading.Event()

    connection = http.client.HTTPConnection(
        "127.0.0.1", 11434, timeout=180
    )
    connection.connect()
    active_socket = connection.sock

    def cancel(reason):
        reasons.append(reason)
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
        raise RuntimeError("; ".join(reasons) or "Incomplete test.")

    prompt_tokens = result.get("prompt_eval_count", 0)
    prompt_duration = result.get("prompt_eval_duration", 0) / 1e9
    tokens = result.get("eval_count", 0)
    duration = result.get("eval_duration", 0) / 1e9
    cached = result.get("prompt_eval_cached_count")
    if cached is not None and cached > 0:
        raise RuntimeError("Cached prompt detected.")
    if tokens <= 0 or duration <= 0 or prompt_tokens <= 0 or prompt_duration <= 0:
        raise RuntimeError("Invalid metrics.")
    if prompt_tokens + options["num_predict"] >= context:
        raise RuntimeError("Insufficient context space.")

    lowest = min([initial.available] + [s[1] for s in samples])
    return {
        "context": context,
        "prompt_tokens": prompt_tokens,
        "prompt_speed": round(prompt_tokens / prompt_duration, 3),
        "generation_speed": round(tokens / duration, 3),
        "ram_increase_gib": round((initial.available - lowest) / GB, 3),
        "lowest_available_ram_gib": round(lowest / GB, 3),
        "first_token_seconds": first_token,
        "output_hash": hashlib.sha256("".join(output).encode("utf-8")).hexdigest(),
        "cached_tokens": cached,
        "options": options
    }


def main():
    print("LocalAI Optimizer v0.4.0")
    print("=== CONTEXT OPTIMIZER ===")

    models = api("/api/tags").get("models", [])
    for i, model in enumerate(models, 1):
        print(f"{i}. {model['name']}")
    choice = int(input("\nSelect model (0 = exit): "))
    if not 1 <= choice <= len(models):
        return
    model = models[choice - 1]
    print("\n1. Quick test")
    print("2. Detailed test")
    mode = input("\nSelect mode: ").strip()
    if mode not in ("1", "2"):
        return
    runs = 1 if mode == "1" else 3

    print("\nThreads:", THREADS)
    print("Batch:", BATCH)
    print("Context candidates:", CONTEXTS)
    print("Maximum tests:", len(CONTEXTS) * runs)
    if input("\nStart optimization? (y/n): ").lower().strip() != "y":
        return

    measurements = []
    skipped = {}
    failure = None
    try:
        for round_number in range(runs):
            offset = round_number % len(CONTEXTS)
            ordered = CONTEXTS[offset:] + CONTEXTS[:offset]
            for context in ordered:
                print("\n" + "=" * 60)
                print(f"Round: {round_number + 1}/{runs}")
                print("Context:", context)
                if not wait_until_unloaded():
                    raise RuntimeError("Model memory not released.")
                current = next(
                    (
                        m for m in api("/api/tags").get("models", [])
                        if m["name"] == model["name"]
                    ),
                    None
                )
                if current is None or current.get("digest") != model.get("digest"):
                    raise RuntimeError("Model changed.")
                allowed, reason = preflight(current, context, BATCH)
                if not allowed:
                    skipped[context] = reason
                    print("SKIPPED:", reason)
                    continue
                result = test_context(current, context)
                result["round"] = round_number + 1
                measurements.append(result)
                print("Prompt:", result["prompt_speed"], "t/s")
                print("Generation:", result["generation_speed"], "t/s")
                print("RAM:", result["ram_increase_gib"], "GiB")

    except (Exception, KeyboardInterrupt) as error:
        failure = str(error) or "Cancelled by user"
        print("\nOptimization stopped:", failure)

    summaries = []
    for context in CONTEXTS:
        records = [r for r in measurements if r["context"] == context]
        if len(records) != runs:
            continue
        summaries.append({
            "context": context,
            "completed_runs": len(records),
            "median_generation_speed": round(statistics.median(
                r["generation_speed"] for r in records
            ), 3),
            "median_prompt_speed": round(statistics.median(
                r["prompt_speed"] for r in records
            ), 3),
            "maximum_ram_gib": max(r["ram_increase_gib"] for r in records),
            "minimum_available_ram_gib": min(
                r["lowest_available_ram_gib"] for r in records
            ),
            "output_hashes": [r["output_hash"] for r in records]
        })

    print("\n=== CONTEXT OPTIMIZATION RESULTS ===")
    for item in summaries:
        print(
            f"Context: {item['context']} | "
            f"Prompt: {item['median_prompt_speed']} t/s | "
            f"Generation: {item['median_generation_speed']} t/s | "
            f"RAM: {item['maximum_ram_gib']} GiB"
        )

    same_input_length = len({r["prompt_tokens"] for r in measurements}) == 1
    complete = (
        len(summaries) == len(CONTEXTS) and failure is None and same_input_length
    )
    if summaries:
        largest = max(r["context"] for r in summaries)
        print("\nLargest tested context:", largest)
        print("Minimum memory candidate:", min(
            summaries, key=lambda r: r["maximum_ram_gib"]
        )["context"])
        print("Identical generated outputs:", len({
            r["output_hash"] for r in measurements
        }) == 1)
    print("Complete comparison:", complete)
    if skipped:
        print("Skipped configurations:", skipped)
    print("Context capacity and quality are not fully validated.")

    folder = Path(__file__).resolve().parent / "results"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = folder / f"context_optimization_{stamp}.json"
    path.write_text(json.dumps({
        "model": model["name"],
        "model_digest": model.get("digest"),
        "threads": THREADS,
        "batch": BATCH,
        "runs": runs,
        "complete": complete,
        "same_input_length": same_input_length,
        "failure": failure,
        "skipped": skipped,
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
