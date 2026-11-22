
import json
import time
import socket
import threading
import http.client
import psutil

from pathlib import Path
from datetime import datetime

from compatibility import api
from benchmark import memory_limits, commit_available
from context_optimizer import preflight
from optimizer import wait_until_unloaded

GB = 1024 ** 3
MB = 1024 ** 2
CONTEXT = 2048

TESTS = [
    {
        "name": "Python string slicing",
        "language": "Python",
        "code": 'print("tower"[1:4])',
        "expected": "owe"
    },
    {
        "name": "C# integer division",
        "language": "C#",
        "code": "int x = 7 / 2; Console.Write(x);",
        "expected": "3"
    },
    {
        "name": "Python arithmetic",
        "language": "Python",
        "code": "print(sum([2, 3, 4]))",
        "expected": "9"
    },
    {
        "name": "Python list indexing",
        "language": "Python",
        "code": "print([10, 20, 30][-1])",
        "expected": "30"
    },
    {
        "name": "C# remainder",
        "language": "C#",
        "code": "Console.Write(11 % 4);",
        "expected": "3"
    },
    {
        "name": "C# loop",
        "language": "C#",
        "code": (
            "int s = 0; "
            "for (int i = 1; i <= 4; i++) s += i; "
            "Console.Write(s);"
        ),
        "expected": "10"
    }
]

CONFIGS = {
    "baseline": {
        "num_ctx": CONTEXT,
        "num_batch": 128,
        "num_predict": 32,
        "num_gpu": 0,
        "temperature": 0,
        "seed": 42
    },
    "optimized": {
        "num_ctx": CONTEXT,
        "num_thread": 8,
        "num_batch": 256,
        "num_predict": 32,
        "num_gpu": 0,
        "temperature": 0,
        "seed": 42
    }
}


def create_prompt(test):
    return (
        f"Language: {test['language']}\n"
        "Determine the exact text printed by the code.\n"
        "Reply ONLY with that text.\n"
        "Do not add explanations, quotes or code fences.\n\n"
        f"{test['code']}"
    )


