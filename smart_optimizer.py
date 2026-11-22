"""Search CPU runtime settings and save evidence for manual Apply."""

import hashlib
import json
import os
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from behavior_validator import check_python
from code_quality_engine import TESTS, build_prompt, evaluate_code, extract_code
from compilation_validator import compile_csharp, compile_python, get_dotnet_framework
from smart_runtime import GB, Session, context_options, local_model, model_list, run_prompt
from unity_behavior_validator import clean_source, door_check, flashlight_check

ROOT = Path(__file__).resolve().parent
PROFILES = ROOT / "profiles"
RESULTS = ROOT / "results"
SCHEMA = 2
METHOD = "smart-cpu-v2"
REFERENCE_BATCH = 512
BENCH_ROUNDS = 3
STAGES = ("structure", "compilation", "behavior")
MIN_GAIN = 0.05
MAX_SPREAD = 0.20
QUALITY_LIMIT = 256  # Same output cap as the guarded Chat runner.
BENCH_LIMIT = 96
BENCH_PROMPT = "\n".join(
    f"Record {i}: minutes worked = {i * 3}; state = {'ready' if i % 2 else 'waiting'}."
    for i in range(1, 17)
) + "\nIn one paragraph explain how to group these records by state and sum their minutes."


def fingerprint(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def reference(context):
    return {"num_ctx": context, "num_gpu": 0, "num_batch": REFERENCE_BATCH}


def candidate_sets(session):
    hardware = session.identity["hardware"]
    logical = min(hardware["logical_cores"], len(hardware["affinity"]))
    physical = min(hardware["physical_cores"], logical)
    # Spend less on speculative batches when the reference has little headroom.
    plans = []
    for context in context_options(session.max_context):
        margin = session.memory_margin(context, REFERENCE_BATCH)
        if margin < 0:
            continue
        large_batch = 128 if margin < GB else 256 if margin < 3 * GB else 512
        threads = sorted({max(1, physical // 2), physical, logical})
        safe_batches = [batch for batch in (64, large_batch)
                        if session.memory_margin(context, batch) >= 0]
        options = [{**reference(context), "num_thread": t, "num_batch": b}
                   for t in threads for b in safe_batches]
        if options:
            plans.append((context, options))
    return plans


def common_options(options, cap):
    return {**options, "num_predict": cap, "temperature": 0, "seed": 42}


def measurement(final, output, options):
    fields = ("prompt_eval_count", "eval_count", "total_duration", "load_duration",
              "prompt_eval_duration", "eval_duration")
    if any(type(final.get(key)) is not int for key in fields):
        return None
    prompt, tokens, total, load, prefill, decode = (final[key] for key in fields)
    # Require useful data and enough context for the whole requested output.
    if (not output.strip() or min(prompt, tokens, total, prefill, decode) <= 0
            or load < 0 or total < prefill + decode
            or tokens > options["num_predict"]
            or prompt + options["num_predict"] >= options["num_ctx"]):
        return None
    return {**{key: final[key] for key in fields}, "output_sha256": fingerprint(output)}


def timing_summary(runs):
    samples = [item["prompt_eval_duration"] + item["eval_duration"]
               for item in runs if item is not None]
    return {
        "valid_runs": len(samples),
        "median_ns": statistics.median(samples) if samples else None,
        "spread": max(samples) / min(samples) - 1 if samples else None,
    }


def compare_speed(baseline, candidate):
    report = {"gain": None, "round_gains": []}
    if any(len(group) != BENCH_ROUNDS or None in group for group in (baseline, candidate)):
        return {**report, "status": "INCONCLUSIVE_MEASUREMENTS"}
    signatures = {(item["prompt_eval_count"], item["eval_count"], item["output_sha256"])
                  for group in (baseline, candidate) for item in group}
    if len(signatures) != 1:
        return {**report, "status": "DIFFERENT_OUTPUT_OR_TOKEN_COUNTS"}
    base_times, trial_times = (
        [item["prompt_eval_duration"] + item["eval_duration"] for item in group]
        for group in (baseline, candidate)
    )
    if any(min(times) <= 0 for times in (base_times, trial_times)):
        return {**report, "status": "INCONCLUSIVE_MEASUREMENTS"}
    gains = [1 - trial / base for base, trial in zip(base_times, trial_times)]
    report["round_gains"] = gains
    if any(max(times) > min(times) * (1 + MAX_SPREAD)
           for times in (base_times, trial_times)):
        return {**report, "status": "UNSTABLE_TIMING"}
    if any(trial > base * (1 - MIN_GAIN) for base, trial in zip(base_times, trial_times)):
        return {**report, "status": "BELOW_MINIMUM_GAIN"}
    return {**report, "gain": statistics.median(gains), "status": "REPEATABLE_GAIN"}


def speed_gain(baseline, candidate):
    return compare_speed(baseline, candidate)["gain"]


def benchmark_orders(size):
    indices = list(range(1, size))
    middle = len(indices) // 2
    return [[0, *indices], [*reversed(indices), 0],
            [*indices[middle:], 0, *indices[:middle]]]


def benchmark(session, context, candidates):
    choices = [reference(context), *candidates]
    samples = [[] for _ in choices]
    orders = benchmark_orders(len(choices))
    interrupted = None
    try:
        for round_number, order in enumerate(orders, 1):
            for index in order:
                config = choices[index]
                print(f"Benchmark round {round_number}/{BENCH_ROUNDS}: {config}", flush=True)
                options = common_options(config, BENCH_LIMIT)
                final, output = run_prompt(session, BENCH_PROMPT, options)
                result = measurement(final, output, options)
                if final.get("done_reason") not in ("stop", "length"):
                    result = None
                samples[index].append(result)
    except (KeyboardInterrupt, RuntimeError, OSError) as exc:
        interrupted = str(exc) or "Cancelled"
    return {"context": context, "reference_options": choices[0], "reference": samples[0],
            "reference_summary": timing_summary(samples[0]), "round_order": orders,
            "interrupted": interrupted,
            "trials": [{"options": config, "runs": run,
                        "summary": timing_summary(run), **compare_speed(samples[0], run)}
                       for config, run in zip(candidates, samples[1:])]}


def choose_winner(benchmarks):
    eligible = [trial for group in benchmarks for trial in group["trials"]
                if trial["gain"] is not None]
    # Preserve usable context first, then choose the faster configuration
    # among trials measured at that same context.
    return max(eligible, key=lambda trial: (trial["options"]["num_ctx"], trial["gain"]),
               default=None)


def assess_source(source, test, framework):
    static_pass, problems = evaluate_code(source, test)
    if test["language"] == "Python":
        compiled, diagnostics = compile_python(source)
        behavior = check_python(source, test["id"])
    else:
        compiled, diagnostics = ((None, ["No usable .NET SDK"])
                                 if framework is None else compile_csharp(source, framework))
        checker = door_check if test["id"] == "unity_door" else flashlight_check
        behavior = checker(clean_source(source))
    status = {"SUPPORTED_PATTERN_PASS": "PASS", "REQUIREMENT_FAIL": "FAIL",
              "PASS": "PASS", "FAIL": "FAIL"}.get(behavior["status"], "INCONCLUSIVE")
    # Treat infrastructure failures separately from compiler errors in the sample.
    compile_status = ("PASS" if compiled is True else
                      "FAIL" if compiled is False and (test["language"] == "Python" or
                          any("Generated.cs(" in line and "error CS" in line
                              for line in diagnostics))
                      else "INCONCLUSIVE")
    return {"structure": "PASS" if static_pass else "FAIL",
            "compilation": compile_status, "behavior": status,
            "diagnostics": problems + diagnostics, "behavior_detail": behavior.get("reason")}


def quality_check(session, candidate):
    try:
        framework = get_dotnet_framework()
    except (RuntimeError, OSError, subprocess.SubprocessError):
        framework = None
    baseline = reference(candidate["num_ctx"])
    base_options = common_options(baseline, QUALITY_LIMIT)
    tuned_options = common_options(candidate, QUALITY_LIMIT)
    cases = []
    interrupted = None
    for index, test in enumerate(TESTS):
        prompt = build_prompt(test)
        print(f"Quality comparison: {test['id']}", flush=True)
        pair = {"baseline": {}, "candidate": {}}
        configs = [("baseline", base_options), ("candidate", tuned_options)]
        for label, options in configs if index % 2 == 0 else reversed(configs):
            try:
                final, output = run_prompt(session, prompt, options)
                metrics = measurement(final, output, options)
                pair[label] = {"metrics": metrics, "finish": final.get("done_reason")}
                if metrics is not None and final.get("done_reason") == "stop":
                    pair[label].update(assess_source(extract_code(output), test, framework))
            except (KeyboardInterrupt, RuntimeError, OSError) as exc:
                interrupted = str(exc) or "Cancelled"
                break
        cases.append({"task": test["id"], "prompt_sha256": fingerprint(prompt), **pair})
        if interrupted:
            break
    return {"baseline_options": base_options, "candidate_options": tuned_options,
            "cases": cases, "status": quality_status(cases),
            "summary": quality_summary(cases), "interrupted": interrupted}


def quality_summary(cases):
    summary = {}
    for label in ("baseline", "candidate"):
        results = [case[label].get(stage) for case in cases for stage in STAGES]
        passed, failed = results.count("PASS"), results.count("FAIL")
        summary[label] = {
            "PASS": passed,
            "FAIL": failed,
            "INCONCLUSIVE": len(TESTS) * len(STAGES) - passed - failed,
        }
    return summary


def quality_status(cases):
    expected = {test["id"]: fingerprint(build_prompt(test)) for test in TESTS}
    if not isinstance(cases, list) or len(cases) != len(expected):
        return "INCONCLUSIVE"
    statuses = set()
    for case in cases:
        if not isinstance(case, dict) or case.get("task") not in expected:
            return "INCONCLUSIVE"
        if expected.pop(case["task"]) != case.get("prompt_sha256"):
            return "INCONCLUSIVE"
        baseline, candidate = case.get("baseline", {}), case.get("candidate", {})
        input_counts = [(item.get("metrics") or {}).get("prompt_eval_count")
                        for item in (baseline, candidate)]
        if (baseline.get("finish") != "stop" or candidate.get("finish") != "stop"
                or not input_counts[0] or input_counts[0] != input_counts[1]):
            statuses.add("INCONCLUSIVE")
            continue
        for stage in STAGES:
            before, after = baseline.get(stage), candidate.get(stage)
            if before not in ("PASS", "FAIL") or after not in ("PASS", "FAIL"):
                statuses.add("INCONCLUSIVE")
            elif before == "PASS" and after == "FAIL":
                statuses.add("REGRESSION")
            elif before == "FAIL":
                statuses.add("REQUIREMENTS_FAILED")
    if expected:
        return "INCONCLUSIVE"
    return next((status for status in ("REGRESSION", "INCONCLUSIVE", "REQUIREMENTS_FAILED")
                 if status in statuses), "LIMITED_TESTS_PASSED")


def check_options(session, options):
    hardware = session.identity["hardware"]
    logical = min(hardware["logical_cores"], len(hardware["affinity"]))
    if not isinstance(options, dict) or set(options) != {
        "num_ctx", "num_gpu", "num_thread", "num_batch",
    } or any(type(value) is not int for value in options.values()):
        raise RuntimeError("Invalid Smart profile options")
    if (options["num_ctx"] not in context_options(session.max_context)
            or options["num_gpu"] != 0 or not 1 <= options["num_thread"] <= logical
            or options["num_batch"] not in (64, 128, 256, 512)):
        raise RuntimeError("Smart profile options are outside the tested limits")


def verify_profile(profile, session):
    if profile.get("schema_version") != SCHEMA or profile.get("method") != METHOD:
        raise RuntimeError("Smart profile uses an older or unsupported test method. Run Smart Auto Optimizer again")
    if (profile.get("identity") != session.identity
            or profile.get("automatic_apply_approved") is not False):
        raise RuntimeError("Smart profile does not match the model digest, hardware or Ollama version")
    options = profile.get("recommended_options")
    check_options(session, options)
    context = options["num_ctx"]
    outcome = profile.get("quality", {})
    if (outcome.get("status") != "LIMITED_TESTS_PASSED" or outcome.get("interrupted")
            or outcome.get("baseline_options") != common_options(reference(context), QUALITY_LIMIT)
            or outcome.get("candidate_options") != common_options(options, QUALITY_LIMIT)
            or quality_status(outcome.get("cases")) != "LIMITED_TESTS_PASSED"):
        raise RuntimeError("Smart profile quality evidence is incomplete or failed")
    performance = profile.get("performance", {})
    try:
        gain = speed_gain(performance["reference"], performance["candidate"])
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        gain = None
    if (gain is None or performance.get("gain") != gain
            or performance.get("prompt_sha256") != fingerprint(BENCH_PROMPT)
            or performance.get("reference_options") != common_options(reference(context), BENCH_LIMIT)
            or performance.get("candidate_options") != common_options(options, BENCH_LIMIT)):
        raise RuntimeError("Smart profile lacks repeated comparable performance evidence")
    return options


def optimize_model(session, plans):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    report_path = RESULTS / f"smart_{stamp}.json"
    report = {"method": METHOD, "identity": session.identity, "benchmarks": [],
              "planned_contexts": [context for context, _ in plans],
              "automatic_apply_approved": False}
    try:
        for context, candidates in plans:
            group = benchmark(session, context, candidates)
            report["benchmarks"].append(group)
            if group.get("interrupted"):
                raise RuntimeError(group["interrupted"])
        winner = choose_winner(report["benchmarks"])
        if winner is None:
            report["status"] = "NO_REPEATABLE_GAIN"
            return report
        options = winner["options"]
        group = next(row for row in report["benchmarks"] if row["context"] == options["num_ctx"])
        quality = quality_check(session, options)
        report.update(quality=quality, quality_candidate_options=options, status=quality["status"])
        if quality.get("interrupted"):
            raise RuntimeError(quality["interrupted"])
        if quality["status"] == "LIMITED_TESTS_PASSED":
            session.preflight(options)
            profile = {
                "schema_version": SCHEMA, "method": METHOD,
                "model": session.identity["model"], "identity": session.identity,
                "recommended_options": options, "quality": quality,
                "performance": {"reference": group["reference"], "candidate": winner["runs"],
                                "gain": winner["gain"], "prompt_sha256": fingerprint(BENCH_PROMPT),
                                "reference_options": common_options(reference(options["num_ctx"]), BENCH_LIMIT),
                                "candidate_options": common_options(options, BENCH_LIMIT)},
                "automatic_apply_approved": False,
            }
            verify_profile(profile, session)
            profile_path = PROFILES / f"optimized_smart_{stamp}.json"
            write_json(profile_path, profile)
            report["profile"] = profile_path.name
            report["recommended_options"] = options
        return report
    except (KeyboardInterrupt, RuntimeError, OSError, ValueError, TypeError) as exc:
        report.update(status="INCONCLUSIVE", reason=str(exc) or "Cancelled")
        return report
    finally:
        write_json(report_path, report)
        print(f"Smart result: {report.get('status', 'INCONCLUSIVE')}. Report: {report_path}")


def interactive():
    models = [m for m in model_list("/api/tags") if local_model(m)]
    if not models:
        raise RuntimeError("No local Ollama models found")
    print("\n=== SMART AUTO OPTIMIZER ===")
    for number, model in enumerate(models, 1):
        print(f"{number}. {model['name']}")
    choice = input("Select model (0 = back): ").strip()
    if not choice.isdecimal() or not 1 <= int(choice) <= len(models):
        return
    session = Session.inspect(models[int(choice) - 1]["name"])
    if model_list("/api/ps"):
        raise RuntimeError("Ollama has a loaded model. Let it unload before retrying")
    plans = candidate_sets(session)
    if not plans:
        raise RuntimeError("No context fits available RAM and Windows commit limits")
    print(f"Reference: automatic threads, batch {REFERENCE_BATCH}, CPU only")
    for context, choices in plans:
        print(f"{context} tokens: " + ", ".join(
            f"{item['num_thread']} threads / batch {item['num_batch']}" for item in choices
        ))
    request_count = BENCH_ROUNDS * sum(1 + len(choices) for _, choices in plans)
    print(f"{BENCH_ROUNDS} rounds per context: {request_count} benchmark requests, "
          f"plus up to {2 * len(TESTS)} quality requests. "
          "Runs local CPU inference and saves a report. Apply is a separate menu action.")
    if input("Type TEST to run the comparisons: ").strip() == "TEST":
        report = optimize_model(session, plans)
        if report.get("profile"):
            print(f"Profile available for manual Apply: {report['profile']}")
        else:
            print("No Smart profile was recommended. Current settings remain in use.")
            if report.get("reason"):
                print(report["reason"])
        if quality := report.get("quality"):
            for label, counts in quality["summary"].items():
                print(f"Quality {label}: {counts['PASS']}/{len(TESTS) * len(STAGES)} passed; "
                      f"{counts['FAIL']} failed, {counts['INCONCLUSIVE']} inconclusive.")
            for case in quality["cases"]:
                for label in ("baseline", "candidate"):
                    unmet = [f"{stage}={case[label].get(stage, 'INCONCLUSIVE')}"
                             for stage in STAGES if case[label].get(stage) != "PASS"]
                    if unmet:
                        print(f"  {case['task']} ({label}): {', '.join(unmet)}")
