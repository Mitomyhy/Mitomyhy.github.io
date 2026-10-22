# Sky Star: original panel renderer assets

This directory contains the exact D3 3.5.17 / D3-Celestial 0.7.35 runtime and
versionless geographic-projection binary from the installed Photoshop panel,
plus its nine original astronomy catalogs. No vendor code or catalog was edited.

See [SOURCES.md](./SOURCES.md) for original source paths, digests, upstream
attribution, rendering scope and time-input semantics.

## Integrity and notices

- [manifest.json](./manifest.json) pins all 12 runtime resources by SHA-256 and
  byte length. It is checked before rendering; scripts also use browser SRI.
- [D3_LICENSE.txt](./D3_LICENSE.txt): Michael Bostock's complete D3 BSD notice.
- [D3_GEO_PROJECTION_LICENSE.txt](./D3_GEO_PROJECTION_LICENSE.txt): complete
  geographic-projection BSD notice from its primary repository.
- [D3_CELESTIAL_LICENSE.txt](./D3_CELESTIAL_LICENSE.txt): original installed
  D3-Celestial BSD-3-Clause notice, preserved byte-for-byte.

Keep these notices and attribution with redistributed assets. Upstream authors
do not endorse MITOM; MITOM's browser adapter is separate application code.

## Browser adapter

`../../sky-panel-renderer.js` loads this bundle in an isolated same-origin frame.
It reproduces the panel's native renderer configuration and verifies the
requested observer and UTC instant. It does not call Photoshop, upload inputs,
or fall back to an approximate handwritten star renderer. Optional grid/cardinal
overlays differ from the panel and are disabled by default.

The 2400-pixel configuration can include the panel's authored background stroke
in the actual captured dimensions. Other export sizes are resampled. Native
rendering remains subject to browser/platform antialiasing; identical PNG bytes
across different platforms are not promised.
