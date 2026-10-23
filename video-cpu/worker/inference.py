"""Single real model generation in an isolated child; never an effects fallback."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback

from model_validation import validate_model
from mp4_export import encode_rgb_frames
from low_memory_pipeline import enable_low_memory_pipeline
from sigma_schedule import DISTILLED_SIGMAS, install_exact_sigma_schedule

STARTED = time.monotonic()


def emit(**event):
    event.setdefault("elapsed_seconds", time.monotonic() - STARTED)
    print(json.dumps(event, ensure_ascii=True), flush=True)


def run(args):
    # Wait until parent attaches the Windows Job memory limit.
    gate_deadline = time.monotonic() + 20
    while not args.start_marker.is_file():
        if time.monotonic() > gate_deadline:
            raise TimeoutError("Parent did not establish process containment")
        time.sleep(0.05)
    request = json.loads(args.request.read_text(encoding="utf-8"))
    model = Path(request["model"])
    validate_model(model)
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                       "HF_DATASETS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                       "OMP_NUM_THREADS": str(request["threads"]),
                       "MKL_NUM_THREADS": str(request["threads"])})
    emit(phase="import_runtime")
    import numpy as np
    import openvino as ov
    import torch
    from PIL import Image
    from optimum.intel.openvino import OVLTXImageToVideoPipeline

    torch.set_num_threads(request["threads"])
    torch.set_num_interop_threads(1)
    options = request["options"]
    emit(phase="load_model", device="CPU", cpu=ov.Core().get_property("CPU", "FULL_DEVICE_NAME"))
    pipe = OVLTXImageToVideoPipeline.from_pretrained(
        str(model), device="CPU", compile=False, local_files_only=True,
        trust_remote_code=False,
        ov_config={"INFERENCE_NUM_THREADS": request["threads"], "NUM_STREAMS": 1,
                   "PERFORMANCE_HINT": "LATENCY", "INFERENCE_PRECISION_HINT": "f32"})
    if str(pipe._device).upper() != "CPU" or str(pipe.device).lower() != "cpu":
        raise RuntimeError("Pipeline did not select CPU")
    decoder_inputs = [value.get_any_name() for value in pipe.vae_decoder.model.inputs]
    if bool(getattr(pipe.vae_decoder.config, "timestep_conditioning", False)) and "timestep" not in decoder_inputs:
        raise RuntimeError("Exported VAE decoder is missing its required timestep input")
    # Preserve the exported dynamic IR. The upstream reshape path doubles the
    # transformer batch for CFG even with guidance1, unnecessarily raising RAM.
    # Do not compile every component together. The reviewed single-use adapter
    # compiles/releases stages sequentially and verifies each CPU device.
    def residency_event(name, **details):
        emit(phase=name, **details)

    residency = enable_low_memory_pipeline(pipe, event_callback=residency_event)
    schedule_report = {}
    steps = install_exact_sigma_schedule(pipe.scheduler, DISTILLED_SIGMAS, schedule_report)
    with Image.open(request["image"]) as source:
        image = source.convert("RGB").copy()

    def callback(pipeline, step, timestep, callback_kwargs):
        if Path(request["cancel_marker"]).exists():
            raise InterruptedError("Generation cancelled")
        emit(phase="denoising", step=int(step) + 1, total_steps=steps)
        return callback_kwargs

    emit(phase="generate_cpu", total_steps=steps)
    result = residency.generate(image=image, prompt=request["prompt"], height=options["height"],
                  width=options["width"], num_frames=options["frames"],
                  num_inference_steps=steps, guidance_scale=1.0,
                  frame_rate=options["fps"], max_sequence_length=128,
                  decode_timestep=0.05, decode_noise_scale=0.025,
                  generator=torch.Generator(device="cpu").manual_seed(options["seed"]),
                  output_type="np", callback_on_step_end=callback,
                  callback_on_step_end_tensor_inputs=["latents"])
    if not schedule_report.get("verified"):
        raise RuntimeError("Distilled schedule was not applied")
    data = np.asarray(result.frames)
    expected = (1, options["frames"], options["height"], options["width"], 3)
    if data.shape != expected or not np.isfinite(data).all():
        raise RuntimeError(f"Unexpected generated frames: {data.shape}")
    pixels = np.clip(data * 255.0, 0, 255).round().astype(np.uint8) if np.issubdtype(data.dtype, np.floating) else data.astype(np.uint8)
    emit(phase="encode_mp4")
    output = Path(request["output"])
    metadata = encode_rgb_frames(request["ffmpeg"], pixels[0].tobytes(),
                                 width=options["width"], height=options["height"],
                                 frame_count=options["frames"], fps=options["fps"], output=output)
    emit(phase="completed", width=options["width"], height=options["height"],
         device="CPU", scheduler_verified=True, **metadata)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--start-marker", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args)
        return 0
    except BaseException as exc:
        emit(phase="failed", error_type=type(exc).__name__, error=str(exc)[:1500])
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
