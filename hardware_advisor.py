"""Read-only, CPU-only hardware recommendations; never apply settings."""
import math

GB = 1024 ** 3
CONTEXTS = (1024, 2048, 4096, 8192)


def recommend(model, info, *, total, available, physical, logical, reserve,
              estimate_kv, commit=None, affinity=None, loaded=False):
    """Return an advisory estimate, not a validated optimization profile."""
    def blocked(reason):
        return {"status": "NO_RECOMMENDATION", "reason": reason}

    name = model.get("name", "")
    size = model.get("size", 0)
    if (not name or name.endswith(":cloud") or model.get("remote_host")
            or model.get("remote_model")):
        return blocked("Remote or unknown model.")
    if loaded:
        return blocked("Models already loaded; unload manually and retry.")
    if not isinstance(size, (int, float)) or not math.isfinite(size) or size <= 0:
        return blocked("Unknown model size.")
    if total <= 0 or not 0 <= available <= total or reserve < 0:
        return blocked("Invalid RAM inventory.")
    arch = info.get("general.architecture")
    try:
        maximum = int(info.get(f"{arch}.context_length", 0))
    except (ValueError, TypeError):
        return blocked("Unknown model context limit.")
    if maximum <= 0:
        return blocked("Unknown model context limit.")
    logical = max(1, logical or 1)
    limit = min(logical, affinity) if affinity and affinity > 0 else logical
    threads = min(limit, max(1, physical or limit))
    # More conservative than the runtime reserve; no safety override.
    reserve = max(reserve, 1.5 * GB, total * 0.12)
    budget = available - reserve
    if commit is not None:
        budget = min(budget, commit - reserve)
    fits = []
    for context in CONTEXTS:
        if context > maximum:
            continue
        kv = estimate_kv(info, context)
        if kv is None or not math.isfinite(kv) or kv <= 0:
            return blocked("Unsupported or unknown KV-cache estimate.")
        required = size + kv + max(GB, size * 0.15)
        if required <= budget:
            fits.append((context, required))
    if not fits:
        return blocked("Insufficient estimated memory or unsupported context size.")
    context, required = fits[-1]
    return {
        "status": "ESTIMATE_ONLY",
        "options": {"num_thread": threads, "num_batch": 128,
                    "num_ctx": context, "num_gpu": 0},
        "estimated_ram_gib": round(required / GB, 3),
        "estimated_remaining_ram_gib": round((available - required) / GB, 3),
        "reserve_gib": round(reserve / GB, 3),
        "candidate_contexts": [c for c, _ in fits],
        "requires_live_validation": True
    }


def analyze_models(models):
    """Print suggestions using current hardware and Ollama metadata only."""
    if not models:
        return
    import psutil
    from compatibility import api, estimate_kv_cache
    from benchmark import memory_limits, commit_available

    print("\n=== AUTOMATIC HARDWARE ADVISOR ===")
    print("CPU-only estimates; GPU/VRAM offloading is not assessed.")
    try:
        running = api("/api/ps").get("models", [])
    except Exception as exc:
        print("Unable to verify running models:", exc)
        return
    try:
        affinity = len(psutil.Process().cpu_affinity())
    except (AttributeError, NotImplementedError, psutil.Error, OSError):
        affinity = None
    for model in models:
        name = model.get("name", "")
        print("\nModel:", name or "Unknown")
        try:
            info = {}
            if (name and not name.endswith(":cloud") and not running
                    and not model.get("remote_host") and not model.get("remote_model")):
                info = api("/api/show", {"model": name}).get("model_info") or {}
            ram = psutil.virtual_memory()
            reserve, _ = memory_limits(ram.total)
            result = recommend(
                model, info, total=ram.total, available=ram.available,
                physical=psutil.cpu_count(logical=False),
                logical=psutil.cpu_count(logical=True), affinity=affinity,
                reserve=reserve, commit=commit_available(), loaded=bool(running),
                estimate_kv=estimate_kv_cache)
            print("Status:", result["status"])
            if result["status"] != "ESTIMATE_ONLY":
                print("Reason:", result["reason"])
                continue
            for key, value in result["options"].items():
                print(f"{key}: {value}")
            print("Estimated model RAM:", result["estimated_ram_gib"], "GiB")
            print("Estimated remaining RAM:", result["estimated_remaining_ram_gib"], "GiB")
            print("Reserve:", result["reserve_gib"], "GiB")
            print("Candidate contexts:", result["candidate_contexts"])
            print("Requires controlled benchmark before use.")
        except Exception as exc:
            print("Analysis unavailable:", exc)
    print("\nNo model was loaded, modified or automatically applied.")
