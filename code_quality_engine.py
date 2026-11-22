
import ast
import hashlib
import http.client
import json
import re
import socket
import threading
import time

from datetime import datetime
from pathlib import Path

import psutil

from compatibility import api
from benchmark import memory_limits, commit_available
from context_optimizer import preflight
from optimizer import wait_until_unloaded


GB = 1024 ** 3
MB = 1024 ** 2

CONTEXT = 2048
MAX_TOKENS = 384

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"

BASELINE = {
    "num_ctx": CONTEXT,
    "num_batch": 128,
    "num_predict": MAX_TOKENS,
    "num_gpu": 0,
    "temperature": 0,
    "seed": 42
}

OPTIMIZED = {
    "num_ctx": CONTEXT,
    "num_thread": 8,
    "num_batch": 256,
    "num_predict": MAX_TOKENS,
    "num_gpu": 0,
    "temperature": 0,
    "seed": 42
}

TESTS = [
    {
        "id": "python_clamp",
        "language": "Python",
        "task": (
            "Write a Python function named clamp_number "
            "with exactly three parameters: value, minimum, maximum. "
            "Assume minimum <= maximum. Return minimum if value "
            "is too small, maximum if value is too large, "
            "otherwise return value. Do not use imports."
        )
    },
    {
        "id": "unity_door",
        "language": "Unity C#",
        "task": (
            "Write a complete Unity MonoBehaviour script named DoorToggle. "
            "Use a public Transform field named door. "
            "Implement public void ToggleDoor() to toggle the door's "
            "localRotation between its initial rotation and a rotation "
            "90 degrees around the local Y axis. Use Quaternion. "
            "Do not use an Update loop."
        )
    },
    {
        "id": "python_even",
        "language": "Python",
        "task": (
            "Write a Python function named count_even "
            "with exactly one parameter named numbers. "
            "Return the number of even integers in the input list. "
            "Use the remainder operator. Do not use imports."
        )
    },
    {
        "id": "unity_flashlight",
        "language": "Unity C#",
        "task": (
            "Write a complete Unity MonoBehaviour script named "
            "FlashlightToggle. Use a public Light field named flashlight. "
            "In Update(), detect Input.GetKeyDown(KeyCode.F). "
            "When F is pressed, toggle flashlight.enabled. "
            "Use the built-in Unity input system."
        )
    }
]


def extract_code(response):
    response = response.strip()

    match = re.search(
        r"```(?:python|py|csharp|c#|cs)?\s*\n"
        r"(.*?)```",
        response,
        re.IGNORECASE | re.DOTALL
    )

    if match:
        return match.group(1).strip()

    return response


def validate_python(code, test_id):
    problems = []

    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return False, [
            f"Python syntax error: {error.msg}"
        ]

    name, parameters = {
        "python_clamp": (
            "clamp_number",
            ["value", "minimum", "maximum"]
        ),
        "python_even": (
            "count_even",
            ["numbers"]
        )
    }[test_id]

    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    ]

    if len(functions) != 1:
        problems.append(f"Missing function: {name}")
        return False, problems

    function = functions[0]

    actual_parameters = [
        arg.arg for arg in function.args.args
    ]

    if actual_parameters != parameters:
        problems.append("Incorrect function parameters")

    nodes = list(ast.walk(function))

    if not any(
        isinstance(node, ast.Return)
        for node in nodes
    ):
        problems.append("Missing return statement")

    if test_id == "python_clamp":
        has_logic = any(
            isinstance(
                node,
                (ast.If, ast.IfExp, ast.Compare)
            )
            for node in nodes
        )

        has_minmax = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("min", "max")
            for node in nodes
        )

        if not (has_logic or has_minmax):
            problems.append(
                "Expected bounds-checking logic"
            )

    if test_id == "python_even":
        has_modulo = any(
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            for node in nodes
        )

        if not has_modulo:
            problems.append(
                "Expected remainder operator"
            )

    return len(problems) == 0, problems


