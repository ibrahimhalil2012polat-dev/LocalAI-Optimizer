import json
import math
import psutil
import platform
import statistics

from pathlib import Path
from compatibility import api, estimate_kv_cache
from benchmark import memory_limits

GB = 1024 ** 3
ROOT = Path(__file__).resolve().parent
PROFILES = ROOT / "profiles"

OPTIONS = {
    "num_ctx": 2048,
    "num_predict": 128,
    "num_batch": 128,
    "num_gpu": 0,
    "temperature": 0,
    "seed": 42
}


def hardware_signature():
    return {
        "os": platform.system(),
        "processor": platform.processor(),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(),
        "total_ram_gib": round(psutil.virtual_memory().total / GB, 3)
    }


def load_measurements(model):
    measurements = []
    seen = set()

    if not PROFILES.exists():
        return measurements

    digest = model.get("digest")
    if not digest:
        return measurements

    for path in PROFILES.glob("profile_*.json"):
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))

            if profile.get("model_digest") != digest:
                continue
            if profile.get("model") != model["name"]:
                continue
            if profile.get("hardware") != hardware_signature():
                continue
            if profile.get("options") != OPTIONS:
                continue
            if not profile.get("consistent_options"):
                continue

            for report in profile.get("measurements", []):
                if report.get("status") != "COMPLETED":
                    continue
                if report.get("model") != model["name"]:
                    continue
                if report.get("options") != OPTIONS:
                    continue
                if report.get("tokens") != OPTIONS["num_predict"]:
                    continue

                value = float(report["ram_increase_gib"])

                if not math.isfinite(value) or value <= 0:
                    continue

                fingerprint = json.dumps(report, sort_keys=True)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                measurements.append(value)

        except (OSError, ValueError, TypeError, KeyError):
            continue

    return measurements


def analyze(model):
    name = model["name"]
    size = model.get("size", 0)
    print("\n" + "=" * 60)
    print("Model:", name)

    if (
        size <= 0 or name.endswith(":cloud")
        or model.get("remote_host") or model.get("remote_model")
    ):
        print("Unsupported or remote model.")
        return

    metadata = api("/api/show", {"model": name})
    info = metadata.get("model_info") or {}
    kv = estimate_kv_cache(info, OPTIONS["num_ctx"])

    if kv is None:
        print("Unsupported architecture.")
        return

    overhead = max(GB, size * 0.15)
    theoretical = (size + kv + overhead) / GB
    values = load_measurements(model)

    print("Context:", OPTIONS["num_ctx"])
    print(f"Theoretical RAM: {theoretical:.3f} GiB")
    print("Matching measurements:", len(values))

    if len(values) >= 3:
        average = statistics.mean(values)
        median = statistics.median(values)
        maximum = max(values)
        variation = statistics.pstdev(values) / average * 100
        uncertainty = max(0.5, maximum * 0.15)
        learned = maximum + uncertainty

        print("\n=== LEARNED MEMORY PROFILE ===")
        print(f"Average RAM: {average:.3f} GiB")
        print(f"Median RAM: {median:.3f} GiB")
        print(f"Maximum RAM: {maximum:.3f} GiB")
        print(f"Variation: {variation:.2f}%")
        print(f"Uncertainty allowance: {uncertainty:.3f} GiB")
        print(f"Learned estimate: {learned:.3f} GiB")

        if len(values) >= 10 and variation < 5:
            print("Profile maturity: DEVELOPING")
        else:
            print("Profile maturity: PRELIMINARY")

        # Historical estimates cannot relax benchmark safety.
        effective = max(theoretical, learned)
        print("Historical estimate is advisory only.")

    else:
        effective = theoretical
        print("Not enough matching historical data.")
        print("Using theoretical estimate.")

    ram = psutil.virtual_memory()
    reserve, _ = memory_limits(ram.total)
    available = ram.available / GB
    reserved = reserve / GB
    remaining = available - effective

    print("\n=== MEMORY EVALUATION ===")
    print(f"Available RAM: {available:.3f} GiB")
    print(f"Effective estimate: {effective:.3f} GiB")
    print(f"Predicted remaining RAM: {remaining:.3f} GiB")
    print(f"Dynamic reserve: {reserved:.3f} GiB")

    if remaining >= reserved:
        print("Status: CPU FIT CANDIDATE")
    else:
        print("Status: INSUFFICIENT ESTIMATED RAM")

    print("Final benchmark safety check is still required.")


def main():
    print("LocalAI Optimizer v0.3.0")
    print("=== LEARNING MEMORY ENGINE ===")

    models = api("/api/tags").get("models", [])
    running = api("/api/ps").get("models", [])

    if running:
        print("Unload running models before analysis.")
        return

    for model in models:
        try:
            analyze(model)
        except Exception as error:
            print("Analysis error:", error)

    print("\n=== ANALYSIS COMPLETE ===")
    print("No models were loaded or modified.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Operation failed:", error)
