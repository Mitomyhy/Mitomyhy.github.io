# Sky Star and Moon Phase sources

## Current production flow — 2026-10-22 R6

Sky location queries and artwork stay on-device. Moon selected civil dates are
sent directly to MoonPhases.co.uk to match the panel's date-only source protocol.

- Sky now uses the exact installed-panel D3-Celestial, D3 v3 and projection
  libraries plus all nine native catalogs in `../../vendor/sky-panel/`.
  Their independent manifest pins every library/catalog byte. Native observer
  and UTC zenith are verified before canvas capture. No handwritten projection
  fallback is used by the production workspace.
- Location paste resolves with the bundled GeoNames city ID and IANA timezone
  supplement. The entered clock means local time at the selected city; DST is
  applied before calling Celestial. Manual latitude, longitude and UTC-offset
  controls are removed. Scroll/pan changes preview CSS, never exported pixels.
- Moon accepts numeric MM/DD/YYYY only, with no automatic day/month swapping.
  Production metadata uses the exact panel endpoint with fixed London observer,
  `len=1`, `tz=0`; returned `date_serial` must match the requested date exactly.
- Illumination, phase name and clockwise disk tilt come from the provider. The
  API has no hour parameter: do not call its daily result an exact noon instant.
- Downloadable Moon PNGs remain a NASA reconstruction, not the provider photo.
  The whole disk/texture rotates by provider tilt. Provider PNG bytes are not
  proxied or redistributed; missing image CORS headers still prevent their
  cross-origin canvas export in a browser-only app.
- Official reference: https://moonphases.co.uk/js/widget.js chooses `phase_img`
  and rotates with `tilt`. No different date or computed metadata is silently
  substituted when the source API fails.

## Historical R1/R2 implementation notes

The notes below document the former local renderer. The R6 production behavior
above supersedes conflicting descriptions; SunCalc remains available as a
standalone library/test path, not a fallback in the production Moon workflow.

## Moon calculation

`suncalc-2.1.1.js` is the unmodified ESM `index.js` from the pinned SunCalc v2.1.1 tag:

- https://github.com/mourner/suncalc/tree/v2.1.1
- https://raw.githubusercontent.com/mourner/suncalc/v2.1.1/index.js
- Documentation: https://github.com/mourner/suncalc/blob/v2.1.1/README.md
- Copyright Volodymyr Agafonkin; BSD-2-Clause notice retained in `SUNCALC-LICENSE.txt`.

SunCalc uses Meeus astronomical models. Its v2 angles are degrees, not the radians
used by v1. Inputs accept pasted panel-style MM/DD/YYYY (default), an explicit
DD/MM/YYYY selector, ISO dates and English month names. City-selected sky times
use an IANA zone and date-specific DST via the browser's Intl database. Advanced
numeric UTC offsets are **hours east of UTC**. The workstation timezone is never inferred. Moon
illumination is geocentric; position metadata uses the requested observer.

The rendered moon has a computed illuminated hemisphere over a fixed north-up
near-side surface texture. It is an illustrative product-design rendering, not
a telescope prediction: lunar libration, lunar-axis position angle, surface
relief shadows and eclipses are not modeled. Numerical illumination and phase
are calculated for the exact UTC instant, not selected from 16 fixed images.
Principal phase labels apply within 0.01 lunar cycle of new/quarter/full phase;
neighbouring dates retain crescent/gibbous labels (98% illumination alone does
not mean the exact Full Moon phase). These descriptive labels are not a calendar
prediction of an exact principal-phase event.

This is intentionally a different web engine from the legacy panel's
moonphases.co.uk/London observer API. The web default is 12:00 UTC with the same
London observer coordinates. Warm, natural and grayscale are explicit appearance
presets. Orientation may be celestial-north-up, observer/zenith-relative, or the
conventional waxing-right phase display. PNGs are available individually without
labels and as a combined grid. It is not advertised as pixel-identical.

On 2026-10-22, the panel provider's public exact-date metadata service permitted
cross-origin reads, but its large PNG image response did not send an
Access-Control-Allow-Origin header. Those images cannot be read back from a
browser canvas for PNG export. We do not bypass that browser protection, copy
the provider's 16-image library into this site, or treat public widget embedding
permission as a license for redistributing its artwork.

## Moon texture

Credit: **NASA's Scientific Visualization Studio / LROC**.

- NASA CGI Moon Kit: https://svs.gsfc.nasa.gov/4720/
- File, downloaded unchanged 2026-10-22:
  https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/lroc_color_2k.jpg
- NASA media policy: https://www.nasa.gov/nasa-brand-center/images-and-media/

The NASA surface map is generally not subject to US copyright as NASA source
material. This is not a blanket license for NASA logos, employee likenesses, or
third-party media; none of those are bundled here. No NASA endorsement of MITOM
or its rendered results is implied. NASA describes this map as optimized for
aesthetics rather than scientific measurement.

## Star catalog

`stars.6.json` and `constellations.lines.json` were copied without data changes
from the installed Photoshop panel. They originate in Olaf Frohn's D3-Celestial:

- https://github.com/ofrohn/d3-celestial
- Source/format notes: https://github.com/ofrohn/d3-celestial/blob/master/data/readme.md
- Credits: https://github.com/ofrohn/d3-celestial/blob/master/readme.md#files
- BSD notice retained in `D3_CELESTIAL_LICENSE.txt`.

D3-Celestial credits the XHIP compilation (Anderson and Francis 2012,
VizieR V/137D) for these stars and the IAU constellation source with Frohn's
line modifications. RA/Dec are J2000 coordinates. This browser renderer applies
IAU-1976 precession and Greenwich mean sidereal time before projecting the
geometric upper hemisphere stereographically (the same projection family as the
panel). East is left when looking up. The local panel Sky effect LUT and
four-neighbour unsharp mask are ported; reapply always starts from a retained raw
render. It does not model atmospheric refraction or stellar proper motion.
This independent Canvas renderer is not claimed pixel-identical to D3-Celestial
or a Photoshop action.

## Places

`cities500.json.gz` is the panel's exact GeoNames 2026-09-12 bundle. Its source,
CC BY 4.0 license, changes, and limitations are retained in `GEONAMES-README.txt`.
The lookup factory is unchanged; the adapter adds only a globalThis root and
an ESM export. Exact aliases plus qualifiers are used. Ambiguous names require
the user to select a candidate; population is never used to choose silently.
Coordinates can be entered directly without loading the place catalog.

`city-timezones.json.gz` supplements the unchanged place catalog by exact
GeoNames city ID using the source's IANA timezone column. Source:
https://download.geonames.org/export/dump/cities500.zip (retrieved 2026-10-22),
same CC BY 4.0 GeoNames attribution. The supplement has 236,632 city IDs and
394 IANA zone names. Missing IDs are not guessed by country or longitude: use
the advanced IANA zone field. The browser Intl database supplies the offset for
the chosen civil date, including DST. A nonexistent or repeated transition hour
is rejected and can be resolved with the explicit advanced UTC offset override.
Timezone law/data can change; use a current browser and verify legally unusual
historical dates if exact historical civil time is critical.
