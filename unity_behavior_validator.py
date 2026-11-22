
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"

CONFIGS = ("baseline", "optimized")
TASKS = ("unity_door", "unity_flashlight")

NAME = r"[A-Za-z_]\w*"
NUMBER_0 = r"0(?:\.0+)?[fFdD]?"
NUMBER_90 = r"90(?:\.0+)?[fFdD]?"

EULER_90 = (
    rf"Quaternion\s*\.\s*Euler\s*\(\s*"
    rf"{NUMBER_0}\s*,\s*"
    rf"{NUMBER_90}\s*,\s*"
    rf"{NUMBER_0}\s*\)"
)

AXIS_90 = (
    rf"Quaternion\s*\.\s*AngleAxis\s*\(\s*"
    rf"{NUMBER_90}\s*,\s*"
    r"Vector3\s*\.\s*up\s*\)"
)

ROTATION_90 = rf"(?:{EULER_90}|{AXIS_90})"


def clean_source(source):
    pattern = (
        r"//[^\n]*"
        r"|/\*[\s\S]*?\*/"
        r'|@"(?:""|[^"])*"'
        r'|"(?:\\.|[^"\\])*"'
        r"|'(?:\\.|[^'\\])*'"
    )

    return re.sub(
        pattern,
        lambda match: " " * len(match.group()),
        source
    )


def extract_block(source, opening):
    depth = 0

    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1

        elif source[index] == "}":
            depth -= 1

            if depth == 0:
                return source[opening + 1:index]

    return None


def method_body(source, name):
    pattern = (
        rf"\bvoid\s+{re.escape(name)}"
        r"\s*\(\s*\)\s*\{"
    )

    match = re.search(pattern, source)

    if match is None:
        return None

    return extract_block(
        source,
        match.end() - 1
    )


def check(status, reason, scenarios=None):
    return {
        "status": status,
        "reason": reason,
        "symbolic_scenarios": scenarios or [],
        "generated_code_executed": False,
        "actual_unity_runtime_tested": False
    }


def flashlight_check(source):
    class_pattern = (
        r"\bclass\s+FlashlightToggle\s*:\s*"
        r"(?:UnityEngine\.)?MonoBehaviour\b"
    )

    if not re.search(class_pattern, source):
        return check(
            "INCONCLUSIVE",
            "FlashlightToggle class not recognized"
        )

    if not re.search(
        r"\bLight\s+flashlight\b",
        source
    ):
        return check(
            "INCONCLUSIVE",
            "Light flashlight field not recognized"
        )

    body = method_body(source, "Update")

    if body is None:
        return check(
            "INCONCLUSIVE",
            "Update method not recognized"
        )

    key_pattern = (
        r"\bif\s*\(\s*"
        r"Input\s*\.\s*GetKeyDown\s*\(\s*"
        r"KeyCode\s*\.\s*F\s*\)\s*\)"
    )

    pressed = re.search(key_pattern, body)

    if not pressed:
        return check(
            "INCONCLUSIVE",
            "F-key condition not recognized"
        )

    remaining = body[pressed.end():].lstrip()

    if remaining.startswith("{"):
        branch = extract_block(remaining, 0)
    else:
        branch = remaining.split(";", 1)[0] + ";"

    if branch is None:
        return check(
            "INCONCLUSIVE",
            "Keyboard branch is incomplete"
        )

    toggle_pattern = (
        r"\bflashlight\s*\.\s*enabled\s*"
        r"=\s*!\s*flashlight\s*\.\s*enabled\s*;"
    )

    writes_pattern = (
        r"\bflashlight\s*\.\s*enabled\s*=(?!=)"
    )

    toggles = re.findall(toggle_pattern, branch)
    writes = re.findall(writes_pattern, body)

    if len(toggles) != 1 or len(writes) != 1:
        return check(
            "INCONCLUSIVE",
            "Single direct flashlight toggle not verified"
        )

    scenarios = [
        {"f_pressed": False, "enabled": False},
        {"f_pressed": True, "enabled": True},
        {"f_pressed": False, "enabled": True},
        {"f_pressed": True, "enabled": False}
    ]

    return check(
        "SUPPORTED_PATTERN_PASS",
        "Direct F-key flashlight toggle recognized",
        scenarios
    )


