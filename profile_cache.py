"""Local, advisory cache and performance estimates. No automatic application."""
import json
import math
from datetime import datetime, timezone, timedelta

MAX_AGE = timedelta(days=30)


def fingerprint(api):
    import os
    import platform
    import psutil
    version = api("/api/version").get("version")
    processor = platform.processor() or platform.uname().processor
    # Unknown identifiers are not eligible for cache reuse.
    if not version or not processor:
        return None
    try:
        affinity = sorted(psutil.Process().cpu_affinity())
    except (AttributeError, NotImplementedError, OSError, psutil.Error):
        affinity = None
    return {
        "ollama": version, "os": platform.platform(),
        "machine": platform.machine(), "processor": processor,
        "physical": psutil.cpu_count(logical=False),
        "logical": psutil.cpu_count(),
        "ram_bytes": psutil.virtual_memory().total,
        "affinity": affinity,
        "runtime_env": {k: os.environ.get(k) for k in (
            "OLLAMA_KV_CACHE_TYPE", "OLLAMA_FLASH_ATTENTION",
            "OLLAMA_NUM_PARALLEL", "OLLAMA_CONTEXT_LENGTH")}
    }


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def eligible(profile, model, signature, now):
    if not signature or not model.get("digest"):
        return False
    try:
        age = now - datetime.fromisoformat(profile["cache_created_utc"])
        opts = profile["recommended_options"]
        v = profile["validation"]
        return (
            timedelta(0) <= age <= MAX_AGE
            and profile["cache_fingerprint"] == signature
            and profile["model"] == model["name"]
            and profile["model_digest"] == model["digest"]
            and profile["status"] == "PRELIMINARY_VALIDATED"
            and profile["identical_validation_output"] is True
            and all(type(opts[k]) is int and lo <= opts[k] <= hi
                    for k, lo, hi in (
                        ("num_thread", 1, signature["logical"] or 1),
                        ("num_batch", 1, 512), ("num_ctx", 512, 8192),
                        ("num_gpu", 0, 0)))
            and all(v["options"].get(k) == opts[k] for k in (
                "num_thread", "num_batch", "num_ctx", "num_gpu"))
            and isinstance(v["output_hash"], str) and bool(v["output_hash"])
            and positive(v["generation_speed"]) and positive(v["prompt_speed"])
            and positive(v["ram_increase_gib"])
            and all(isinstance(profile[k], dict) for k in (
                "historical_thread_result", "historical_batch_result",
                "historical_context_result"))
        )
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def cached_profiles(directory, model, signature, now=None):
    now = now or datetime.now(timezone.utc)
    hits = []
    for path in directory.glob("optimized_*.json"):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
            if eligible(p, model, signature, now):
                hits.append(p)
        except (OSError, ValueError, TypeError):
            continue
    return sorted(hits, key=lambda p: datetime.fromisoformat(p["cache_created_utc"]),
                  reverse=True)


def estimate(profiles, options, tokens=128):
    """Observed ranges for identical settings only; no cold-load prediction."""
    if type(tokens) is not int or tokens <= 0:
        raise ValueError("Positive token count required")
    runs = [p["validation"] for p in profiles
            if p["recommended_options"] == options]
    if not runs:
        return None
    speeds = [r["generation_speed"] for r in runs]
    ram = [r["ram_increase_gib"] for r in runs]
    return {"samples": len(runs), "speed_range": [min(speeds), max(speeds)],
            "ram_range_gib": [min(ram), max(ram)],
            "generation_seconds_range": [tokens/max(speeds), tokens/min(speeds)]}


def main():
    from pathlib import Path
    from compatibility import api
    models = api("/api/tags").get("models", [])
    signature = fingerprint(api)
    directory = Path(__file__).resolve().parent / "profiles"
    print("\n=== HISTORICAL PERFORMANCE ESTIMATES ===")
    for model in models:
        print("\nModel:", model["name"])
        profiles = cached_profiles(directory, model, signature)
        if not profiles:
            print("No matching recent measurements. Run combined optimization first.")
            continue
        options = profiles[0]["recommended_options"]
        report = estimate(profiles, options)
        print("Settings:", options)
        print("Matching samples:", report["samples"])
        print("Observed generation speed range (t/s):", report["speed_range"])
        print("Observed RAM increase range (GiB):", report["ram_range_gib"])
        print("Estimated 128-token generation time (s):", report["generation_seconds_range"])
        print("Excludes loading and prompt processing; not a guarantee.")
    print("No model loaded or settings changed.")


if __name__ == "__main__":
    main()
