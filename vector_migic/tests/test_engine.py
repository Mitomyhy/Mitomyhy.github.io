from pathlib import Path
import tempfile
import re
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from PIL import Image, ImageDraw

from vector_migic import engine


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "icon.png"
        image = Image.new("RGBA", (160, 120), (11, 55, 99, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 15, 135, 105), fill=(220, 35, 50, 255))
        draw.ellipse((40, 30, 110, 95), fill=(20, 90, 230, 255))
        draw.rectangle((62, 50, 84, 73), fill=(100, 10, 10, 0))
        image.save(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def trace(self, **options):
        output = self.root / "result.svg"
        result = engine.vectorize(self.source, output, {"speckle": 0, **options})
        return output, result

    def test_transparent_color_paths_and_real_hole(self):
        output, result = self.trace()
        text = output.read_text("utf-8")
        root = ET.fromstring(text)
        paths = list(root.iter("{" + engine.SVG_NS + "}path"))
        self.assertGreaterEqual(len(paths), 2)
        self.assertLessEqual(result["color_count"], 16)
        self.assertNotIn("<image", text)
        self.assertNotIn("data:", text)
        self.assertEqual(root.attrib["viewBox"], "0 0 160 120")
        self.assertTrue(any(len(re.findall('[Mm]',path.attrib['d']))>1 for path in paths), "hole must be a compound path")
        self.assertTrue(all(path.get("transform") != "translate(0,0)" for path in paths), "transparent canvas must not be filled")

    def test_bw_foreground_does_not_fill_transparent_canvas(self):
        output, result = self.trace(color_mode="bw", bw_threshold=200)
        paths = list(ET.fromstring(output.read_bytes()).iter("{" + engine.SVG_NS + "}path"))
        self.assertEqual(result["color_count"], 1)
        self.assertTrue(all(path.get("fill") == "#000000" for path in paths))
        self.assertTrue(all(path.get("transform") != "translate(0,0)" for path in paths))

    def test_bw_opaque_option_keeps_white_vector_background(self):
        output, result = self.trace(color_mode="bw", bw_threshold=200, transparent=False)
        paths = list(ET.fromstring(output.read_bytes()).iter("{" + engine.SVG_NS + "}path"))
        self.assertEqual(result["color_count"], 2)
        self.assertIn("#FFFFFF", {path.get("fill") for path in paths})

    def test_svg_pdf_eps_exports_are_vectors(self):
        svg, _ = self.trace()
        pdf = self.root / "result.pdf"
        eps = self.root / "result.eps"
        engine.export_vector(svg, pdf)
        engine.export_vector(svg, eps, "eps")
        pdf_bytes = pdf.read_bytes()
        eps_bytes = eps.read_bytes()
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertNotIn(b"/Subtype /Image", pdf_bytes)
        self.assertIn(b"EPSF", eps_bytes[:80])
        self.assertIn(b"curveto", eps_bytes)
        self.assertNotIn(b"colorimage", eps_bytes)

    def test_direct_pdf_and_eps(self):
        for fmt in ("pdf", "eps"):
            output = self.root / ("direct." + fmt)
            result = engine.vectorize(self.source, output, {"speckle": 0})
            self.assertEqual(result["output_format"], fmt)
            self.assertGreater(result["path_count"], 0)

    def test_exif_orientation_preview_and_resolution(self):
        source = self.root / "rotated.jpg"
        image = Image.new("RGB", (100, 60), "red")
        exif = image.getexif()
        exif[274] = 6
        image.save(source, exif=exif)
        info = engine.inspect_image(source)
        self.assertEqual((info["width"], info["height"]), (60, 100))
        preview = self.root / "preview.png"
        engine.create_preview(source, preview, 64)
        with Image.open(preview) as rendered:
            self.assertEqual(rendered.size, (38, 64))
        output = self.root / "scaled.svg"
        result = engine.vectorize(source, output, {"max_dimension": 64})
        root = ET.fromstring(output.read_bytes())
        self.assertEqual(root.get("width"), "60")
        self.assertEqual(root.get("viewBox"), "0 0 38 64")
        self.assertEqual(result["trace_height"], 64)

    def test_supported_raster_formats(self):
        for fmt, extension in (("JPEG", "jpg"), ("WEBP", "webp"), ("BMP", "bmp"), ("TIFF", "tiff")):
            source = self.root / ("input." + extension)
            with Image.open(self.source) as image:
                image.convert("RGB").save(source, format=fmt)
            result = engine.vectorize(source, self.root / (extension + ".svg"))
            self.assertEqual(result["source_format"], fmt)

    def test_multipage_tiff_first_page_explicit(self):
        source = self.root / "pages.tiff"
        Image.new("RGB", (70, 65), "blue").save(source, save_all=True, append_images=[Image.new("RGB", (60, 80), "green")])
        info = engine.inspect_image(source)
        self.assertEqual((info["width"], info["height"]), (70, 65))
        self.assertTrue(any("first" in warning for warning in info["warnings"]))

    def test_partial_alpha_and_progress_are_explicit(self):
        source = self.root / "partial.png"
        Image.new("RGBA", (64, 64), (30, 40, 50, 180)).save(source)
        updates = []
        result = engine.vectorize(source, self.root / "partial.svg", progress=updates.append)
        self.assertTrue(any("Partial transparency" in w for w in result["warnings"]))
        self.assertEqual(updates[-1]["progress"], 100)
        self.assertEqual(sorted(u["progress"] for u in updates), [u["progress"] for u in updates])

    def test_limits_and_unsupported_input(self):
        with mock.patch.object(engine, "MAX_INPUT_PIXELS", 100):
            with self.assertRaisesRegex(engine.VectorizationError, "40 megapixel"):
                engine.inspect_image(self.source)
        gif = self.root / "unsupported.gif"
        Image.new("RGB", (64, 64), "red").save(gif)
        with self.assertRaisesRegex(engine.VectorizationError, "PNG, JPG"):
            engine.inspect_image(gif)
        invalid = self.root / "bad.png"
        invalid.write_bytes(b"not an image")
        with self.assertRaises(engine.VectorizationError):
            engine.inspect_image(invalid)

    def test_options_and_failed_trace_do_not_overwrite(self):
        for options in ({"colors": 1}, {"smoothing": float("nan")}, {"colors": True},
                        {"max_dimension": 5000}, {"x": 1}, {"color_mode": "bad"}):
            with self.assertRaises(engine.VectorizationError):
                engine.normalize_options(options)
        white = self.root / "white.png"
        Image.new("RGB", (64, 64), "white").save(white)
        target = self.root / "existing.svg"
        target.write_bytes(b"keep existing output")
        with self.assertRaisesRegex(engine.VectorizationError, "No visible foreground"):
            engine.vectorize(white, target, {"color_mode": "bw"})
        self.assertEqual(target.read_bytes(), b"keep existing output")
        with self.assertRaises(engine.VectorizationError):
            engine.export_vector(target, self.root / "bad.png")

    def test_export_rejects_external_and_raster_svg(self):
        source = self.root / "untrusted.svg"
        for body in ('<image href="file:///private.png"/>', '<script>alert(1)</script>',
                     '<path d="M0 0 L1 1" fill="url(http://example.com/x)"/>'):
            source.write_text('<svg xmlns="http://www.w3.org/2000/svg">' + body + '</svg>')
            with self.assertRaises(engine.VectorizationError):
                engine.export_vector(source, self.root / "unsafe.pdf")


if __name__ == "__main__":
    unittest.main()
