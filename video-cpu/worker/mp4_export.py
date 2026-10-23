"""Software H.264 export of actual generated RGB frames, without frame repeats."""
from __future__ import annotations

from pathlib import Path
import subprocess


def export_dimensions(width, height, short_side=720):
    if (not all(isinstance(value, int) and not isinstance(value, bool) and value > 0
                for value in (width, height, short_side)) or short_side % 2):
        raise ValueError("Positive integer dimensions and an even export short side are required")
    # Round the long side to the nearest even pixel for H.264 yuv420p. Keep the
    # short side exact; the resulting aspect-ratio rounding is at most one pixel.
    if width >= height:
        return 2 * ((width * short_side + height) // (2 * height)), short_side
    return short_side, 2 * ((height * short_side + width) // (2 * width))


def export_metadata(width, height, frame_count, fps):
    if (not isinstance(frame_count, int) or isinstance(frame_count, bool) or frame_count <= 0
            or not isinstance(fps, int) or isinstance(fps, bool) or fps <= 0):
        raise ValueError("Frame count and frame rate must be positive integers")
    out_width, out_height = export_dimensions(width, height)
    return {"native_resolution": {"width": width, "height": height},
            "export_resolution": {"width": out_width, "height": out_height},
            "upscaled": out_width > width or out_height > height,
            "frames": frame_count, "fps": fps, "duration_seconds": frame_count / fps,
            "format": "mp4", "codec": "h264", "pixel_format": "yuv420p", "audio": False}


def encode_rgb_frames(ffmpeg, frames, *, width, height, frame_count, fps, output, timeout=120):
    metadata = export_metadata(width, height, frame_count, fps)
    if not isinstance(frames, (bytes, bytearray, memoryview)):
        raise TypeError("Generated RGB frame storage must be bytes-like")
    if len(frames) != width * height * frame_count * 3:
        raise ValueError("RGB byte count does not match the actual generated frame dimensions")
    ffmpeg = Path(ffmpeg)
    if not ffmpeg.is_file():
        raise FileNotFoundError("Local ffmpeg executable is unavailable")
    output = Path(output)
    partial = output.with_name("video.partial.mp4")
    export = metadata["export_resolution"]
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{width}x{height}",
               "-framerate", str(fps), "-i", "pipe:0", "-an",
               "-vf", f"scale={export['width']}:{export['height']}:flags=lanczos",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads", "1",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(partial)]
    try:
        result = subprocess.run(command, input=frames, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, check=False, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError("MP4 encoding failed: " + result.stderr.decode("utf-8", "replace")[-1000:])
        if not partial.is_file() or partial.stat().st_size < 64:
            raise RuntimeError("Encoder did not produce a video")
        with partial.open("rb") as stream:
            if stream.read(12)[4:8] != b"ftyp":
                raise RuntimeError("Encoder output does not have an MP4 file-type box")
        partial.replace(output)
        return {**metadata, "bytes": output.stat().st_size}
    finally:
        partial.unlink(missing_ok=True)
