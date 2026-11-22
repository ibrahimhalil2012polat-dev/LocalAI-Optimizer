"""LocalAI Optimizer v0.6.0: opt-in, reversible Ollama API profile.

The original Ollama model is NEVER modified. Applying writes one local JSON
profile. Only conversations launched with this script use the configured
runtime options. A quality failure shared by baseline and optimized may be
accepted manually; regressions and inconclusive results block activation.
"""

import http.client
import json
import os
import platform
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import psutil

from benchmark import commit_available, memory_limits
from context_optimizer import preflight
from optimizer import wait_until_unloaded
from smart_optimizer import METHOD as SMART_METHOD, SCHEMA as SMART_SCHEMA
from smart_optimizer import QUALITY_LIMIT, fingerprint, verify_profile
from smart_runtime import Session as SmartSession

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
PROFILES = ROOT / "profiles"
ACTIVE = PROFILES / "active_optimization.json"
HOST, PORT = "127.0.0.1", 11434
MB = 1024 ** 2
GB = 1024 ** 3


def load_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError(f"Cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid JSON object: {path.name}")
    return value


def ollama_request(endpoint, payload=None):
    connection = http.client.HTTPConnection(HOST, PORT, timeout=20)
    try:
        method = "POST" if payload is not None else "GET"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        connection.request(method, endpoint, body=body,
                           headers={"Content-Type": "application/json"})
        with connection.getresponse() as response:
            data = response.read()
            if response.status != 200:
                raise RuntimeError(f"Ollama HTTP {response.status}: {data[:250]!r}")
            return json.loads(data.decode("utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Ollama at {HOST}:{PORT} is unavailable: {exc}") from exc
    finally:
        connection.close()


def find_model(name, digest):
    models = ollama_request("/api/tags").get("models", [])
    for model in models:
        if model.get("name") == name and model.get("digest") == digest:
            if model.get("remote_host") or model.get("remote_model"):
                raise RuntimeError("Remote models are unsupported")
            return model
    raise RuntimeError("Model is missing or its digest has changed. Re-run optimization.")


def compatible_quality(model_name, digest):
    reports = sorted(RESULTS.glob("quality_summary_*.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for path in reports:
        try:
            report = load_json(path)
        except RuntimeError:
            continue
        if report.get("model") == model_name and report.get("model_digest") == digest:
            return path, report
    raise RuntimeError("No matching quality summary. Run the v0.5.0 tests first.")


def validate_quality(summary, path, model_name, digest, requested):
    """Fail closed on missing evidence, regressions, or mismatched options."""
    if summary.get("model") != model_name or summary.get("model_digest") != digest:
        raise RuntimeError("Quality report does not match model/digest")
    if summary.get("complete") is not True:
        raise RuntimeError("Quality comparison is incomplete")
    if summary.get("regressions") or summary.get("unresolved"):
        raise RuntimeError("Regressions/inconclusive tests block application")
    if summary.get("status") not in ("LIMITED_TESTS_PASSED", "QUALITY_REQUIREMENTS_FAILED"):
        raise RuntimeError("Quality status blocks application")
    if summary.get("automatic_apply_approved") is not False:
        raise RuntimeError("Unexpected automatic-approval flag; review report manually")

    rows = summary.get("rows")
    if not isinstance(rows, list) or len(rows) != 12 or any(
        not isinstance(row, dict) for row in rows
    ):
        raise RuntimeError("Expected all 12 quality check rows")
    keys = {(row.get("task"), row.get("stage")) for row in rows}
    required = {(task, stage) for task in
                ("python_clamp", "python_even", "unity_door", "unity_flashlight")
                for stage in ("structure", "compilation", "behavior")}
    if keys != required:
        raise RuntimeError("Expected one row per required quality stage")
    failures = []
    for row in rows:
        baseline, optimized = row.get("baseline"), row.get("optimized")
        if baseline not in ("PASS", "FAIL") or optimized not in ("PASS", "FAIL"):
            raise RuntimeError("Unknown quality status in report")
        if baseline == "PASS" and optimized == "FAIL":
            raise RuntimeError("Detected optimized-only quality failure")
        if optimized == "FAIL":
            if baseline != "FAIL":
                raise RuntimeError("Optimized-only quality failure")
            failures.append(f"{row['task']}/{row['stage']}")
    if set(failures) != set(summary.get("shared_failures", [])):
        raise RuntimeError("Shared-failure details do not match")
    if bool(failures) != (summary.get("status") == "QUALITY_REQUIREMENTS_FAILED"):
        raise RuntimeError("Quality status contradicts failure rows")

    quality_name = summary.get("source_reports", {}).get("code_quality")
    if not isinstance(quality_name, str) or Path(quality_name).name != quality_name:
        raise RuntimeError("Missing quality test source")
    quality_path = RESULTS / quality_name
    if not quality_path.is_file():
        raise RuntimeError("Original code-quality report is missing")
    quality = load_json(quality_path)
    if not quality.get("completed") or quality.get("model") != model_name:
        raise RuntimeError("Original quality test is incomplete")
    if quality.get("model_digest") != digest:
        raise RuntimeError("Original quality test model changed")
    configs = quality.get("configurations", {})
    tested = configs.get("optimized", {})
    if any(type(tested.get(key)) is not int or tested[key] != requested[key]
           for key in ("num_thread", "num_batch", "num_gpu")):
        raise RuntimeError("Optimized settings differ from tested quality settings")
    context = tested.get("num_ctx")
    if type(context) is not int or not 512 <= context <= 8192:
        raise RuntimeError("Invalid quality-test context")
    if requested["num_ctx"] < context:
        raise RuntimeError("Profile context smaller than quality-test context")
    # Crucially, 8192 was benchmarked but *not* tested for code quality.
    requested = dict(requested)
    requested["num_ctx"] = context
    return requested, failures, quality_path.name


def profile_candidates():
    return sorted(PROFILES.glob("optimized_*.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def read_candidate(path):
    profile = load_json(path)
    if profile.get("status") != "PRELIMINARY_VALIDATED":
        raise RuntimeError("Candidate has not passed combined validation")
    if profile.get("identical_validation_output") is not True:
        raise RuntimeError("Combined output consistency not verified")
    model_name, digest = profile.get("model"), profile.get("model_digest")
    if not isinstance(model_name, str) or not model_name or not isinstance(digest, str) or not digest:
        raise RuntimeError("Invalid model identity")
    hardware = profile.get("hardware", {})
    if not isinstance(hardware, dict):
        raise RuntimeError("Missing hardware fingerprint")
    if hardware.get("os") != platform.system():
        raise RuntimeError("Profile was created on a different operating system")
    if hardware.get("physical_cores") != psutil.cpu_count(logical=False):
        raise RuntimeError("Profile was created with a different physical CPU count")
    if hardware.get("logical_cores") != psutil.cpu_count(logical=True):
        raise RuntimeError("Profile was created with a different logical CPU count")
    measured_ram = hardware.get("total_ram_gib")
    if type(measured_ram) not in (float, int) or abs(
        measured_ram - psutil.virtual_memory().total / GB
    ) > max(0.5, measured_ram * 0.05):
        raise RuntimeError("Profile RAM fingerprint does not match this computer")
    values = profile.get("recommended_options", {})
    if not isinstance(values, dict):
        raise RuntimeError("Missing recommended options")
    result = {}
    for name, lower, upper in (
        ("num_thread", 1, psutil.cpu_count() or 1),
        ("num_batch", 1, 512),
        ("num_ctx", 512, 8192),
        ("num_gpu", 0, 0),
    ):
        value = values.get(name)
        if type(value) is not int or not lower <= value <= upper:
            raise RuntimeError(f"Unsupported {name}: {value}")
        result[name] = value
    validation = profile.get("validation", {}).get("options", {})
    if not isinstance(validation, dict) or any(validation.get(k) != v for k, v in result.items()):
        raise RuntimeError("Candidate differs from final validation run")
    return model_name, digest, result


def save_active(state):
    PROFILES.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=PROFILES,
                                delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, indent=2)
        # Publish the complete state atomically without replacing concurrent Apply.
        if os.name == "nt":
            os.rename(temporary, ACTIVE)
        else:
            os.link(temporary, ACTIVE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def smart_evidence(profile):
    model = profile.get("model")
    if not isinstance(model, str) or not model:
        raise RuntimeError("Smart profile is missing its model")
    session = SmartSession.inspect(model)
    options = verify_profile(profile, session)
    signature = fingerprint(json.dumps(profile, sort_keys=True))
    return session, options, signature


def apply_smart(path, profile):
    session, options, signature = smart_evidence(profile)
    session.preflight(options)
    print("\n=== APPLY PREVIEW ===")
    print("Model:", session.identity["model"])
    print("Settings to use:", options)
    print("Quality: all limited comparisons passed")
    print("Scope: only conversations through LocalAI Optimizer")
    if input("Type 'APPLY' to activate: ").strip() != "APPLY":
        print("Cancelled. No settings changed.")
        return
    current, verified_options, verified_signature = smart_evidence(load_json(path))
    if verified_signature != signature or verified_options != options:
        raise RuntimeError("Smart profile changed after preview")
    current.preflight(options)
    save_active({
        "schema_version": SMART_SCHEMA, "method": SMART_METHOD,
        "model": current.identity["model"], "model_digest": current.identity["digest"],
        "options": options, "max_output_tokens": QUALITY_LIMIT,
        "quality_status": "LIMITED_TESTS_PASSED", "known_shared_failures": [],
        "source_profile": path.name, "source_fingerprint": signature,
        "applied_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "localai_optimizer_chat_only", "automatically_approved": False,
    })
    print("\nAPPLIED to local chat runner only. Original Ollama model unchanged.")


def apply():
    if ACTIVE.exists():
        print("An active profile already exists. Restore first.")
        return
    candidates = profile_candidates()
    if not candidates:
        print("No optimized profile found. Run Smart Auto Optimizer or Build combined profile.")
        return
    print("\n=== AVAILABLE PROFILES ===")
    for i, path in enumerate(candidates, 1):
        try:
            p = load_json(path)
            print(f"{i}. {p.get('model', 'unknown')} | {path.name}")
        except RuntimeError:
            print(f"{i}. INVALID | {path.name}")
    value = input("Select profile (0 = cancel): ").strip()
    if not value.isdigit() or not 1 <= int(value) <= len(candidates):
        return
    path = candidates[int(value) - 1]
    profile = load_json(path)
    if profile.get("method") == SMART_METHOD or profile.get("schema_version") == SMART_SCHEMA:
        apply_smart(path, profile)
        return
    name, digest, options = read_candidate(path)
    quality_path, summary = compatible_quality(name, digest)
    applied_options, failures, source_name = validate_quality(
        summary, quality_path, name, digest, options
    )
    model = find_model(name, digest)

    print("\n=== APPLY PREVIEW ===")
    print("Model:", name)
    print("Combined proposal:", options)
    print("Settings to use:", applied_options)
    if applied_options["num_ctx"] != options["num_ctx"]:
        print("CONTEXT LIMITED: quality was tested at", applied_options["num_ctx"],
              "not", options["num_ctx"])
    print("Quality status:", summary["status"])
    print("Known shared failures:", failures)
    print("Original Ollama model: unchanged")
    print("Scope: ONLY conversations through this script")
    print("Automatic approval: DISABLED")
    if failures:
        print("WARNING: Both configurations FAILED required behaviors.")
        print("This is a manual, experimental opt-in; not a quality endorsement.")

    consent = "I ACCEPT" if failures else "APPLY"
    if input(f"Type '{consent}' to activate: ").strip() != consent:
        print("Cancelled. No settings changed.")
        return
    if not wait_until_unloaded():
        raise RuntimeError("Loaded Ollama model was not released")
    eligible, reason = preflight(model, applied_options["num_ctx"],
                                 applied_options["num_batch"])
    if not eligible:
        raise RuntimeError("Safety preflight BLOCKED application: " + reason)
    find_model(name, digest)  # Reconfirm digest just before saving.
    save_active({
        "schema_version": 1,
        "model": name,
        "model_digest": digest,
        "options": applied_options,
        "max_output_tokens": 256,
        "quality_status": summary["status"],
        "known_shared_failures": failures,
        "source_profile": path.name,
        "source_summary": quality_path.name,
        "source_quality_test": source_name,
        "applied_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "localai_optimizer_chat_only",
        "automatically_approved": False,
    })
    print("\nAPPLIED to local chat runner only. Original Ollama model unchanged.")
    print("Select 'Run optimized chat' to use these options.")


def restore():
    if not ACTIVE.exists():
        print("Already using defaults. Nothing to restore.")
        return
    try:
        print("\nActive model:", load_json(ACTIVE).get("model"))
    except RuntimeError:
        print("\nThe active profile cannot be read. Restore will remove it.")
    print("Restore means this tool stops passing optimized settings.")
    print("The original Ollama model was never changed.")
    if input("Type RESTORE to confirm: ").strip() != "RESTORE":
        print("Cancelled")
        return
    ACTIVE.unlink()
    print("RESTORED. Use the original model normally in Ollama.")


def status():
    print("\n=== OPTIMIZATION STATUS ===")
    if not ACTIVE.exists():
        print("No active optimization. Original Ollama settings are unchanged.")
        return
    state = load_json(ACTIVE)
    print("Model:", state.get("model"))
    print("Applied options:", state.get("options"))
    print("Quality:", state.get("quality_status"))
    print("Shared failures:", state.get("known_shared_failures"))
    print("Scope: LocalAI Optimizer chat only (not global Ollama)")


def stream_chat(model_name, messages, options):
    """One guarded request; unload the model after each answer.

    Closing the client socket is best-effort only; Ollama may continue running.
    """
    initial = psutil.virtual_memory()
    reserve, critical = memory_limits(initial.total)
    cancelled = threading.Event()
    done = threading.Event()
    reason = []
    samples = []
    connection = http.client.HTTPConnection(HOST, PORT, timeout=180)
    connection.connect()
    sock = connection.sock

    def cancel(message):
        if not cancelled.is_set():
            reason.append(message)
            cancelled.set()
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def monitor():
        consecutive = 0
        try:
            while not done.is_set():
                now = time.monotonic()
                ram = psutil.virtual_memory().available
                commit = commit_available()
                samples.append((now, ram))
                consecutive = consecutive + 1 if (
                    ram < critical or (commit is not None and commit < critical)
                ) else 0
                if consecutive >= 3:
                    cancel("Critical memory pressure")
                    break
                if len(samples) >= 6:
                    old_time, old_ram = samples[-6]
                    rate = (old_ram - ram) / max(now - old_time, .01)
                    if rate > 128 * MB and ram < reserve and ram - 2 * rate < critical:
                        cancel("Rapid memory depletion")
                        break
                done.wait(.5)
        except Exception as exc:
            cancel("Memory monitor error: " + str(exc))

    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()
    output = []
    completed = False
    try:
        payload = {"model": model_name, "messages": messages, "options": options,
                   "stream": True, "keep_alive": 0}
        connection.request("POST", "/api/chat", body=json.dumps(payload).encode("utf-8"),
                           headers={"Content-Type": "application/json"})
        with connection.getresponse() as response:
            if response.status != 200:
                raise RuntimeError(f"Ollama HTTP {response.status}: {response.read(256)!r}")
            print("\nAssistant: ", end="", flush=True)
            for line in response:
                if cancelled.is_set():
                    break
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])
                piece = (chunk.get("message") or {}).get("content") or ""
                if piece:
                    output.append(piece)
                    print(piece, end="", flush=True)
                if chunk.get("done"):
                    completed = True
                    break
            print()
    except (OSError, ValueError) as exc:
        raise RuntimeError("Ollama connection failed or was interrupted: " + str(exc)) from exc
    finally:
        done.set()
        worker.join(timeout=3)
        connection.close()
    if cancelled.is_set() or not completed:
        raise RuntimeError("Request cancelled/incomplete: " + "; ".join(reason))
    return "".join(output)


def chat_evidence(state, opts):
    name, digest = state.get("model"), state.get("model_digest")
    candidate_name = state.get("source_profile")
    if not isinstance(candidate_name, str) or Path(candidate_name).name != candidate_name:
        raise RuntimeError("Invalid source profile")
    candidate = PROFILES / candidate_name
    profile = load_json(candidate)
    if (state.get("schema_version") == SMART_SCHEMA or profile.get("schema_version") == SMART_SCHEMA
            or state.get("method") == SMART_METHOD or profile.get("method") == SMART_METHOD):
        session, checked, signature = smart_evidence(profile)
        if (state.get("schema_version") != SMART_SCHEMA or state.get("method") != SMART_METHOD
                or state.get("automatically_approved") is not False
                or state.get("source_fingerprint") != signature or checked != opts
                or (session.identity["model"], session.identity["digest"]) != (name, digest)):
            raise RuntimeError("Active settings no longer match the approved Smart profile")
        return session
    find_model(name, digest)
    model_name, profile_digest, candidate_opts = read_candidate(candidate)
    if (model_name, profile_digest) != (name, digest):
        raise RuntimeError("Source profile changed")
    if any(candidate_opts.get(key) != opts.get(key) for key in
           ("num_thread", "num_batch", "num_gpu")):
        raise RuntimeError("Active settings no longer match source profile")
    if candidate_opts["num_ctx"] < opts["num_ctx"]:
        raise RuntimeError("Active context exceeds source profile")
    quality_name = state.get("source_summary")
    if not isinstance(quality_name, str) or Path(quality_name).name != quality_name:
        raise RuntimeError("Invalid quality report path")
    summary = load_json(RESULTS / quality_name)
    checked_opts, failures, _ = validate_quality(summary, RESULTS / quality_name,
                                                  name, digest, candidate_opts)
    if checked_opts != opts or failures != state.get("known_shared_failures"):
        raise RuntimeError("Quality evidence or applied settings changed")
    return None


def chat():
    if not ACTIVE.exists():
        print("No active configuration. Choose Apply Optimization first.")
        return
    state = load_json(ACTIVE)
    if state.get("scope") != "localai_optimizer_chat_only":
        raise RuntimeError("Unsupported profile scope")
    name = state.get("model")
    opts = state.get("options", {})
    if not isinstance(opts, dict) or any(type(opts.get(k)) is not int for k in
                                        ("num_ctx", "num_batch", "num_thread", "num_gpu")):
        raise RuntimeError("Corrupt active profile")
    if not (512 <= opts["num_ctx"] <= 8192 and 1 <= opts["num_batch"] <= 512 and
            1 <= opts["num_thread"] <= (psutil.cpu_count() or 1) and opts["num_gpu"] == 0):
        raise RuntimeError("Unsafe profile options")
    smart_session = chat_evidence(state, opts)
    if state.get("known_shared_failures"):
        print("WARNING: Known quality failures:", state["known_shared_failures"])
    print("\nOptimized local chat:", name)
    print("Options:", opts)
    print("Type /exit to return to the menu. Nothing is sent to cloud services by this tool.")

    history = []
    while True:
        try:
            prompt = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if prompt.lower() in ("/exit", "/quit"):
            break
        if not prompt:
            continue
        if len(prompt) > 1200:
            print("Prompt too long for this guarded prototype (max 1200 characters).")
            continue
        messages = history[-4:] + [{"role": "user", "content": prompt}]
        char_budget = max(1000, 2 * (opts["num_ctx"] - 512))
        while sum(len(m["content"]) for m in messages) > char_budget and len(messages) > 1:
            messages.pop(0)
        if smart_session is not None:
            try:
                smart_session.preflight(opts)
            except RuntimeError as exc:
                print("SAFETY BLOCK:", exc)
                continue
        else:
            if not wait_until_unloaded():
                print("Loaded model did not unload; skipping request.")
                continue
            model = find_model(name, state.get("model_digest"))
            allowed, reason = preflight(model, opts["num_ctx"], opts["num_batch"])
            if not allowed:
                print("SAFETY BLOCK:", reason)
                continue
        request_options = dict(opts)
        request_options["num_predict"] = QUALITY_LIMIT if smart_session is not None else 256
        try:
            answer = stream_chat(name, messages, request_options)
        except RuntimeError as exc:
            print("ERROR:", exc)
            print("Memory interruption is best effort. Check: ollama ps")
            continue
        history = messages + [{"role": "assistant", "content": answer}]


def main():
    print("LocalAI Optimizer v0.7.0")
    print("=== APPLY / RESTORE / CHAT ===")
    while True:
        print("\n1. Apply Optimization (manual only)")
        print("2. Run optimized chat")
        print("3. Restore Defaults")
        print("4. Show status")
        print("0. Exit")
        try:
            option = input("\nSelect: ").strip()
            if option == "0":
                return
            if option == "1":
                apply()
            elif option == "2":
                chat()
            elif option == "3":
                restore()
            elif option == "4":
                status()
            else:
                print("Invalid selection")
        except EOFError:
            return
        except KeyboardInterrupt:
            print("\nCancelled")
        except RuntimeError as exc:
            print("BLOCKED:", exc)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print("ERROR:", exc)


if __name__ == "__main__":
    main()
