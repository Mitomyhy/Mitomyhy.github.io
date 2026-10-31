"""Single-use sequential CPU compilation for Optimum LTX image-to-video.

T5 and image-VAE outputs are copied before unloading their compiled requests and
IRs. The transformer stays compiled across denoising steps and is released before
the decoder compiles. Configuration/dtype metadata remain available throughout.
This controls residency; only a measured real-model run can establish RAM fit.
"""
from __future__ import annotations

import copy
import gc
import time
import types


def clone_owned_output(value, memo=None):
    """Detach all returned arrays/tensors from OpenVINO's shared result storage."""
    import numpy as np
    import torch

    memo = {} if memo is None else memo
    if id(value) in memo:
        return memo[id(value)]
    if isinstance(value, torch.Tensor):
        cloned = value.detach().clone()
        memo[id(value)] = cloned
        return cloned
    if isinstance(value, np.ndarray):
        cloned = value.copy()
        memo[id(value)] = cloned
        return cloned
    if isinstance(value, dict):
        # Preserve transformers.ModelOutput's attribute and integer-index access.
        cloned = type(value)((key, clone_owned_output(item, memo)) for key, item in value.items())
        memo[id(value)] = cloned
        return cloned
    if isinstance(value, tuple):
        items = [clone_owned_output(item, memo) for item in value]
        cloned = type(value)(*items) if hasattr(value, "_fields") else tuple(items)
        memo[id(value)] = cloned
        return cloned
    if isinstance(value, list):
        cloned = [clone_owned_output(item, memo) for item in value]
        memo[id(value)] = cloned
        return cloned
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    if hasattr(value, "__dict__"):
        # DiagonalGaussianDistribution keeps tensors in parameters/mean/std/etc.
        # A shallow object clone plus recursive attribute clones preserves its API.
        cloned = copy.copy(value)
        memo[id(value)] = cloned
        for key, item in vars(value).items():
            setattr(cloned, key, clone_owned_output(item, memo))
        return cloned
    raise TypeError(f"Unsupported shared output type: {type(value).__name__}")


class SequentialCpuResidency:
    """Destructively adapt one pipeline for one guidance1 image+prompt call.

    Released components cannot be compiled/reused. Construct a fresh pipeline for
    another request. No model files are modified and no class-wide monkey patch
    is performed; wrappers are bound only to these four component instances.
    """

    def __init__(self, pipeline, event_callback=None):
        if str(pipeline._device).upper() != "CPU" or str(pipeline.device).lower() != "cpu":
            raise ValueError("Sequential residency requires an explicitly CPU pipeline")
        self.pipeline = pipeline
        self.event_callback = event_callback
        self.started = time.monotonic()
        self.used = False
        self.parts = {name: getattr(pipeline, name) for name in ("text_encoder", "vae_encoder", "transformer", "vae_decoder")}
        self.state = {
            "mode": "single_use_sequential_cpu",
            "guidance_scale_required": 1.0,
            "output_storage_cloned_before_release": True,
            "events": [],
            "components": {name: {"compiled": False, "released": False, "forward_calls": 0} for name in self.parts},
        }
        for name, part in self.parts.items():
            if part is None or getattr(part, "model", None) is None:
                raise ValueError(f"Missing uncompiled IR component: {name}")
            if getattr(part, "request", None) is not None:
                raise ValueError(f"Component already compiled before sequential residency: {name}")
        for name, part in self.parts.items():
            self._wrap(name, part)
        self._event("sequential_cpu_prepared")

    def _event(self, name, **details):
        event = {"event": name, "elapsed_seconds": time.monotonic() - self.started, **details}
        self.state["events"].append(event)
        if self.event_callback:
            self.event_callback(name, **details)

    def release(self, name):
        entry, part = self.state["components"][name], self.parts[name]
        if entry["released"]:
            return
        # Keep part.config/ov_config/name/dtype metadata for remaining pipeline math.
        part.request = None
        part.model = None
        entry["released"] = True
        gc.collect()
        self._event("component_released", component=name)

    def _wrap(self, name, part):
        original_compile = part.compile
        original_forward = part.forward
        owner = self

        def compile_cpu(instance):
            entry = owner.state["components"][name]
            if entry["released"]:
                raise RuntimeError(f"Sequential CPU pipeline is single-use: {name} was already released")
            if instance.request is None:
                owner._event("component_compile_start", component=name)
                original_compile()
                devices = list(instance.request.get_property("EXECUTION_DEVICES"))
                if devices != ["CPU"]:
                    raise RuntimeError(f"{name} compiled on unexpected devices: {devices}")
                entry["compiled"] = True
                entry["execution_devices"] = devices
                owner._event("component_compile_end", component=name, execution_devices=devices)

        def forward_once(instance, *args, **kwargs):
            entry = owner.state["components"][name]
            if entry["released"]:
                raise RuntimeError(f"Sequential CPU pipeline is single-use: {name} cannot run twice")
            if name == "vae_decoder":
                # The caller has finished denoising and unpacked all latents before
                # reaching this hook. The decoder's config remains intact.
                owner.release("transformer")
                owner.release("text_encoder")
                owner.release("vae_encoder")
            owner._event("component_forward_start", component=name)
            result = original_forward(*args, **kwargs)
            cloned = clone_owned_output(result)
            del result
            entry["forward_calls"] += 1
            owner._event("component_output_cloned", component=name)
            if name in ("text_encoder", "vae_encoder", "vae_decoder"):
                owner.release(name)
            return cloned

        part.compile = types.MethodType(compile_cpu, part)
        part.forward = types.MethodType(forward_once, part)

    def close(self):
        for name in self.parts:
            self.release(name)

    def generate(self, *args, **kwargs):
        """Call once through this guard, rather than reusing the destroyed pipeline."""
        if self.used:
            raise RuntimeError("Sequential CPU pipeline is single-use; load a fresh pipeline for another request")
        if kwargs.get("guidance_scale", 3.0) != 1.0:
            raise ValueError("Sequential CPU pipeline requires explicit guidance_scale1.0")
        self.used = True
        try:
            return self.pipeline(*args, **kwargs)
        finally:
            self.close()


def enable_low_memory_pipeline(pipeline, event_callback=None):
    return SequentialCpuResidency(pipeline, event_callback)
