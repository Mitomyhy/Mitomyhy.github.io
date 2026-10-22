# Exact Photoshop-panel Sky Star renderer

The three JavaScript libraries and all nine catalog files are copied without
modification from the installed panel. Their bytes were independently verified
on 2026-10-22 against both source locations:

- Installed: `%APPDATA%/Adobe/CEP/extensions/com.uoy.orderpanel/vendor/skymoon/`
- Project copy: `com.uoy.orderpanel/vendor/skymoon/`

The `data/` subfolder is retained. `manifest.json` records all 12 relative paths,
SHA-256 digests and byte lengths; a filename or library version alone is not used
as proof that an asset matches the installed panel.

| Original library | Version identified in bundled code | SHA-256 |
| --- | --- | --- |
| `d3.min.js` | 3.5.17 | `54add204da301145a792a3a60cff7677e827b4c189b2668311d7428581edaeec` |
| `d3.geo.projection.min.js` | No version string; preserved by exact digest | `2cbb20601deb5cdac17e5cb97ce747949bbaa44641a1b4c3bb5e9bf31e15dda1` |
| `celestial.min.js` | 0.7.35 | `c9bddddde8a4774506adf903d2514cf0bde5a57446c164025ad5b1fabf419119` |

- D3 v3: https://github.com/d3/d3/tree/v3.5.17
- D3 geographic projections: https://github.com/d3/d3-geo-projection
- D3-Celestial: https://github.com/ofrohn/d3-celestial
- D3 license: https://raw.githubusercontent.com/d3/d3/v3.5.17/LICENSE
- Geographic projection license: https://raw.githubusercontent.com/d3/d3-geo-projection/v0.2.16/LICENSE

The installed D3-Celestial BSD-3-Clause notice is retained byte-for-byte in
`D3_CELESTIAL_LICENSE.txt`. Complete Michael Bostock BSD notices from the primary
D3 and geographic-projection repositories accompany it in `D3_LICENSE.txt` and
`D3_GEO_PROJECTION_LICENSE.txt`. The projection license source is not a claim
that the versionless installed projection binary was newly identified or
replaced by that release. All three original runtime files are unchanged.

## Catalog attribution

The installed D3-Celestial catalog bytes are retained, not replaced by current
upstream data. The [upstream catalog credits](https://github.com/ofrohn/d3-celestial/blob/master/readme.md#files)
identify XHIP (Anderson and Francis) for stellar data; catalog cross-indices and
Stellarium for names; IAU constellation definitions, with Olaf Frohn's placement
and line changes; Davenhall and Leggett for boundaries; José R. Vieira for Milky
Way outlines; and JPL's approximate planetary elements. D3-Celestial also credits
Michael Bostock and Jason Davies for D3 and its geographic/zoom components.

Those upstream references document attribution; they do not assert that a
newer upstream catalog is byte-identical to this installed-panel snapshot.
`dsos.bright.json`, naming data and disabled visual layers remain in the package
because this renderer requests all nine original catalogs. Their authorship
and included notices are retained. This adapter does not claim exclusive
ownership of the library, astronomy data, or their generated scientific content.

Rendering uses the panel's stereographic/equatorial/follow-zenith configuration,
star magnitude/radius settings, constellation styling, planet disks and hidden
labels/Milky Way. The native configuration requests 2400 pixels with rendering
device-pixel ratio 1. Celestial's authored background stroke may add 1–2 raster
pixels to that canvas, exactly as in the panel; requested output sizes are
resampled from the captured native canvas.
Platform antialiasing can differ, so the adapter does not promise cross-platform
byte-identical PNG output. It does not add precession absent from the panel.

City-local wall time/DST is resolved by the parent UI into an exact UTC instant.
The panel's own clock field is UTC: compare matching instants, not identical
wall-clock text. The frame verifies the native observer and sidereal zenith
before returning a result. No location or date is sent to a remote renderer.

Grid and cardinal labels are optional browser controls and disabled by default.
No approximate renderer is used if this vendor bundle fails to load.
