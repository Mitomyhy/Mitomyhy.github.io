# MiFont

MiFont is a local Windows image-to-font workspace. Upload specimen images,
inspect detected glyph regions, confirm their character labels, edit spacing,
then export editable TrueType (`glyf`) and OpenType (`CFF`) fonts. The default
target repertoire is printable ASCII U+0020–U+007E. Projects save locally and
can be exported as self-contained ZIP files. Fonts are downloaded for the user
to install; MiFont never installs them automatically.

Windows OCR offers editable suggestions, not authoritative transcription.
CPU geometric completion is an explicitly selected approximate proposal.
Online research is a separate requested action: public Google Fonts catalogue
and OFL-licensed candidates are downloaded and compared locally. Screenshots,
glyph recordings and project files are never uploaded. Choosing reference-fit
completion preserves observed glyphs, labels generated glyphs, requires license
acceptance, renames the derivative family and carries the full source license
into font metadata and project notices. This is not neural inference or a claim
to recover an unknown original font.

Projects live under `%LOCALAPPDATA%/MITOM/MiFont/Projects`; verified open-font
reference files use the adjacent `ReferenceCache`. Each desktop launch uses an
authenticated IPv4 loopback session and a random port. No cloud account, API
credit, GPU, system font installation or online service is needed for local
image tracing and font export.

## Build from complete source

Use CPython3.12 x64 on Windows:

```powershell
py -3.12 -m venv .work/mifont-env
.work/mifont-env/Scripts/python.exe -m pip install -r mifont/requirements.txt -r mifont/requirements-build.txt
./mifont/build.ps1
```

The build runs source tests, creates an onedir portable application, exercises
the actual frozen HTTP worker, then emits a hash-pinned flat-root ZIP and
`mifont-release.json` for the launcher. App1.0.0 has executable `MiFont.exe` and
manifest `mifont-app.json`. It does not depend on an installed Photoshop panel
or Vector Migic. The launcher publishes compact online installers while the
standalone ZIP runs offline after extraction.

The separate unmodified Potrace1.16 CLI, GPL license, copyright notices and
complete matching upstream source archive are included under `vendor/potrace`.
FontTools (MIT), Pillow (HPND and bundled library notices), Python and
PyInstaller's bootloader exception notices are collected in the application.
The unmodified Inter UI font is bundled with its complete OFL-1.1 license and
verified official source URLs/hashes under `assets/fonts` and `notices/Inter`.
The complete MiFont source ZIP and rebuilding instructions are also included.
No customer specimen, project, reference-cache download, reverse-engineering
tool or proprietary application binary belongs in a distributable.