def door_check(source):
    class_pattern = (
        r"\bclass\s+DoorToggle\s*:\s*"
        r"(?:UnityEngine\.)?MonoBehaviour\b"
    )

    if not re.search(class_pattern, source):
        return check(
            "INCONCLUSIVE",
            "DoorToggle class not recognized"
        )

    if not re.search(
        r"\bTransform\s+door\b",
        source
    ):
        return check(
            "INCONCLUSIVE",
            "Transform door field not recognized"
        )

    body = method_body(source, "ToggleDoor")

    if body is None:
        return check(
            "INCONCLUSIVE",
            "ToggleDoor method not recognized"
        )

    initializers = [
        method_body(source, name)
        for name in ("Start", "Awake")
    ]

    initializers = [
        item for item in initializers
        if item is not None
    ]

    capture = None
    init_body = None

    capture_pattern = (
        rf"\b({NAME})\s*=\s*"
        r"door\s*\.\s*localRotation\s*;"
    )

    for part in initializers:
        match = re.search(capture_pattern, part)

        if match:
            capture = match.group(1)
            init_body = part
            break

    if capture is None:
        return check(
            "INCONCLUSIVE",
            "Initial rotation capture not recognized"
        )

    assignment_pattern = (
        r"\bdoor\s*\.\s*localRotation\s*"
        r"=(?!=)\s*([^;]+);"
    )

    assignments = re.findall(
        assignment_pattern,
        body
    )

    if len(assignments) != 1:
        return check(
            "INCONCLUSIVE",
            "Expected one localRotation assignment"
        )

    rhs = assignments[0].strip()

    ternary = re.fullmatch(
        r"(.+?)\s*\?\s*(.+?)\s*:\s*(.+)",
        rhs,
        flags=re.S
    )

    if not ternary:
        return check(
            "INCONCLUSIVE",
            "Door rotation expression not recognized"
        )

    condition, opening, closing = [
        value.strip()
        for value in ternary.groups()
    ]

    if closing != capture:
        return check(
            "INCONCLUSIVE",
            "Closing branch does not restore initial rotation"
        )

    equals_a = (
        rf"door\s*\.\s*localRotation\s*"
        rf"==\s*{re.escape(capture)}"
    )

    equals_b = (
        rf"{re.escape(capture)}\s*==\s*"
        r"door\s*\.\s*localRotation"
    )

    condition_valid = (
        re.fullmatch(equals_a, condition)
        or re.fullmatch(equals_b, condition)
    )

    if not condition_valid:
        return check(
            "INCONCLUSIVE",
            "Door toggle condition not recognized"
        )

    # Detect the confirmed bug:
    # absolute 90 degrees instead of a relative rotation.
    if re.fullmatch(ROTATION_90, opening):
        return check(
            "REQUIREMENT_FAIL",
            "Opening assigns absolute 90 degrees "
            "instead of initial rotation + 90 degrees"
        )

    # Recognize a valid relative-rotation expression.
    relative_pattern = (
        rf"{re.escape(capture)}\s*\*\s*"
        rf"{ROTATION_90}"
    )

    if re.fullmatch(relative_pattern, opening):
        scenarios = [
            {
                "call": 1,
                "rotation": "initial * yaw90"
            },
            {
                "call": 2,
                "rotation": "initial"
            }
        ]

        return check(
            "SUPPORTED_PATTERN_PASS",
            "Relative opening rotation and "
            "initial closing rotation recognized",
            scenarios
        )

    # Also recognize a relative rotation saved
    # in a variable during initialization.
    if re.fullmatch(NAME, opening):
        saved_rotation_pattern = (
            rf"\b{re.escape(opening)}\s*=\s*"
            rf"{relative_pattern}\s*;"
        )

        if re.search(
            saved_rotation_pattern,
            init_body
        ):
            scenarios = [
                {
                    "call": 1,
                    "rotation": "initial * yaw90"
                },
                {
                    "call": 2,
                    "rotation": "initial"
                }
            ]

            return check(
                "SUPPORTED_PATTERN_PASS",
                "Saved relative open rotation recognized",
                scenarios
            )

    return check(
        "INCONCLUSIVE",
        "Opening expression not recognized"
    )