def validate_unity(code, test_id):
    problems = []

    checks = {
        "unity_door": {
            "DoorToggle MonoBehaviour class":
                r"\bclass\s+DoorToggle\s*:\s*MonoBehaviour\b",
            "Transform field":
                r"\bTransform\s+door\b",
            "ToggleDoor method":
                r"\bpublic\s+void\s+ToggleDoor\s*\(\s*\)",
            "Quaternion usage":
                r"\bQuaternion\b",
            "Rotation assignment":
                r"\bdoor\.(?:localRotation|rotation)\b"
        },
        "unity_flashlight": {
            "FlashlightToggle MonoBehaviour class":
                r"\bclass\s+FlashlightToggle\s*:\s*MonoBehaviour\b",
            "Light field":
                r"\bLight\s+flashlight\b",
            "Update method":
                r"\bvoid\s+Update\s*\(\s*\)",
            "Keyboard input":
                r"\bInput\.GetKeyDown\s*\(",
            "F key":
                r"\bKeyCode\.F\b",
            "Light enabled property":
                r"\bflashlight\.enabled\b"
        }
    }[test_id]

    for label, pattern in checks.items():
        if not re.search(pattern, code):
            problems.append("Missing: " + label)

    # Heuristic structural checks, NOT C# compilation.
    stripped = re.sub(
        r'"(?:\\.|[^"\\])*"',
        '""',
        code
    )
    stripped = re.sub(
        r"//[^\n]*|/\*.*?\*/",
        "",
        stripped,
        flags=re.DOTALL
    )

    for opening, closing in [
        ("{", "}"),
        ("(", ")"),
        ("[", "]")
    ]:
        balance = 0

        for character in stripped:
            if character == opening:
                balance += 1
            elif character == closing:
                balance -= 1

            if balance < 0:
                break

        if balance != 0:
            problems.append(
                f"Unbalanced {opening}{closing}"
            )

    if re.search(
        r"\bTODO\b|NotImplementedException|\.\.\.",
        code
    ):
        problems.append("Incomplete implementation marker")

    return len(problems) == 0, problems


def evaluate_code(code, test):
    if test["language"] == "Python":
        return validate_python(code, test["id"])

    return validate_unity(code, test["id"])


def build_prompt(test):
    return (
        "You are a code generation assistant.\n"
        "Return ONLY source code, without explanation.\n"
        "Do not include Markdown fences.\n"
        "Implement the following task:\n\n"
        + test["task"]
    )


def run_test(model, test, options):
    if not wait_until_unloaded():
        raise RuntimeError(
            "Previously loaded model was not released"
        )

    current = next(
        (
            m for m in api("/api/tags").get("models", [])
            if m["name"] == model["name"]
        ),
        None
    )

    if (
        current is None
        or current.get("digest") != model.get("digest")
    ):
        raise RuntimeError("Selected model changed")

    allowed, reason = preflight(
        current,
        CONTEXT,
        options["num_batch"]
    )

    if not allowed:
        raise RuntimeError(
            "Safety preflight blocked test: " + reason
        )

    ram = psutil.virtual_memory()
    reserve, critical = memory_limits(ram.total)

    payload = {
        "model": model["name"],
        "prompt": build_prompt(test),
        "stream": False,
        "keep_alive": 0,
        "options": options
    }

    stop = threading.Event()
    cancelled = threading.Event()
    reasons = []
    samples = []

    connection = http.client.HTTPConnection(
        "127.0.0.1",
        11434,
        timeout=180
    )

    connection.connect()
    active_socket = connection.sock

    def cancel(reason):
        if cancelled.is_set():
            return

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
                now = time.monotonic()
                available = psutil.virtual_memory().available
                commit = commit_available()

                samples.append((now, available))

                if available < critical or (
                    commit is not None
                    and commit < critical
                ):
                    critical_count += 1
                else:
                    critical_count = 0

                if critical_count >= 3:
                    cancel("Critical memory pressure")
                    return

                if len(samples) >= 6:
                    old_time, old_ram = samples[-6]

                    elapsed = max(
                        now - old_time,
                        0.01
                    )

                    rate = (
                        old_ram - available
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
            cancel(
                f"Memory monitor failed: {error}"
            )

    worker = threading.Thread(
        target=monitor,
        daemon=True
    )

    response_data = None
    start = time.perf_counter()

    worker.start()

    try:
        connection.request(
            "POST",
            "/api/generate",
            body=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json"
            }
        )

        with connection.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(
                    f"Ollama HTTP {response.status}"
                )

            response_data = json.loads(
                response.read().decode("utf-8")
            )

    finally:
        stop.set()
        worker.join()
        connection.close()

    if cancelled.is_set():
        raise RuntimeError(
            "; ".join(reasons)
        )

    if (
        not response_data
        or not response_data.get("done")
    ):
        raise RuntimeError(
            "Incomplete model response"
        )

    if response_data.get("error"):
        raise RuntimeError(
            response_data["error"]
        )

    raw_output = response_data.get(
        "response",
        ""
    )

    code = extract_code(raw_output)
    passed, problems = evaluate_code(code, test)

    tokens = response_data.get("eval_count", 0)
    duration = (
        response_data.get("eval_duration", 0)
        / 1e9
    )

    lowest = min(
        [ram.available]
        + [item[1] for item in samples]
    )

    possible_truncation = (
        tokens >= MAX_TOKENS
        or response_data.get("done_reason") == "length"
    )

    return {
        "test_id": test["id"],
        "language": test["language"],
        "static_pass": passed,
        "problems": problems,
        "generated_code": code,
        "output_sha256": hashlib.sha256(
            code.encode("utf-8")
        ).hexdigest(),
        "tokens": tokens,
        "possible_truncation": possible_truncation,
        "speed_tokens_per_second": (
            round(tokens / duration, 3)
            if duration > 0 else None
        ),
        "elapsed_seconds": round(
            time.perf_counter() - start,
            3
        ),
        "ram_increase_gib": round(
            (ram.available - lowest) / GB,
            3
        )
    }


