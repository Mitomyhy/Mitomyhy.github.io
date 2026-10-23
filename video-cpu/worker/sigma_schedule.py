"""Reviewed LTX 0.9.6 distilled schedule for Diffusers 0.40 I2V.

The I2V caller supplies its own linear sigmas and does not expose a sigmas
argument. Bind this adapter to one scheduler instance, preserving the original
setter and rejecting configurations which would transform the intended values.
Source recipe: Lightricks/LTX-Video configs/ltxv-2b-0.9.6-distilled.yaml.
Copied from the model-free, real-scheduler tested CPU probe on 2026-10-22.
"""
from __future__ import annotations

import inspect
import math
import types

DISTILLED_SIGMAS = [1.0, 0.9937, 0.9875, 0.9812, 0.9750, 0.9094, 0.7250, 0.4219]


def install_exact_sigma_schedule(scheduler, requested_sigmas, report):
    import numpy as np

    if not isinstance(requested_sigmas, list) or not requested_sigmas:
        raise ValueError("A sigma schedule must be a nonempty list")
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in requested_sigmas):
        raise ValueError("Sigma values must be finite numbers")
    fixed = [float(value) for value in requested_sigmas]
    if not all(0 < value <= 1 for value in fixed):
        raise ValueError("Denoising sigmas must be in (0, 1]")
    if any(left <= right for left, right in zip(fixed, fixed[1:])):
        raise ValueError("Sigmas must be strictly descending")
    config = scheduler.config
    transforms = {
        "use_dynamic_shifting": bool(getattr(config, "use_dynamic_shifting", False)),
        "shift": float(getattr(config, "shift", 1.0)),
        "effective_shift": float(getattr(scheduler, "shift", 1.0)),
        "shift_terminal": getattr(config, "shift_terminal", None),
        "invert_sigmas": bool(getattr(config, "invert_sigmas", False)),
        "use_karras_sigmas": bool(getattr(config, "use_karras_sigmas", False)),
        "use_exponential_sigmas": bool(getattr(config, "use_exponential_sigmas", False)),
        "use_beta_sigmas": bool(getattr(config, "use_beta_sigmas", False)),
    }
    if (transforms["use_dynamic_shifting"] or transforms["shift_terminal"] is not None
            or transforms["invert_sigmas"] or transforms["use_karras_sigmas"]
            or transforms["use_exponential_sigmas"] or transforms["use_beta_sigmas"]
            or transforms["shift"] != 1.0 or transforms["effective_shift"] != 1.0):
        raise ValueError(f"Exact sigma adapter requires identity transforms: {transforms}")
    if int(getattr(config, "num_train_timesteps", 1000)) != 1000:
        raise ValueError("Reviewed sigma adapter requires 1000 training timesteps")
    original = scheduler.set_timesteps
    if "sigmas" not in inspect.signature(original).parameters:
        raise RuntimeError("Scheduler setter has no sigmas parameter")
    report.update({"adapter": "scheduler_instance_original_set_timesteps",
                   "input_sigmas": fixed, "expected_steps": len(fixed),
                   "transforms": transforms, "verified": False})

    def exact_set_timesteps(self, num_inference_steps=None, device=None,
                            sigmas=None, mu=None, timesteps=None):
        if timesteps is not None:
            raise ValueError("Pipeline timesteps must remain None")
        if num_inference_steps is not None and num_inference_steps != len(fixed):
            raise ValueError("Pipeline step count differs from distilled schedule")
        original(num_inference_steps=len(fixed), device=device, sigmas=fixed,
                 mu=None, timesteps=None)
        actual_sigmas = self.sigmas.detach().cpu().numpy()
        actual_timesteps = self.timesteps.detach().cpu().numpy()
        expected_sigmas = np.asarray(fixed + [0.0], dtype=np.float32)
        expected_timesteps = np.asarray(fixed, dtype=np.float32) * 1000.0
        if (actual_sigmas.shape != expected_sigmas.shape
                or not np.allclose(actual_sigmas, expected_sigmas, rtol=0, atol=1e-7)):
            raise RuntimeError("Scheduler changed exact distilled sigmas")
        if (actual_timesteps.shape != expected_timesteps.shape
                or not np.allclose(actual_timesteps, expected_timesteps, rtol=0, atol=1e-4)
                or self.num_inference_steps != len(fixed)):
            raise RuntimeError("Scheduler changed exact distilled timesteps")
        report.update({"verified": True, "effective_sigmas": actual_sigmas.tolist(),
                       "effective_timesteps": actual_timesteps.tolist()})

    scheduler.set_timesteps = types.MethodType(exact_set_timesteps, scheduler)
    return len(fixed)