def find_latest(pattern):
    files = sorted(
        RESULTS.glob(pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True
    )

    return files[0] if files else None


def matching_compilation(source_name):
    files = sorted(
        RESULTS.glob("compilation_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True
    )

    for path in files:
        try:
            report = json.loads(
                path.read_text(encoding="utf-8")
            )

            if report.get("source_report") == source_name:
                return report, path.name

        except (OSError, ValueError, TypeError):
            continue

    return None, None


def main():
    print("LocalAI Optimizer v0.5.0-dev")
    print("=== UNITY BEHAVIOR VALIDATOR ===")

    source_path = find_latest(
        "code_quality_*.json"
    )

    if source_path is None:
        print("No code quality report found")
        return

    quality = json.loads(
        source_path.read_text(encoding="utf-8")
    )

    compilation, compilation_name = matching_compilation(
        source_path.name
    )

    print("Source:", source_path.name)
    print("Model:", quality.get("model"))
    print(
        "Compilation report:",
        compilation_name or "NOT FOUND"
    )

    results = {
        config: {}
        for config in CONFIGS
    }

    for config in CONFIGS:
        for task_id in TASKS:
            task = quality.get(
                "results", {}
            ).get(config, {}).get(task_id)

            compiled = (
                (compilation or {})
                .get("results", {})
                .get(config, {})
                .get(task_id, {})
                .get("compiled")
            )

            if not task or compiled is not True:
                outcome = check(
                    "INCONCLUSIVE",
                    "Missing code or successful compilation"
                )

            else:
                original = task.get("generated_code")
                expected_hash = task.get("output_sha256")

                if (
                    not isinstance(original, str)
                    or len(original) > 50000
                ):
                    outcome = check(
                        "INCONCLUSIVE",
                        "Invalid source or size limit exceeded"
                    )

                elif (
                    not expected_hash
                    or hashlib.sha256(
                        original.encode("utf-8")
                    ).hexdigest() != expected_hash
                ):
                    outcome = check(
                        "INCONCLUSIVE",
                        "Source hash mismatch"
                    )

                else:
                    cleaned = clean_source(original)

                    if task_id == "unity_door":
                        outcome = door_check(cleaned)
                    else:
                        outcome = flashlight_check(cleaned)

            outcome["compiled_with_stubs"] = compiled
            results[config][task_id] = outcome

            print(
                f"{config} / {task_id}:",
                outcome["status"]
            )
            print("Reason:", outcome["reason"])

    regressions = []
    improvements = []
    shared_failures = []
    unresolved = []

    for task_id in TASKS:
        baseline = results[
            "baseline"
        ][task_id]["status"]

        optimized = results[
            "optimized"
        ][task_id]["status"]

        if (
            baseline == "REQUIREMENT_FAIL"
            and optimized == "REQUIREMENT_FAIL"
        ):
            shared_failures.append(task_id)

        elif (
            baseline == "SUPPORTED_PATTERN_PASS"
            and optimized == "REQUIREMENT_FAIL"
        ):
            regressions.append(task_id)

        elif (
            baseline == "REQUIREMENT_FAIL"
            and optimized == "SUPPORTED_PATTERN_PASS"
        ):
            improvements.append(task_id)

        elif "INCONCLUSIVE" in (baseline, optimized):
            unresolved.append(task_id)

    has_failures = any(
        results[config][task_id]["status"]
        == "REQUIREMENT_FAIL"
        for config in CONFIGS
        for task_id in TASKS
    )

    if has_failures:
        status = "BEHAVIOR_REQUIREMENT_FAILURE_DETECTED"

    elif unresolved:
        status = "INCONCLUSIVE"

    else:
        status = "SUPPORTED_PATTERNS_VERIFIED"

    print("\n=== UNITY BEHAVIOR RESULTS ===")
    print("Model:", quality.get("model"))

    for config in CONFIGS:
        passed = sum(
            results[config][task_id]["status"]
            == "SUPPORTED_PATTERN_PASS"
            for task_id in TASKS
        )

        failed = sum(
            results[config][task_id]["status"]
            == "REQUIREMENT_FAIL"
            for task_id in TASKS
        )

        print(
            f"{config}: {passed}/2 supported, "
            f"{failed}/2 requirement failures"
        )

    print("Shared failures:", shared_failures)
    print("Regressions:", regressions)
    print("Improvements:", improvements)
    print("Unresolved:", unresolved)
    print("Status:", status)
    print("Actual Unity runtime tested: False")
    print("Generated code executed: False")

    report = {
        "schema_version": 2,
        "model": quality.get("model"),
        "model_digest": quality.get("model_digest"),
        "source_quality_report": source_path.name,
        "source_compilation_report": compilation_name,
        "status": status,
        "shared_failures": shared_failures,
        "regressions": regressions,
        "improvements": improvements,
        "unresolved": unresolved,
        "checks": results,
        "generated_code_executed": False,
        "actual_unity_runtime_tested": False,
        "full_quality_verified": False
    }

    RESULTS.mkdir(exist_ok=True)

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    destination = RESULTS / (
        f"unity_behavior_{stamp}.json"
    )

    destination.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8"
    )

    print("Report saved:", destination)


if __name__ == "__main__":
    main()