def run_inference(model, test, options):
    if not wait_until_unloaded():
        raise RuntimeError("Model memory not released.")

    current = next(
        (
            item
            for item in api("/api/tags").get("models", [])
            if item["name"] == model["name"]
        ),
        None
    )

    if (
        current is None
        or current.get("digest") != model.get("digest")
    ):
        raise RuntimeError("Model changed during testing.")

    allowed, reason = preflight(
        current, CONTEXT, options["num_batch"]
    )

    if not allowed:
        raise RuntimeError("Safety preflight: " + reason)

    initial = psutil.virtual_memory()
    reserve, critical = memory_limits(initial.total)

    payload = {
        "model": model["name"],
        "prompt": create_prompt(test),
        "stream": False,
        "keep_alive": 0,
        "options": options
    }

    stop = threading.Event()
    cancelled = threading.Event()
    samples = []
    reasons = []

    connection = http.client.HTTPConnection(
        "127.0.0.1", 11434, timeout=180
    )

    connection.connect()
    active_socket = connection.sock

    def cancel(reason):
        if cancelled.is_set():
            return

        reasons.append(reason)
        cancelled.set()
        print("\nSAFETY STOP:", reason, flush=True)

        try:
            active_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def monitor():
        critical_count = 0

        try:
            while not stop.is_set():
                now = time.monotonic()
                available = psutil.virtual_memory().available
                commit = commit_available()

                samples.append((now, available))

                if (
                    available < critical
                    or (
                        commit is not None
                        and commit < critical
                    )
                ):
                    critical_count += 1
                else:
                    critical_count = 0

                if critical_count >= 3:
                    cancel("Critical memory pressure")
                    return

                if len(samples) >= 6:
                    old_time, old_available = samples[-6]

                    elapsed = max(now - old_time, 0.01)
                    rate = (
                        old_available - available
                    ) / elapsed

                    if (
                        rate > 128 * MB
                        and available < reserve
                        and available - rate * 2 < critical
                    ):
                        cancel("Rapid memory depletion")
                        return

                stop.wait(0.5)

        except Exception as error:
            cancel(f"Memory monitor failed: {error}")

    worker = threading.Thread(
        target=monitor,
        daemon=True
    )

    response_data = None
    started = time.perf_counter()

    worker.start()

    try:
        connection.request(
            "POST",
            "/api/generate",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        with connection.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(
                    f"Ollama HTTP error: {response.status}"
                )

            response_data = json.loads(
                response.read().decode("utf-8")
            )

    finally:
        stop.set()
        worker.join()
        connection.close()

    if cancelled.is_set():
        raise RuntimeError("; ".join(reasons))

    if not response_data or not response_data.get("done"):
        raise RuntimeError("Incomplete model response.")

    if response_data.get("error"):
        raise RuntimeError(response_data["error"])

    answer = response_data.get("response", "").strip()

    tokens = response_data.get("eval_count", 0)
    duration = response_data.get("eval_duration", 0) / 1e9

    lowest = min(
        [initial.available]
        + [sample[1] for sample in samples]
    )

    return {
        "test": test["name"],
        "language": test["language"],
        "expected": test["expected"],
        "actual": answer,
        "correct": answer == test["expected"],
        "tokens": tokens,
        "tokens_per_second": round(
            tokens / duration, 3
        ) if duration > 0 else None,
        "total_seconds": round(
            time.perf_counter() - started, 3
        ),
        "ram_increase_gib": round(
            (initial.available - lowest) / GB, 3
        )
    }


def main():
    print("LocalAI Optimizer v0.5.0-dev")
    print("=== QUALITY ENGINE ===")

    models = api("/api/tags").get("models", [])

    for index, model in enumerate(models, 1):
        print(f"{index}. {model['name']}")

    print("0. Exit")

    choice = int(input("\nSelect model: "))

    if choice == 0:
        return

    if not 1 <= choice <= len(models):
        print("Invalid selection.")
        return

    model = models[choice - 1]

    print("\n1. Quick test (3 questions, 6 inferences)")
    print("2. Detailed test (6 questions, 12 inferences)")

    mode = input("\nSelect mode: ").strip()

    if mode == "1":
        tests = TESTS[:3]
    elif mode == "2":
        tests = TESTS
    else:
        return

    print("\nModel:", model["name"])
    print("Test cases:", len(tests))
    print("Total inferences:", len(tests) * 2)
    print("Context:", CONTEXT)
    print("CPU only: Yes")
    print("Permanent changes: None")

    if input("\nStart quality test? (y/n): ").strip().lower() != "y":
        return

    results = {
        "baseline": [],
        "optimized": []
    }

    failure = None

    try:
        for index, test in enumerate(tests):
            print("\n" + "=" * 60)
            print(f"QUESTION {index + 1}/{len(tests)}")
            print(test["name"])

            order = (
                ["baseline", "optimized"]
                if index % 2 == 0
                else ["optimized", "baseline"]
            )

            for name in order:
                print("\nConfiguration:", name)

                result = run_inference(
                    model,
                    test,
                    CONFIGS[name]
                )

                results[name].append(result)

                print("Expected:", result["expected"])
                print("Actual:", repr(result["actual"]))
                print(
                    "Result:",
                    "PASS" if result["correct"] else "FAIL"
                )

                print(
                    "Speed:",
                    result["tokens_per_second"],
                    "tokens/s"
                )

    except (Exception, KeyboardInterrupt) as error:
        failure = str(error) or "Interrupted by user"
        print("\nTesting stopped:", failure)

    baseline = {
        result["test"]: result
        for result in results["baseline"]
    }

    optimized = {
        result["test"]: result
        for result in results["optimized"]
    }

    common = set(baseline) & set(optimized)

    regressions = [
        name for name in common
        if baseline[name]["correct"]
        and not optimized[name]["correct"]
    ]

    improvements = [
        name for name in common
        if not baseline[name]["correct"]
        and optimized[name]["correct"]
    ]

    baseline_score = sum(
        result["correct"]
        for result in results["baseline"]
    )

    optimized_score = sum(
        result["correct"]
        for result in results["optimized"]
    )

    complete = (
        failure is None
        and len(results["baseline"]) == len(tests)
        and len(results["optimized"]) == len(tests)
    )

    print("\n=== QUALITY COMPARISON RESULTS ===")
    print("Completed:", complete)

    print(
        "Baseline correctness:",
        f"{baseline_score}/{len(tests)}"
    )

    print(
        "Optimized correctness:",
        f"{optimized_score}/{len(tests)}"
    )

    print("Regressions:", regressions)
    print("Improvements:", improvements)

    if complete:
        if regressions:
            print("Status: QUALITY REGRESSION DETECTED")
        else:
            print("Status: NO REGRESSION IN TESTED CASES")
    else:
        print("Status: INCOMPLETE")

    print(
        "Note: These tests only assess exact code-output "
        "answers, not general coding quality."
    )

    report = {
        "schema_version": 1,
        "model": model["name"],
        "model_digest": model.get("digest"),
        "context": CONTEXT,
        "complete": complete,
        "failure": failure,
        "baseline_correct": baseline_score,
        "optimized_correct": optimized_score,
        "total_questions": len(tests),
        "regressions": regressions,
        "improvements": improvements,
        "status": (
            "INCOMPLETE"
            if not complete
            else (
                "QUALITY_REGRESSION_DETECTED"
                if regressions
                else "NO_REGRESSION_IN_TESTED_CASES"
            )
        ),
        "quality_fully_validated": False,
        "configurations": CONFIGS,
        "results": results
    }

    folder = Path(__file__).resolve().parent / "results"
    folder.mkdir(exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    path = folder / f"quality_{stamp}.json"

    path.write_text(
        json.dumps(report, indent=4),
        encoding="utf-8"
    )

    print("\nReport saved:", path)
    print("No permanent model settings were changed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Quality engine failed:", error)
