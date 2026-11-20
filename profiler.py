import json
import sys
import time
import psutil
import platform
import statistics
import subprocess

from pathlib import Path
from datetime import datetime
from compatibility import api

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
PROFILES = ROOT / "profiles"
RUNS = 3


def wait_until_unloaded():
    for _ in range(30):
        running = api("/api/ps").get("models", [])
        if not running:
            return True
        time.sleep(1)
    return False


def run_benchmark(model):
    RESULTS.mkdir(exist_ok=True)
    before = set(RESULTS.glob("adaptive_benchmark_*.json"))

    command = [
        sys.executable,
        "-c",
        (
            "import json, sys; "
            "from benchmark import benchmark; "
            "benchmark(json.loads(sys.argv[1]))"
        ),
        json.dumps(model)
    ]

    process = subprocess.run(
        command, input="y\n", text=True, cwd=ROOT
    )

    after = set(RESULTS.glob("adaptive_benchmark_*.json"))
    new_reports = after - before

    if process.returncode != 0 or len(new_reports) != 1:
        return None

    report = json.loads(
        new_reports.pop().read_text(encoding="utf-8")
    )

    if (
        report.get("status") != "COMPLETED"
        or report.get("model") != model["name"]
        or not report.get("tokens_per_second")
    ):
        return None

    return report


def create_profile(model, reports):
    speeds = [r["tokens_per_second"] for r in reports]
    memory = [r["ram_increase_gib"] for r in reports]
    latencies = [
        r["first_token_seconds"]
        for r in reports
        if r.get("first_token_seconds") is not None
    ]

    average_speed = statistics.mean(speeds)
    median_speed = statistics.median(speeds)
    variation = statistics.pstdev(speeds) / average_speed * 100
    same_options = all(
        r["options"] == reports[0]["options"]
        for r in reports
    )

    return {
        "model": model["name"],
        "model_digest": model.get("digest"),
        "completed_runs": len(reports),
        "requested_runs": RUNS,
        "hardware": {
            "os": platform.system(),
            "processor": platform.processor(),
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(),
            "total_ram_gib": round(
                psutil.virtual_memory().total / (1024 ** 3), 3
            )
        },
        "average_speed": round(average_speed, 3),
        "median_speed": round(median_speed, 3),
        "minimum_speed": min(speeds),
        "maximum_speed": max(speeds),
        "speed_variation_percent": round(variation, 3),
        "average_ram_increase_gib": round(statistics.mean(memory), 3),
        "maximum_ram_increase_gib": max(memory),
        "median_first_token_seconds": round(
            statistics.median(latencies), 3
        ) if latencies else None,
        "consistent_options": same_options,
        "options": reports[0]["options"],
        "status": (
            "PRELIMINARY_PROFILE"
            if len(reports) == RUNS and same_options
            else "INCOMPLETE_PROFILE"
        ),
        "safety_override_allowed": False,
        "measurements": reports
    }


def main():
    print("LocalAI Optimizer v0.4.0")
    print("=== ADAPTIVE PROFILER ===")

    models = api("/api/tags").get("models", [])
    if not models:
        print("No installed models found.")
        return

    for i, model in enumerate(models, 1):
        print(f"{i}. {model['name']}")
    print("0. Exit")

    choice = int(input("\nSelect model: "))
    if choice == 0:
        return
    if not 1 <= choice <= len(models):
        print("Invalid selection.")
        return

    model = models[choice - 1]
    print("\nSelected model:", model["name"])
    print("Benchmark repetitions:", RUNS)
    print("Adaptive memory protection: Enabled")

    consent = input("\nStart repeated benchmarks? (y/n): ")
    if consent.strip().lower() != "y":
        print("Cancelled.")
        return

    reports = []

    for run in range(1, RUNS + 1):
        print("\n" + "=" * 60)
        print(f"BENCHMARK ROUND {run}/{RUNS}")
        print("=" * 60)

        if not wait_until_unloaded():
            print("Model memory has not been released.")
            print("Remaining tests cancelled.")
            break

        current_models = api("/api/tags").get("models", [])
        current = next(
            (m for m in current_models if m["name"] == model["name"]),
            None
        )

        if current is None or current.get("digest") != model.get("digest"):
            print("Model changed. Remaining tests cancelled.")
            break

        report = run_benchmark(current)

        if report is None:
            print("Test blocked, failed or incomplete.")
            print("Remaining tests cancelled.")
            break

        reports.append(report)
        print("\nRound speed:", report["tokens_per_second"], "t/s")
        print("RAM increase:", report["ram_increase_gib"], "GiB")

    if not reports:
        print("No successful benchmarks.")
        return

    profile = create_profile(model, reports)
    print("\n=== FINAL MODEL PROFILE ===")

    for key, value in profile.items():
        if key != "measurements":
            print(f"{key}: {value}")

    PROFILES.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = PROFILES / f"profile_{stamp}.json"
    path.write_text(json.dumps(profile, indent=4), encoding="utf-8")
    print("\nProfile saved:", path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProfiler cancelled.")
        print("Check running models with: ollama ps")
    except Exception as error:
        print("Profiler failed:", error)
