"""Safety and workflow tests. Ollama inference and activation are isolated."""

import copy
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import apply_optimization as activation
import smart_optimizer as smart
import smart_runtime as runtime


def session():
    return runtime.Session(
        {"model": "local:latest", "digest": "a" * 64, "ollama_version": "0.40.15",
         "size": runtime.GB, "hardware": {"logical_cores": 6, "physical_cores": 4,
                                         "affinity": list(range(6)), "cpu": "Test CPU"}},
        {"general.architecture": "llama", "llama.block_count": 16,
         "llama.attention.head_count": 16, "llama.attention.head_count_kv": 4,
         "llama.embedding_length": 2048},
        32768,
    )


def sample(duration):
    return {"prompt_eval_count": 55, "eval_count": 50,
            "prompt_eval_duration": duration, "eval_duration": duration,
            "total_duration": 3 * duration, "load_duration": duration,
            "output_sha256": "a" * 64}


def passing_quality(options):
    cases = []
    for test in smart.TESTS:
        result = {"finish": "stop", "metrics": sample(100),
                  **dict.fromkeys(smart.STAGES, "PASS")}
        cases.append({"task": test["id"], "prompt_sha256": smart.fingerprint(smart.build_prompt(test)),
                      "baseline": copy.deepcopy(result), "candidate": copy.deepcopy(result)})
    return {"cases": cases, "status": "LIMITED_TESTS_PASSED",
            "baseline_options": smart.common_options(smart.reference(options["num_ctx"]), smart.QUALITY_LIMIT),
            "candidate_options": smart.common_options(options, smart.QUALITY_LIMIT)}


def tested_profile(s):
    options = {**smart.reference(4096), "num_thread": 2, "num_batch": 64}
    base, candidate = [sample(100)] * 3, [sample(85)] * 3
    return {
        "schema_version": smart.SCHEMA, "method": smart.METHOD, "model": s.identity["model"],
        "identity": copy.deepcopy(s.identity), "recommended_options": options,
        "quality": passing_quality(options), "automatic_apply_approved": False,
        "performance": {"reference": base, "candidate": candidate,
                        "gain": smart.speed_gain(base, candidate),
                        "prompt_sha256": smart.fingerprint(smart.BENCH_PROMPT),
                        "reference_options": smart.common_options(smart.reference(4096), smart.BENCH_LIMIT),
                        "candidate_options": smart.common_options(options, smart.BENCH_LIMIT)},
    }


