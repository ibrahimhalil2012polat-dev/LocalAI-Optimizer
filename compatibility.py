import json
import urllib.request
import psutil

GB = 1024 ** 3
URL = "http://127.0.0.1:11434"
CONTEXTS = [1024, 2048, 4096, 8192]


def api(endpoint, payload=None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        URL + endpoint,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def gib(value):
    return round(value / GB, 3)


def estimate_kv_cache(info, context):
    arch = info.get("general.architecture")
    if arch not in ("qwen2", "llama"):
        return None

    try:
        layers = int(info[f"{arch}.block_count"])
        heads = int(info[f"{arch}.attention.head_count"])
        kv_heads = int(info[f"{arch}.attention.head_count_kv"])
        embedding = int(info[f"{arch}.embedding_length"])

        key_dim = int(info.get(
            f"{arch}.attention.key_length", embedding // heads
        ))
        value_dim = int(info.get(
            f"{arch}.attention.value_length", embedding // heads
        ))

        if min(layers, heads, kv_heads, key_dim, value_dim) <= 0:
            return None

        return layers * kv_heads * (key_dim + value_dim) * context * 2

    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def assess_model(model, running):
    name = model["name"]
    size = model.get("size", 0)
    ram = psutil.virtual_memory()

    # Preliminary inventory policy; benchmark.py uses adaptive limits.
    reserve = max(1.5 * GB, ram.total * 0.12)
    budget = max(0, ram.available - reserve)

    print("\n" + "=" * 60)
    print("Model:", name)
    print("Disk size:", gib(size), "GiB")
    print("RAM budget:", gib(budget), "GiB")

    if model.get("remote_host") or model.get("remote_model"):
        print("Status: REMOTE MODEL")
        return
    if size <= 0:
        print("Status: UNKNOWN MODEL SIZE")
        return

    try:
        metadata = api("/api/show", {"model": name})
        info = metadata.get("model_info") or {}
        arch = info.get("general.architecture", "Unknown")
        print("Architecture:", arch)
    except Exception as error:
        print("Metadata error:", error)
        return

    if any(name in (m.get("name"), m.get("model")) for m in running):
        print("Status: ALREADY LOADED")
        print("Cold-load estimate skipped.")
        return

    max_context = info.get(f"{arch}.context_length", 0)
    runtime_overhead = max(GB, size * 0.15)

    print("Estimated runtime overhead:", gib(runtime_overhead), "GiB")
    print("KV cache assumption: F16, single sequence")

    for context in CONTEXTS:
        if isinstance(max_context, int):
            if max_context > 0 and context > max_context:
                continue

        kv = estimate_kv_cache(info, context)
        print(f"\nContext: {context}")

        if kv is None:
            print("Status: UNKNOWN ARCHITECTURE")
            print("Automatic benchmark not recommended.")
            continue

        estimated = size + kv + runtime_overhead
        print("KV cache:", gib(kv), "GiB")
        print("Estimated memory:", gib(estimated), "GiB")
        print("Budget margin:", gib(budget - estimated), "GiB")

        if estimated <= budget:
            print("Status: CPU FIT CANDIDATE")
        elif estimated <= ram.total - reserve:
            print("Status: INSUFFICIENT AVAILABLE RAM")
        else:
            print("Status: CPU MEMORY LIMIT")


def main():
    print("LocalAI Optimizer v0.3.0")
    print("=== ADVANCED COMPATIBILITY ENGINE ===")
    ram = psutil.virtual_memory()
    print("Total RAM:", gib(ram.total), "GiB")
    print("Available RAM:", gib(ram.available), "GiB")

    try:
        models = api("/api/tags").get("models", [])
        running = api("/api/ps").get("models", [])
        for model in models:
            assess_model(model, running)
    except Exception as error:
        print("Analysis failed:", error)

    print("\n=== ANALYSIS COMPLETE ===")
    print("CPU-only estimates, not guaranteed.")
    print("GPU offloading is not included.")
    print("No models were loaded or modified.")


if __name__ == "__main__":
    main()
