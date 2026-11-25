import unittest
from hardware_advisor import GB, recommend


class HardwareAdvisorTests(unittest.TestCase):
    def evaluate(self, model=None, info=None, **kwargs):
        args = dict(total=16*GB, available=12*GB, physical=8, logical=12,
                    reserve=GB, estimate_kv=lambda info, ctx: ctx*GB/4096)
        args.update(kwargs)
        return recommend(
            model if model is not None else {"name": "local", "size": 4*GB},
            info if info is not None else {"general.architecture": "llama",
                                           "llama.context_length": 8192}, **args)

    def test_fit(self):
        result = self.evaluate()
        self.assertEqual(result["options"], {"num_thread": 8, "num_batch": 128,
                                             "num_ctx": 8192, "num_gpu": 0})
        self.assertTrue(result["requires_live_validation"])

    def test_low_ram(self):
        self.assertEqual(self.evaluate(available=5*GB)["status"], "NO_RECOMMENDATION")

    def test_context_limit(self):
        result = self.evaluate(info={"general.architecture": "llama",
                                     "llama.context_length": 2048})
        self.assertEqual(result["options"]["num_ctx"], 2048)

    def test_affinity(self):
        self.assertEqual(self.evaluate(affinity=3)["options"]["num_thread"], 3)

    def test_remote(self):
        self.assertEqual(self.evaluate(model={"name": "x:cloud", "size": GB})["status"],
                         "NO_RECOMMENDATION")

    def test_loaded(self):
        self.assertEqual(self.evaluate(loaded=True)["status"], "NO_RECOMMENDATION")

    def test_unknown_kv(self):
        self.assertEqual(self.evaluate(estimate_kv=lambda i,c: None)["status"],
                         "NO_RECOMMENDATION")

    def test_low_commit(self):
        self.assertEqual(self.evaluate(commit=2*GB)["status"], "NO_RECOMMENDATION")

    def test_unknown_context(self):
        self.assertEqual(self.evaluate(info={})["status"], "NO_RECOMMENDATION")

    def test_smaller_context(self):
        self.assertEqual(self.evaluate(available=8*GB)["options"]["num_ctx"], 4096)


if __name__ == "__main__":
    unittest.main()
