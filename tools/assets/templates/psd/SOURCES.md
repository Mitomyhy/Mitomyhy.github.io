# Original MITOM Panel 3D templates

These are the three existing Photoshop Panel template files requested for the
browser workflow. The same original PSD hashes were already listed in the
public Panel 2026.10.21.2 payload manifest. They are not customer job PSDs.
No new ownership or license grant is asserted by this transport package.

The `.psd.gz` files are lossless gzip transport files: level 9, timestamp 0,
OS header 255. `manifest.json` records the original and compressed byte lengths
and SHA-256 values. Downloading a template verifies both hashes and returns the
unchanged original `.psd` filename and bytes, not a rendered approximation.

| Original file | Parent canvas | IN / DEPTH child canvas |
| --- | --- | --- |
| Template acrylic.psd | 3543 × 1181 | 3000 × 3001 |
| Circleceramic-Template.psd | 1866 × 933 | 2799 × 2799 |
| Heartceramic-Template.psd | 1866 × 856 | 2799 × 2568 |

These parent PSDs contain production map layouts, not 3D mesh documents.
Browser generation writes named RGB8 raster content into the embedded IN,
DEPTH and (Acrylic) CUT roles. Original child layers remain editable but hidden;
parent guides/instructions remain stored but hidden. Existing unsupported
geometry is rejected rather than silently re-rendered. The browser's separate
16-bit depth PNG remains the higher-precision scalar export.

For Ceramic, the hash-verified original body image remains a separate visible
base under IN artwork; its alpha and hanging-hole cutouts are retained. DEPTH
uses a separate black copy below generated relief, matching the panel's native
black-substrate step. It does not invent a generic circle or heart.

Source inspection and lossless transport verification: 2026-10-23.