def main():
    print("LocalAI Optimizer v0.5.0-dev")
    print("=== CODE QUALITY ENGINE ===")

    models = api("/api/tags").get("models", [])

    for index, model in enumerate(models, 1):
        print(f"{index}. {model['name']}")

    print("0. Exit")

    choice = int(input("\nSelect model: "))

    if choice == 0:
        return

    if not 1 <= choice <= len(models):
        print("Invalid model selection")
        return

    model = models[choice - 1]

    print("\n1. Quick - Python + Unity C#")
    print("2. Detailed - All four tasks")

    mode = input("\nSelect mode: ").strip()

    if mode == "1":
        selected_tests = TESTS[:2]
    elif mode == "2":
        selected_tests = TESTS
    else:
        return

    print("\nModel:", model["name"])
    print("Tasks:", len(selected_tests))
    print("Model calls:", len(selected_tests) * 2)
    print("Generated code execution: Disabled")
    print("Permanent changes: None")

    if input(
        "\nStart code quality test? (y/n): "
    ).strip().lower() != "y":
        return

    configurations = {
        "baseline": BASELINE,
        "optimized": OPTIMIZED
    }

    results = {
        "baseline": {},
        "optimized": {}
    }

    failure = None

    try:
        for index, test in enumerate(selected_tests):
            print("\n" + "=" * 60)
            print("TASK:", test["id"])

            order = (
                ["baseline", "optimized"]
                if index % 2 == 0
                else ["optimized", "baseline"]
            )

            for config_name in order:
                print("\nConfiguration:", config_name)

                result = run_test(
                    model,
                    test,
                    configurations[config_name]
                )

                results[config_name][test["id"]] = result

                print(
                    "Static check:",
                    "PASS" if result["static_pass"]
                    else "FAIL"
                )

                print(
                    "Problems:",
                    result["problems"]
                )

                print(
                    "Speed:",
                    result["speed_tokens_per_second"],
                    "t/s"
                )

                if result["possible_truncation"]:
                    print(
                        "WARNING: Output may be truncated"
                    )

    except (Exception, KeyboardInterrupt) as error:
        failure = str(error) or "Interrupted"
        print("\nTest stopped:", failure)

    baseline = results["baseline"]
    optimized = results["optimized"]

    common = set(baseline) & set(optimized)

    regressions = sorted([
        key for key in common
        if baseline[key]["static_pass"]
        and not optimized[key]["static_pass"]
    ])

    improvements = sorted([
        key for key in common
        if not baseline[key]["static_pass"]
        and optimized[key]["static_pass"]
    ])

    baseline_score = sum(
        item["static_pass"]
        for item in baseline.values()
    )

    optimized_score = sum(
        item["static_pass"]
        for item in optimized.values()
    )

    total = len(selected_tests)

    complete = (
        failure is None
        and len(baseline) == total
        and len(optimized) == total
    )

    print("\n=== CODE QUALITY RESULTS ===")
    print("Completed:", complete)

    print(
        "Baseline static checks:",
        f"{baseline_score}/{total}"
    )

    print(
        "Optimized static checks:",
        f"{optimized_score}/{total}"
    )

    print("Regressions:", regressions)
    print("Improvements:", improvements)

    status = (
        "INCOMPLETE"
        if not complete
        else (
            "STATIC_REGRESSION_DETECTED"
            if regressions
            else "NO_STATIC_REGRESSION_IN_TESTED_CASES"
        )
    )

    print("Status:", status)
    print(
        "Note: Static checks do not prove "
        "runtime correctness or Unity compilation."
    )

    report = {
        "schema_version": 1,
        "model": model["name"],
        "model_digest": model.get("digest"),
        "completed": complete,
        "failure": failure,
        "status": status,
        "baseline_passed": baseline_score,
        "optimized_passed": optimized_score,
        "total_tasks": total,
        "regressions": regressions,
        "improvements": improvements,
        "configurations": configurations,
        "results": results,
        "code_executed": False,
        "unity_compiled": False,
        "full_quality_verified": False
    }

    RESULTS.mkdir(exist_ok=True)

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    path = RESULTS / f"code_quality_{stamp}.json"

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
        print("Code quality engine failed:", error)
