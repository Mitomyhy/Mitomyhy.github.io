"""Real encode/decode tests using synthetic RGB frames, never AI model output."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from mp4_export import encode_rgb_frames, export_dimensions, export_metadata

BASE = Path(__file__).resolve().parent
DEFAULT_FFMPEG = BASE.parent / ".work/media-read-tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"
FFMPEG = Path(os.environ.get("MITOM_TEST_FFMPEG", str(DEFAULT_FFMPEG)))


class ExportTests(unittest.TestCase):
    def test_even_aspect_preserving_short_side(self):
        expected = {(512, 320): (1152, 720), (320, 512): (720, 1152),
                    (384, 384): (720, 720), (704, 448): (1132, 720),
                    (448, 704): (720, 1132), (320, 192): (1200, 720)}
        for native, output in expected.items():
            self.assertEqual(export_dimensions(*native), output)
            self.assertEqual(min(output), 720)
            self.assertTrue(all(value % 2 == 0 for value in output))

    def test_listing_duration_uses_all_native_frames_without_repeats(self):
        metadata = export_metadata(512, 320, 49, 8)
        self.assertEqual(metadata["duration_seconds"], 6.125)
        self.assertEqual(metadata["frames"], 49)
        self.assertEqual(metadata["native_resolution"], {"width": 512, "height": 320})
        self.assertTrue(metadata["upscaled"])
        self.assertFalse(metadata["audio"])

    def test_rejects_incomplete_generated_frame_storage(self):
        with self.assertRaisesRegex(ValueError, "RGB byte count"):
            encode_rgb_frames(FFMPEG, b"incomplete", width=32, height=32,
                              frame_count=9, fps=8, output=BASE / "unused.mp4")

    @unittest.skipUnless(FFMPEG.is_file(), "Set MITOM_TEST_FFMPEG to a local ffmpeg executable")
    def test_actual_H264_export_dimensions_duration_frame_count_and_decode(self):
        (BASE / ".test-tmp").mkdir(exist_ok=True)
        evidence = {"synthetic_frames_only": True, "AI_model_run": False, "cases": []}
        with tempfile.TemporaryDirectory(dir=BASE / ".test-tmp") as folder:
            for width, height, frame_count, fps in ((128, 80, 49, 8), (80, 128, 9, 12)):
                output = Path(folder) / f"{width}-{height}.mp4"
                # Each frame has its own color, proving a real RGB frame sequence
                # is passed through the encoder rather than repeating one still.
                frames = b"".join(bytes((number * 5 % 256, number * 11 % 256, 100)) * (width * height)
                                   for number in range(frame_count))
                metadata = encode_rgb_frames(FFMPEG, frames, width=width, height=height,
                                             frame_count=frame_count, fps=fps, output=output)
                command = [str(FFMPEG), "-hide_banner", "-i", str(output), "-map", "0:v:0",
                           "-vf", "showinfo,scale=8:8", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
                decoded = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                self.assertEqual(decoded.returncode, 0)
                text = decoded.stderr.decode("utf-8", "replace")
                export = metadata["export_resolution"]
                self.assertIn(f"s:{export['width']}x{export['height']}", text)
                self.assertRegex(text, r"Video: h264[^\r\n]+yuv420p")
                self.assertNotRegex(text, r"Stream #[^\r\n]+Audio:")
                self.assertIn(f"frame_rate: {fps}/1", text)
                timestamps = re.findall(r"\bn:\s*(\d+)\s+pts:\s*\d+\s+pts_time:([0-9.]+)", text)
                self.assertEqual(len(timestamps), frame_count)
                self.assertEqual(int(timestamps[-1][0]), frame_count - 1)
                self.assertAlmostEqual(float(timestamps[-1][1]) + 1 / fps, frame_count / fps, places=4)
                self.assertEqual(len(decoded.stdout), 8 * 8 * 3 * frame_count)
                first = decoded.stdout[:192]
                last = decoded.stdout[-192:]
                self.assertNotEqual(first, last)
                evidence["cases"].append({**metadata, "decoded_frames": len(timestamps),
                    "decoded_last_pts_seconds": float(timestamps[-1][1]),
                    "first_and_last_frames_differ": True, "decode_succeeded": True})
        (BASE / "encode-proof.json").write_text(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
