import unittest
from datetime import datetime, timezone, timedelta
from profile_cache import eligible, estimate, cached_profiles
from pathlib import Path
from tempfile import TemporaryDirectory
import json


class ProfileCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.sig = {"logical": 12, "ollama": "test"}
        self.model = {"name": "model", "digest": "digest"}
        self.options = {"num_thread": 8, "num_batch": 128, "num_ctx": 2048, "num_gpu": 0}
        self.p = {
            "cache_created_utc": self.now.isoformat(),
            "cache_fingerprint": self.sig, "model": "model", "model_digest": "digest",
            "status": "PRELIMINARY_VALIDATED", "identical_validation_output": True,
            "recommended_options": self.options,
            "validation": {"options": self.options.copy(), "output_hash": "hash",
                           "generation_speed": 8, "prompt_speed": 20, "ram_increase_gib": 5},
            "historical_thread_result": {}, "historical_batch_result": {},
            "historical_context_result": {}
        }

    def valid(self):
        return eligible(self.p, self.model, self.sig, self.now)

    def test_match(self):
        self.assertTrue(self.valid())

    def test_expired(self):
        self.p["cache_created_utc"] = (self.now - timedelta(days=31)).isoformat()
        self.assertFalse(self.valid())

    def test_digest_change(self):
        self.model["digest"] = "new"
        self.assertFalse(self.valid())

    def test_hardware_change(self):
        self.sig = {"logical": 8, "ollama": "test"}
        self.assertFalse(self.valid())

    def test_unvalidated(self):
        self.p["status"] = "REVIEW_REQUIRED"
        self.assertFalse(self.valid())

    def test_mismatched_options(self):
        self.p["validation"]["options"]["num_thread"] = 4
        self.assertFalse(self.valid())

    def test_missing_signature(self):
        self.sig = None
        self.assertFalse(self.valid())

    def test_estimate(self):
        self.assertEqual(estimate([self.p], self.options)["generation_seconds_range"], [16, 16])

    def test_no_data(self):
        self.assertIsNone(estimate([], self.options))

    def test_invalid_speed(self):
        self.p["validation"]["generation_speed"] = float("nan")
        self.assertFalse(self.valid())

    def test_files(self):
        with TemporaryDirectory() as d:
            p = Path(d)
            (p / "optimized_good.json").write_text(json.dumps(self.p))
            (p / "optimized_bad.json").write_text("{")
            self.assertEqual(len(cached_profiles(p, self.model, self.sig, self.now)), 1)


if __name__ == "__main__":
    unittest.main()
