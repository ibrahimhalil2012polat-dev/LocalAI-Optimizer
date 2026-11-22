
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"

CONFIGS = ("baseline", "optimized")
TASKS = (
    "python_clamp",
    "python_even",
    "unity_door",
    "unity_flashlight",
)

STAGES = (
    "structure",
    "compilation",
    "behavior",
)


def latest(pattern):
    files = sorted(
        RESULTS.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def load(path):
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def find_matching(pattern, key, expected):
    files = sorted(
        RESULTS.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    for path in files:
        try:
            data = load(path)
            if data.get(key) == expected:
                return path, data
        except (OSError, ValueError, TypeError):
            continue

    return None, None


def boolean_status(value):
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "INCONCLUSIVE"


def check_status(
    task_id, config, stage,
    quality, compilation, behavior, unity
):
    if stage == "structure":
        task = (
            quality.get("results", {})
            .get(config, {})
            .get(task_id)
        )

        if not isinstance(task, dict):
            return "INCONCLUSIVE"

        if task.get("possible_truncation"):
            return "INCONCLUSIVE"

        return boolean_status(task.get("static_pass"))

    if stage == "compilation":
        task = (
            (compilation or {})
            .get("results", {})
            .get(config, {})
            .get(task_id, {})
        )

        return boolean_status(task.get("compiled"))

    if task_id.startswith("python_"):
        task = (
            (behavior or {})
            .get("python_behavior", {})
            .get(config, {})
            .get(task_id, {})
        )

        status = task.get("status")

        if status in ("PASS", "FAIL"):
            return status

        return "INCONCLUSIVE"

    task = (
        (unity or {})
        .get("checks", {})
        .get(config, {})
        .get(task_id, {})
    )

    status = task.get("status")

    if status == "SUPPORTED_PATTERN_PASS":
        return "PASS"

    if status == "REQUIREMENT_FAIL":
        return "FAIL"

    return "INCONCLUSIVE"


def main():
    print("LocalAI Optimizer v0.5.0-dev")
    print("=== QUALITY SUMMARY ===")

    source_path = latest("code_quality_*.json")

    if source_path is None:
        print("No code-quality report found.")
        return

    quality = load(source_path)

    compilation_path, compilation = find_matching(
        "compilation_*.json",
        "source_report",
        source_path.name,
    )

    behavior_path, behavior = find_matching(
        "behavior_*.json",
        "source_quality_report",
        source_path.name,
    )

    unity_path, unity = find_matching(
        "unity_behavior_*.json",
        "source_quality_report",
        source_path.name,
    )

    files = {
        "code_quality": source_path.name,
        "compilation": (
            compilation_path.name
            if compilation_path else None
        ),
        "python_behavior": (
            behavior_path.name
            if behavior_path else None
        ),
        "unity_behavior": (
            unity_path.name
            if unity_path else None
        ),
    }

    rows = []
    regressions = []
    improvements = []
    shared_failures = []
    unresolved = []
    optimized_failures = []

    for task_id in TASKS:
        for stage in STAGES:
            baseline = check_status(
                task_id, "baseline", stage,
                quality, compilation, behavior, unity,
            )

            optimized = check_status(
                task_id, "optimized", stage,
                quality, compilation, behavior, unity,
            )

            row = {
                "task": task_id,
                "stage": stage,
                "baseline": baseline,
                "optimized": optimized,
            }

            rows.append(row)

            label = f"{task_id}/{stage}"

            if baseline == "PASS" and optimized == "FAIL":
                regressions.append(label)

            if baseline == "FAIL" and optimized == "PASS":
                improvements.append(label)

            if baseline == "FAIL" and optimized == "FAIL":
                shared_failures.append(label)

            if optimized == "FAIL":
                optimized_failures.append(label)

            if "INCONCLUSIVE" in (baseline, optimized):
                unresolved.append(label)

    all_reports_present = all(
        value is not None
        for value in files.values()
    )

    all_tasks_present = all(
        task_id in quality.get(
            "results", {}
        ).get(config, {})
        for config in CONFIGS
        for task_id in TASKS
    )

    reports_consistent = all(
        report is None
        or (
            report.get("model") == quality.get("model")
            and (
                not report.get("model_digest")
                or report.get("model_digest")
                == quality.get("model_digest")
            )
        )
        for report in (compilation, behavior, unity)
    )

    complete = (
        quality.get("completed") is True
        and all_reports_present
        and all_tasks_present
        and reports_consistent
        and not unresolved
    )

    if not complete:
        status = "INCOMPLETE_OR_INCONCLUSIVE"

    elif regressions:
        status = "OPTIMIZATION_REGRESSION_DETECTED"

    elif optimized_failures:
        status = "QUALITY_REQUIREMENTS_FAILED"

    else:
        status = "LIMITED_TESTS_PASSED"

    print("Model:", quality.get("model"))
    print("All reports matched:", all_reports_present)
    print("Complete:", complete)

    print("\nTask / Stage")
    print("Baseline | Optimized")

    for row in rows:
        print(
            f"{row['task']} / {row['stage']}: "
            f"{row['baseline']} | {row['optimized']}"
        )

    print("\n=== FINAL QUALITY RESULTS ===")

    for config in CONFIGS:
        passed = sum(
            row[config] == "PASS"
            for row in rows
        )

        failed = sum(
            row[config] == "FAIL"
            for row in rows
        )

        print(
            f"{config}: {passed}/{len(rows)} checks passed, "
            f"{failed} failed"
        )

    print("Regressions:", regressions)
    print("Improvements:", improvements)
    print("Shared failures:", shared_failures)
    print("Unresolved:", unresolved)
    print("Status:", status)

    print("Automatic apply approved: False")
    print("Actual Unity runtime verified: False")

    summary = {
        "schema_version": 1,
        "model": quality.get("model"),
        "model_digest": quality.get("model_digest"),
        "source_reports": files,
        "complete": complete,
        "status": status,
        "rows": rows,
        "regressions": regressions,
        "improvements": improvements,
        "shared_failures": shared_failures,
        "unresolved": unresolved,
        "automatic_apply_approved": False,
        "actual_unity_runtime_verified": False,
        "generated_code_executed_by_summary": False,
        "notes": [
            "No detected regression is not proof of equal quality.",
            "Python behavior uses restricted AST interpretation.",
            "Unity behavior uses limited static pattern analysis.",
            "C# compilation used Unity API stubs, not Unity itself.",
            "This summary does not apply optimization settings.",
        ],
    }

    RESULTS.mkdir(exist_ok=True)

    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    json_path = RESULTS / f"quality_summary_{stamp}.json"
    md_path = RESULTS / f"quality_summary_{stamp}.md"

    json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines = [
        "# LocalAI Optimizer — Quality Summary",
        "",
        f"**Model:** {quality.get('model')}",
        f"**Status:** {status}",
        f"**Complete:** {complete}",
        "",
        "| Task | Stage | Baseline | Optimized |",
        "|---|---|---|---|",
    ]

    for row in rows:
        lines.append(
            f"| {row['task']} | {row['stage']} "
            f"| {row['baseline']} "
            f"| {row['optimized']} |"
        )

    lines.extend([
        "",
        f"**Regressions:** {regressions}",
        f"**Shared failures:** {shared_failures}",
        f"**Unresolved:** {unresolved}",
        "",
        "## Limitations",
        "",
        "- No generated code was executed by this summarizer.",
        "- Python behavior was evaluated using a restricted AST interpreter.",
        "- Unity behavior was checked through limited static patterns.",
        "- C# compilation used Unity API stubs.",
        "- Actual Unity runtime behavior has not been verified.",
        "- Automatic optimization application is not approved.",
        "",
    ])

    md_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    print("\nJSON:", json_path)
    print("Markdown:", md_path)


if __name__ == "__main__":
    main()
