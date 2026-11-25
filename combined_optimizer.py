import json
import math
import psutil
import platform

from pathlib import Path
from datetime import datetime
from compatibility import api
from optimizer import wait_until_unloaded

import context_optimizer

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
PROFILES = ROOT / "profiles"
GB = 1024 ** 3
BATCH_TOLERANCE = 0.01


def load_report(prefix, model, validator=None):
    if not model.get("digest"):
        return None
    paths = sorted(RESULTS.glob(f"{prefix}_*.json"), reverse=True)
    for path in paths:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("model") != model["name"]:
                continue
            if report.get("model_digest") != model["digest"]:
                continue
            if report.get("complete") is not True:
                continue
            if report.get("failure"):
                continue
            if not report.get("summaries"):
                continue
            if validator and not validator(report):
                continue
            print("Loaded:", path.name)
            if report.get("screened_out"):
                print("Adaptive report: some candidates were screened after two runs.")
            return report
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return None


def choose_threads(report):
    candidates = [
        r for r in report["summaries"]
        if r["median_speed"] > 0
        and math.isfinite(r["median_speed"])
        and (
            r["threads"] is None
            or 1 <= r["threads"] <= (psutil.cpu_count() or 1)
        )
    ]
    if not candidates:
        raise RuntimeError("No valid thread candidates.")
    return max(candidates, key=lambda r: r["median_speed"])


def choose_batch(report):
    candidates = [
        r for r in report["summaries"]
        if r["median_prompt_speed"] > 0
        and math.isfinite(r["median_prompt_speed"])
        and r["maximum_ram_gib"] >= 0
        and r["batch"] > 0
    ]
    if not candidates:
        raise RuntimeError("No valid batch candidates.")
    fastest = max(r["median_prompt_speed"] for r in candidates)
    balanced = [
        r for r in candidates
        if r["median_prompt_speed"] >= fastest * (1 - BATCH_TOLERANCE)
    ]
    return min(balanced, key=lambda r: r["maximum_ram_gib"])


def choose_context(report):
    candidates = sorted(
        r["context"] for r in report["summaries"]
        if r.get("completed_runs", 0) > 0
    )
    if not candidates:
        raise RuntimeError("No valid context candidates.")
    print("\nTested context values:", candidates)
    value = input(f"Desired context [{max(candidates)}]: ").strip()
    selected = int(value) if value else max(candidates)
    if selected not in candidates:
        raise RuntimeError("Context has not been tested.")
    return next(r for r in report["summaries"] if r["context"] == selected)


def main():
    print("LocalAI Optimizer v0.4.0")
    print("=== COMBINED OPTIMIZER ===")

    models = api("/api/tags").get("models", [])
    for i, model in enumerate(models, 1):
        print(f"{i}. {model['name']}")
    choice = int(input("\nSelect model (0 = exit): "))
    if not 1 <= choice <= len(models):
        return
    model = models[choice - 1]
    print("\n=== LOADING HISTORICAL RESULTS ===")
    thread_report = load_report("thread_optimization", model)
    if thread_report is None:
        print("Missing thread optimization report.")
        return
    thread_result = choose_threads(thread_report)
    threads = thread_result["threads"]
    if threads is None:
        print("Default threads selected.")
        print("Explicit thread calibration is required.")
        return

    batch_report = load_report(
        "batch_optimization", model,
        lambda r: r.get("threads") == threads
    )
    if batch_report is None:
        print("Missing compatible batch report.")
        return
    batch_result = choose_batch(batch_report)
    batch = batch_result["batch"]

    context_report = load_report(
        "context_optimization", model,
        lambda r: r.get("threads") == threads and r.get("batch") == batch
    )
    if context_report is None:
        print("Missing compatible context report.")
        return
    context_result = choose_context(context_report)
    context = context_result["context"]

    print("\n=== PROPOSED CONFIGURATION ===")
    print("Model:", model["name"])
    print("Threads:", threads)
    print("Batch:", batch)
    print("Context:", context)
    print("Historical generation speed:", thread_result["median_speed"], "t/s")
    print("Historical context RAM increase:", context_result["maximum_ram_gib"], "GiB")
    print("Historical reports are preliminary.")
    print("Full hardware compatibility is not verified.")

    consent = input("\nRun final validation? (y/n): ")
    if consent.strip().lower() != "y":
        return
    if not wait_until_unloaded():
        print("Other models are still loaded.")
        return

    current = next(
        (m for m in api("/api/tags").get("models", [])
         if m["name"] == model["name"]),
        None
    )
    if current is None or current.get("digest") != model["digest"]:
        print("Model changed.")
        return

    allowed, reason = context_optimizer.preflight(current, context, batch)
    if not allowed:
        print("VALIDATION BLOCKED:", reason)
        return

    # These changes only affect this Python process.
    context_optimizer.THREADS = threads
    context_optimizer.BATCH = batch
    print("\n=== FINAL VALIDATION ===")
    validation = context_optimizer.test_context(current, context)
    expected_hashes = set(context_result.get("output_hashes", []))
    same_output = bool(expected_hashes) and expected_hashes == {validation["output_hash"]}

    profile = {
        "schema_version": 1,
        "model": model["name"],
        "model_digest": model["digest"],
        "hardware": {
            "os": platform.system(),
            "processor": platform.processor(),
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(),
            "total_ram_gib": round(psutil.virtual_memory().total / GB, 3)
        },
        "recommended_options": {
            "num_thread": threads, "num_batch": batch,
            "num_ctx": context, "num_gpu": 0
        },
        "historical_thread_result": thread_result,
        "historical_batch_result": batch_result,
        "historical_context_result": context_result,
        "validation": validation,
        "identical_validation_output": same_output,
        "status": "PRELIMINARY_VALIDATED" if same_output else "REVIEW_REQUIRED",
        "quality_fully_validated": False,
        "safety_override_allowed": False
    }

    print("\n=== FINAL OPTIMIZATION PROFILE ===")
    print("Model:", model["name"])
    print("Threads:", threads)
    print("Batch:", batch)
    print("Context:", context)
    print("Prompt speed:", validation["prompt_speed"], "t/s")
    print("Generation speed:", validation["generation_speed"], "t/s")
    print("RAM increase:", validation["ram_increase_gib"], "GiB")
    print("Remaining RAM:", validation["lowest_available_ram_gib"], "GiB")
    print("Identical validation output:", same_output)
    print("Status:", profile["status"])

    PROFILES.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = PROFILES / f"optimized_{stamp}.json"
    path.write_text(json.dumps(profile, indent=4), encoding="utf-8")
    print("\nOptimization profile saved:", path)
    print("No permanent model settings were changed.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOptimization cancelled.")
        print("Check running models with: ollama ps")
    except Exception as error:
        print("Operation failed:", error)
