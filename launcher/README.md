# MITOM Launcher

Launcher **2026.10.24.3** includes **Vector Migic** as a separate, local application.
Its grid card and table row share independent install state, search, filters
and counts. The Photoshop Panel's install/update/repair/rollback and account
flows remain unchanged. English is the default; Settings offers Tiếng Việt and
stores only the language preference in `launcher-preferences.json`.

Vector Migic uses a standalone Windows executable, so another computer does
not need Python, Ghidra, a GPU, or an API account. `vector-release.json` is a
trusted build input containing the version, entry executable, runtime-manifest
SHA-256 and ZIP SHA-256/size. Its ZIP contains `vector-app.json` and every
executable/dependency listed with size and SHA-256. The launcher rejects changed
archives, missing/changed dependencies, extra members and unsafe archive paths.
It stages and verifies the entire runtime before replacing its own versioned
folder under `Apps/VectorMigic/<version>` in the launcher data directory. It
verifies installed bytes again before opening the app. This never edits the
Photoshop extension or any login/session files.

For offline distribution, include `apps/VectorMigic-1.0.0.zip` in the packaged
launcher and set `archive.bundled` to that relative path. The exact same pinned
ZIP can be downloaded from the owner release URL if the bundle is absent.
After installation the app opens locally without a network dependency.

The earlier launcher 2026.10.22.1 repaired the TLS runtime packaged in the public
2026.10.21.4 executable. That runtime raised `FileNotFoundError: [Errno 2] No
such file or directory` while creating `SSLContext`, before either update
discovery or installation could contact the release feed. The rebuilt launcher
passes the packaged HTTPS smoke check with certificate and hostname verification
enabled. The previous external-bundle inventory repair remains included.

The launcher keeps SVN-Helvetica Neue Regular and natural character spacing.
It retains the installation staging and interrupted download
repairs and the MITOM desktop catalogue. The existing signed Photoshop Panel core
feed is `https://mitomyhy.github.io/releases/stable.json`, currently publishing
panel 2026.10.21.2. Account data and machine settings are kept by the original
transactional installer. Metadata-only update checks run after startup and every
five idle minutes; they never install software or close applications.

Run from source with Python 3.12:

```powershell
python launcher/main.py
python -m unittest discover -s launcher/tests -v
```

Build on Windows with a Python 3.12 environment containing
`requirements-build.txt`:

```powershell
./launcher/build.ps1 -Python C:/path/to/python.exe
```

The default `-VectorPayload Online` produces the compact launcher, which downloads
the pinned application ZIP when Install is clicked. Use `-VectorPayload Bundled`
for an offline installer containing that ZIP.

The default Python path uses the existing local `prompt-studio/.venv`. Output is
written to `releases/mitom-launcher/2026.10.24.3/` with SHA-256 and packaged runtime,
UI and HTTPS smoke reports. Runtime/UI smoke tests do not use the network.
`--network-smoke` fetches and verifies signed release metadata and checks the
launcher URL using HEAD; it downloads no installer artifact and installs nothing.
`--verify-only --payload-root PATH --base-root SANDBOX` validates a local payload
without changing the installed panel. The build must pass all three packaged
checks and the regression tests before an executable is published.

For a signed release, put an eligible code-signing certificate in the Windows
certificate store and pass `-CertificateThumbprint` and `-TimestampUrl` (or set
`MITOM_SIGN_CERT_SHA1` and `MITOM_SIGN_TIMESTAMP_URL`). Add `-RequireSigned` to fail
when a signing certificate is not configured. A new unsigned executable can
still produce a SmartScreen warning.

After a successful install, the launcher retains its versioned executable,
creates the MITOM Start menu shortcut, and registers `uoy://launch` for the
current user. Website links only open the catalogue; they cannot start an
installation. Install, repair, rollback, Photoshop, Companion and Chrome/Codex
actions remain available in the desktop UI.

The original launcher Python sources were not present in the owned website
repository. See [RECOVERY.md](RECOVERY.md) for the recovered backend provenance,
hashes, limitations and replacement boundaries.