class SmartOptimizerTests(unittest.TestCase):
    def test_candidates_respect_model_limit_cpu_affinity_and_ram(self):
        s = session()
        s.max_context = 4096
        s.identity["hardware"]["affinity"] = [0, 1]
        margins = {(1024, 512): runtime.GB // 2, (2048, 512): 2 * runtime.GB,
                   (4096, 512): -1, (2048, 256): -1}
        with patch.object(s, "memory_margin", side_effect=lambda *key: margins.get(key, 0)):
            plans = smart.candidate_sets(s)
        self.assertEqual([ctx for ctx, _ in plans], [1024, 2048])
        self.assertTrue(all(1 <= o["num_thread"] <= 2 for _, options in plans for o in options))
        self.assertEqual({o["num_batch"] for o in plans[0][1]}, {64, 128})
        self.assertEqual({o["num_batch"] for o in plans[1][1]}, {64})

    def test_candidates_cross_threads_with_both_batches(self):
        s = session()
        s.max_context = 1024
        with patch.object(s, "memory_margin", return_value=2 * runtime.GB):
            [(_, candidates)] = smart.candidate_sets(s)
        self.assertEqual({(o["num_thread"], o["num_batch"]) for o in candidates},
                         {(2, 64), (2, 256), (4, 64), (4, 256), (6, 64), (6, 256)})

    def test_preflight_blocks_busy_low_commit_and_missing_commit(self):
        s = session()
        ram = SimpleNamespace(total=16 * runtime.GB, available=12 * runtime.GB)
        options = smart.reference(2048)
        with patch.object(s, "check_runtime"), patch.object(runtime, "model_list", return_value=[{}]):
            with self.assertRaisesRegex(RuntimeError, "loaded model"):
                s.preflight(options)
        with patch.object(s, "check_runtime"), patch.object(runtime, "model_list", return_value=[]), \
             patch.object(runtime.psutil, "virtual_memory", return_value=ram):
            for commit in (None, runtime.GB):
                with patch.object(runtime, "commit_available", return_value=commit):
                    with self.assertRaisesRegex(RuntimeError, "commit"):
                        s.preflight(options)
            with patch.object(runtime, "commit_available", return_value=10 * runtime.GB):
                self.assertGreater(s.preflight(options), 0)

    def test_cloud_and_invalid_identity_rejected(self):
        for name in ("model:cloud", "model-cloud:latest", "remote.example/model:latest"):
            self.assertFalse(runtime.local_model({"name": name, "size": 100}))
        self.assertFalse(runtime.local_model({"name": "local", "size": 1, "remote_host": "remote"}))
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            runtime.digest_for({"digest": "abc"})

    def test_profile_matches_digest_cpu_and_server_version(self):
        s = session()
        profile = tested_profile(s)
        self.assertEqual(smart.verify_profile(profile, s), profile["recommended_options"])
        for key in ("digest", "ollama_version", "hardware"):
            changed = copy.deepcopy(profile)
            changed["identity"][key] = "different"
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "does not match"):
                smart.verify_profile(changed, s)

    def test_speed_requires_three_consistent_equivalent_runs(self):
        base, fast = [sample(100)] * 3, [sample(85)] * 3
        self.assertIsNotNone(smart.speed_gain(base, fast))
        self.assertIsNotNone(smart.speed_gain(base, [sample(95)] * 3))
        changed = {**sample(85), "output_sha256": "b" * 64}
        for reference, candidate, expected in (
            (base[:2], fast[:2], "INCONCLUSIVE_MEASUREMENTS"),
            (base, [*fast[:2], sample(96)], "BELOW_MINIMUM_GAIN"),
            ([sample(200), *base[:2]], fast, "UNSTABLE_TIMING"),
            (base, [*fast[:2], changed], "DIFFERENT_OUTPUT_OR_TOKEN_COUNTS"),
            (base, [*fast[:2], {**sample(85), "eval_count": 49}],
             "DIFFERENT_OUTPUT_OR_TOKEN_COUNTS"),
        ):
            with self.subTest(expected=expected):
                comparison = smart.compare_speed(reference, candidate)
                self.assertIsNone(comparison["gain"])
                self.assertEqual(comparison["status"], expected)

    def test_benchmark_rotates_reference_with_identical_workload(self):
        options = tested_profile(session())["recommended_options"]
        context = options["num_ctx"]
        candidates = [options, {**options, "num_batch": 128}]
        final = {**sample(100), "done_reason": "stop"}
        with (
            patch.object(smart, "run_prompt", return_value=(final, "content")) as generate,
            redirect_stdout(io.StringIO()),
        ):
            bench = smart.benchmark(session(), context, candidates)
        self.assertEqual(bench["round_order"], [[0, 1, 2], [2, 1, 0], [2, 0, 1]])
        self.assertEqual(bench["reference_summary"]["valid_runs"], 3)
        for trial in bench["trials"]:
            self.assertEqual(trial["status"], "BELOW_MINIMUM_GAIN")
            self.assertEqual(trial["summary"]["median_ns"], 200)
        for call in generate.call_args_list:
            self.assertEqual(call.args[1], smart.BENCH_PROMPT)
            expected = smart.common_options(smart.reference(context), smart.BENCH_LIMIT)
            actual = call.args[2].copy()
            actual.pop("num_thread", None)
            actual["num_batch"] = expected["num_batch"]
            self.assertEqual(actual, expected)

    def test_interrupted_run_keeps_measurement_without_issuing_profile(self):
        s = session()
        options = tested_profile(s)["recommended_options"]
        final = {**sample(100), "done_reason": "stop"}
        with (
            tempfile.TemporaryDirectory() as folder,
            redirect_stdout(io.StringIO()),
            patch.object(smart, "RESULTS", Path(folder)),
            patch.object(smart, "run_prompt",
                         side_effect=[(final, "content"), RuntimeError("Low commit")]),
            patch.object(smart, "quality_check") as quality,
        ):
            result = smart.optimize_model(s, [(options["num_ctx"], [options])])
            self.assertEqual(len(list(Path(folder).glob("smart_*.json"))), 1)
        quality.assert_not_called()
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(result["reason"], "Low commit")
        self.assertEqual(result["benchmarks"][0]["reference_summary"]["valid_runs"], 1)
        self.assertNotIn("recommended_options", result)

    def test_live_comparisons_require_explicit_test_confirmation(self):
        s = session()
        options = tested_profile(s)["recommended_options"]
        model = {"name": s.identity["model"], "size": 100}
        with (
            patch.object(smart, "model_list", side_effect=[[model], []] * 2),
            patch.object(smart.Session, "inspect", return_value=s),
            patch.object(smart, "candidate_sets", return_value=[(4096, [options])]),
            patch.object(smart, "optimize_model", return_value={}) as optimize,
            redirect_stdout(io.StringIO()),
        ):
            with patch("builtins.input", side_effect=["1", "yes"]):
                smart.interactive()
            optimize.assert_not_called()
            with patch("builtins.input", side_effect=["1", "TEST"]):
                smart.interactive()
            optimize.assert_called_once()

    def test_quality_uses_same_prompt_context_and_generation_controls(self):
        s = session()
        options = tested_profile(s)["recommended_options"]
        final = {**sample(100), "done_reason": "stop"}
        with patch.object(smart, "run_prompt", return_value=(final, "content")) as generate, \
             patch.object(smart, "get_dotnet_framework", return_value="net8.0"), \
             patch.object(smart, "assess_source", return_value=dict.fromkeys(smart.STAGES, "PASS")), \
             redirect_stdout(io.StringIO()):
            quality = smart.quality_check(s, options)
        self.assertEqual(quality["status"], "LIMITED_TESTS_PASSED")
        self.assertEqual(generate.call_count, 8)
        for first, second in zip(generate.call_args_list[::2], generate.call_args_list[1::2]):
            self.assertEqual(first.args[1], second.args[1])
            for field in ("num_ctx", "num_gpu", "num_predict", "seed", "temperature"):
                self.assertEqual(first.args[2][field], second.args[2][field])

    def test_interrupted_quality_keeps_failures_and_counts_missing_checks(self):
        s = session()
        options = tested_profile(s)["recommended_options"]
        final = {**sample(100), "done_reason": "stop"}
        assessment = {"structure": "PASS", "compilation": "PASS", "behavior": "FAIL"}
        with (
            patch.object(smart, "run_prompt",
                         side_effect=[(final, "content"), RuntimeError("Low commit")]),
            patch.object(smart, "get_dotnet_framework", return_value="net8.0"),
            patch.object(smart, "assess_source", return_value=assessment),
            redirect_stdout(io.StringIO()),
        ):
            result = smart.quality_check(s, options)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(result["interrupted"], "Low commit")
        self.assertEqual(result["summary"]["baseline"], {"PASS": 2, "FAIL": 1, "INCONCLUSIVE": 9})
        self.assertEqual(result["summary"]["candidate"], {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 12})

    def test_no_gain_preserves_active_settings_and_skips_quality(self):
        s = session()
        options = tested_profile(s)["recommended_options"]
        bench = {"context": options["num_ctx"], "trials": [{"options": options, "gain": None}]}
        with (
            tempfile.TemporaryDirectory() as folder,
            redirect_stdout(io.StringIO()),
            patch.object(smart, "RESULTS", Path(folder) / "results"),
            patch.object(smart, "PROFILES", Path(folder) / "profiles"),
            patch.object(smart, "benchmark", return_value=bench),
            patch.object(smart, "quality_check") as quality,
        ):
            smart.PROFILES.mkdir()
            active = smart.PROFILES / "active_optimization.json"
            active.write_text("{}", encoding="utf-8")
            result = smart.optimize_model(s, [(options["num_ctx"], [options])])
            self.assertEqual(list(smart.PROFILES.iterdir()), [active])
            self.assertEqual(active.read_text(encoding="utf-8"), "{}")
        quality.assert_not_called()
        self.assertEqual(result["status"], "NO_REPEATABLE_GAIN")
        self.assertNotIn("recommended_options", result)

    def test_quality_regression_shared_failure_and_uncertainty_are_blocked(self):
        s = session()
        for mode, expected in (("regression", "REGRESSION"), ("shared", "REQUIREMENTS_FAILED"),
                               ("truncated", "INCONCLUSIVE"), ("different_prompt", "INCONCLUSIVE")):
            profile = tested_profile(s)
            cases = profile["quality"]["cases"]
            door = next(case for case in cases if case["task"] == "unity_door")
            if mode in ("regression", "shared"):
                door["candidate"]["behavior"] = "FAIL"
                if mode == "shared":
                    door["baseline"]["behavior"] = "FAIL"
            elif mode == "truncated":
                door["candidate"]["finish"] = "length"
            else:
                door["candidate"]["metrics"]["prompt_eval_count"] += 1
            with self.subTest(mode=mode):
                self.assertEqual(smart.quality_status(cases), expected)
                with self.assertRaisesRegex(RuntimeError, "quality evidence"):
                    smart.verify_profile(profile, s)

    def test_smart_run_saves_candidate_only_after_quality(self):
        s = session()
        template = tested_profile(s)
        options = template["recommended_options"]
        base, trials = [sample(100)] * 3, [sample(85)] * 3
        bench = {"context": 4096, "reference": base, "trials": [
            {"options": options, "runs": trials, "gain": smart.speed_gain(base, trials)}]}
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            base_dir = Path(folder)
            with patch.object(smart, "PROFILES", base_dir / "profiles"), \
                 patch.object(smart, "RESULTS", base_dir / "results"), \
                 patch.object(smart, "benchmark", return_value=bench), \
                 patch.object(s, "preflight"):
                failed = passing_quality(options)
                failed["status"] = "INCONCLUSIVE"
                with patch.object(smart, "quality_check", return_value=failed):
                    blocked = smart.optimize_model(s, [(4096, [options])])
                    self.assertNotIn("profile", blocked)
                    self.assertNotIn("recommended_options", blocked)
                    self.assertEqual(blocked["quality_candidate_options"], options)
                with patch.object(smart, "quality_check", return_value=passing_quality(options)):
                    result = smart.optimize_model(s, [(4096, [options])])
                self.assertTrue((base_dir / "profiles" / result["profile"]).is_file())
                self.assertFalse((base_dir / "profiles" / "active_optimization.json").exists())

    def test_smart_apply_chat_restore_and_refusal_in_temporary_folder(self):
        s = session()
        profile = tested_profile(s)
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            directory = Path(folder)
            path = directory / "optimized_smart_test.json"
            active = directory / "active_optimization.json"
            smart.write_json(path, profile)
            with patch.object(activation, "PROFILES", directory), \
                 patch.object(activation, "ACTIVE", active), \
                 patch.object(activation.psutil, "cpu_count", return_value=6), \
                 patch.object(activation.SmartSession, "inspect", return_value=s), \
                 patch.object(s, "preflight"), \
                 patch.object(activation, "stream_chat", return_value="OK") as chat:
                with patch("builtins.input", side_effect=["1", ""]):
                    activation.apply()
                self.assertFalse(active.exists())
                with patch("builtins.input", side_effect=["1", "APPLY"]):
                    activation.apply()
                self.assertTrue(active.exists())
                with self.assertRaises(FileExistsError):
                    activation.save_active({"replaced": True})
                with patch("builtins.input", side_effect=["Hello", "/exit"]):
                    activation.chat()
                chat.assert_called_once()
                self.assertEqual(chat.call_args.args[2]["num_predict"], smart.QUALITY_LIMIT)
                modified = {**profile, "extra": "changed after approval"}
                smart.write_json(path, modified)
                with self.assertRaisesRegex(RuntimeError, "approved Smart profile"):
                    activation.chat()
                with patch("builtins.input", return_value="RESTORE"):
                    activation.restore()
                self.assertFalse(active.exists())


if __name__ == "__main__":
    unittest.main()
