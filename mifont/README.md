# MiFont

MiFont 1.0.1 is a simple Windows image-to-font app. Upload one or more images,
enter the font family name, optionally adjust the ink threshold, and press
Generate. One automatic job detects and labels letters, traces their shapes,
completes the character set, and downloads one ZIP containing both TrueType
(`glyf`) and OpenType (`CFF`) fonts. No manual mapping or separate research,
completion and export buttons are needed.

Generation uses local Windows OCR and image tracing. It can automatically
compare verified OFL-licensed public Google Fonts references on the computer
and fit a suitable source for missing characters. If online lookup is
unavailable, it uses verified cached sources or its own CPU shape proposals.
Screenshots, glyph shapes and projects are never uploaded. The default set is
the 95 printable ASCII characters U+0020–U+007E: Latin letters, digits and common
punctuation. OCR labels and unseen characters can be imperfect; generated
characters are estimates, not guaranteed recovery of an unknown original font.

The download includes both font files, coverage and file hashes in
`manifest.json`, and a README. Any selected OFL source's full license and
provenance travel in `licenses/` and inside the font metadata. Reference-derived
fonts retain your new family name, with a safe rename if it conflicts with a
source's Reserved Font Name. Fonts are downloaded for you to inspect and
install; MiFont never installs them into Windows automatically.

Projects live under `%LOCALAPPDATA%/MITOM/MiFont/Projects`; verified open-font
reference files use the adjacent `ReferenceCache`. Each desktop launch uses an
authenticated IPv4 loopback session and a random port. No cloud account, API
credit, GPU or model download is needed. Tracing, completion and export run on
CPU; online catalogue lookup is only used for public reference sources.

## Build from complete source

Use CPython 3.12 x64 on Windows:

```powershell
py -3.12 -m venv .work/mifont-env
.work/mifont-env/Scripts/python.exe -m pip install -r mifont/requirements.txt -r mifont/requirements-build.txt
./mifont/build.ps1
```

The build runs source tests, creates an onedir portable application, exercises
the actual frozen HTTP worker, then emits a hash-pinned flat-root ZIP and
`mifont-release.json` for the launcher. App 1.0.1 has executable `MiFont.exe` and
manifest `mifont-app.json`. It does not depend on an installed Photoshop panel
or Vector Migic. The launcher publishes compact online installers while the
standalone ZIP runs offline after extraction.

The separate unmodified Potrace 1.16 CLI, GPL license, copyright notices and
complete matching upstream source archive are included under `vendor/potrace`.
FontTools (MIT), Pillow (HPND and bundled library notices), Python and
PyInstaller's bootloader exception notices are collected in the application.
The unmodified Inter UI font is bundled with its complete OFL-1.1 license and
verified official source URLs/hashes under `assets/fonts` and `notices/Inter`.
The complete MiFont source ZIP and rebuilding instructions are also included.
No customer specimen, project, reference-cache download, reverse-engineering
tool or proprietary application binary belongs in a distributable.
