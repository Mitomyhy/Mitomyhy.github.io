# MITOM Launcher

## Connected online updates (2026.10.25.7)

The launcher checks the authenticated Panel, Launcher and app channels when it
opens and every five minutes while idle. It selects the newest verified Launcher
from both channels and upgrades itself through a health-checked handoff. MiFont
and Vector Migic use `releases/apps-stable.json`; a new app version no longer
requires rebuilding Launcher. Cached metadata is signature-checked on startup.
Already-installed apps update automatically. Panel updates wait for Photoshop
and Order Studio to close; the launcher never closes those applications.

The newest advertised Launcher is not a mandatory Panel minimum. Only an
explicit authenticated `min_launcher_version` can impose that requirement.
Compatible installation can continue if a Launcher upgrade is unavailable.

Panel downloads reuse verified cache objects across versions, fetch at most
three artifacts concurrently and resume large interrupted transfers. Process
checks are bounded to every 250 ms during installation, with mandatory checks
at transaction entry and commit. Disk planning credits only hash-verified cache
and budgets full extraction plus the real changed files/backups. Initial setup
still downloads the required AI models (about 1.4 GB) and runtimes; its duration
depends on the connection, storage and Windows scanning.

Use `publishing/prepare_online_release.py` to stage one synchronized release:
the website cards, download index, signed Launcher channel and signed app
catalogue. Publish the resulting manifest together, after tests and signatures
pass. This prevents the website and installed Launcher from advertising
different releases. The original signed Panel feed and both trusted keys remain
separate. Private projects, credentials, specimen images and local test output
must never be added to that manifest.

Launcher **2026.10.24.9** includes **Vector Migic 1.0.4** and **MiFont 1.0.0** as separate, local applications.
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
five idle minutes. The launcher checks the independent signed
`releases/launcher-stable.json` channel as well as the original panel feed.
Manual **Check for updates** checks both channels and reports partial failures
rather than claiming everything is current when one check failed.

**Update & restart** downloads and verifies the advertised launcher, keeps it in
a versioned local folder, and starts it withdrawn. The current launcher stays
open until the new process has built its interface and acknowledged readiness.
It then hands over the window, refreshes the Start menu/protocol registration,
and closes only its own UI. Failure keeps the old UI open for retry. Previous
program versions, open Vector Migic work and Photoshop data are not deleted.

Versions through 2026.10.24.5 only consult the original panel feed, which still
advertises launcher 2026.10.22.1. They need one upgrade to this corrected release
to use the independent launcher channel; subsequent releases update in-app.
The panel feed/key remain unchanged. The new channel exposes only a launcher
artifact even though its signed envelope uses the existing verifier format.

Every launcher publication must update the executable, signed launcher channel,
website catalogue and latest-download index together. Sign the final payload
with `publishing/sign-launcher-channel.ps1`; its private key is Windows DPAPI
protected outside the project under the current user's MITOM/Publishing folder.
Only `launcher-update-public-key.json` is bundled or published. A missing key
fails signing instead of silently generating a new publisher identity.

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
written to `releases/mitom-launcher/2026.10.24.9/` with SHA-256 and packaged runtime,
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
