"""Model-free test against the actual installed Diffusers scheduler."""
import importlib.util
import unittest

from sigma_schedule import DISTILLED_SIGMAS, install_exact_sigma_schedule


@unittest.skipUnless(importlib.util.find_spec("diffusers") and importlib.util.find_spec("torch"),
                     "Requires the reviewed ML environment; no model weights required")
class SigmaTests(unittest.TestCase):
    def test_exact_schedule_and_first_real_euler_step(self):
        import numpy as np
        import torch
        from diffusers import FlowMatchEulerDiscreteScheduler

        scheduler = FlowMatchEulerDiscreteScheduler(num_train_timesteps=1000, shift=1.0,
                                                    use_dynamic_shifting=False)
        report = {}
        count = install_exact_sigma_schedule(scheduler, DISTILLED_SIGMAS, report)
        scheduler.set_timesteps(num_inference_steps=count, device="cpu",
                                sigmas=np.linspace(1, 1 / count, count).tolist(), mu=.5)
        self.assertTrue(report["verified"])
        sample = torch.tensor([1.0, -2.0], dtype=torch.float32)
        prediction = torch.tensor([.5, -1.0], dtype=torch.float32)
        actual = scheduler.step(prediction, scheduler.timesteps[0], sample, return_dict=False)[0]
        expected = sample + (DISTILLED_SIGMAS[1] - DISTILLED_SIGMAS[0]) * prediction
        self.assertTrue(torch.allclose(actual, expected, rtol=0, atol=1e-7))

    def test_rejects_incompatible_scheduler_transforms(self):
        from diffusers import FlowMatchEulerDiscreteScheduler
        for flag, value in (("use_dynamic_shifting", True), ("shift", 2.0),
                            ("shift_terminal", .1), ("invert_sigmas", True),
                            ("use_karras_sigmas", True), ("use_exponential_sigmas", True),
                            ("use_beta_sigmas", True)):
            with self.subTest(flag=flag):
                scheduler = FlowMatchEulerDiscreteScheduler(**{"num_train_timesteps": 1000, "shift": 1.0, flag: value})
                with self.assertRaises(ValueError):
                    install_exact_sigma_schedule(scheduler, DISTILLED_SIGMAS, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
