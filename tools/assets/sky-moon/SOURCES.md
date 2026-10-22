# Sky Star and Moon Phase sources

The browser tools do not send dates, place queries, or artwork to an external API.
Assets are fetched only from this site's own `tools/assets/sky-moon/` directory.

## Moon calculation

`suncalc-2.1.1.js` is the unmodified ESM `index.js` from the pinned SunCalc v2.1.1 tag:

- https://github.com/mourner/suncalc/tree/v2.1.1
- https://raw.githubusercontent.com/mourner/suncalc/v2.1.1/index.js
- Documentation: https://github.com/mourner/suncalc/blob/v2.1.1/README.md
- Copyright Volodymyr Agafonkin; BSD-2-Clause notice retained in `SUNCALC-LICENSE.txt`.

SunCalc uses Meeus astronomical models. Its v2 angles are degrees, not the radians
used by v1. Inputs are strict Gregorian dates and an explicit numeric UTC offset
in **hours east of UTC**. The workstation's timezone is never inferred. Moon
illumination is geocentric; position metadata uses the requested observer.

The rendered moon has a computed illuminated hemisphere over a fixed north-up
near-side surface texture. It is an illustrative product-design rendering, not
a telescope prediction: lunar libration, lunar-axis position angle, surface
relief shadows and eclipses are not modeled. Numerical illumination and phase
are calculated for the exact UTC instant, not selected from 16 fixed images.
Phase names are rounded to the nearest of eight conventional names.

This is intentionally a different web engine from the legacy panel's
moonphases.co.uk/London observer API. It is not advertised as pixel-identical.

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
geometric upper hemisphere orthographically. East is left when looking up.
It does not model atmospheric refraction or stellar proper motion. The panel
uses a different stereographic presentation; both use the real bundled catalog.

## Places

`cities500.json.gz` is the panel's exact GeoNames 2026-09-12 bundle. Its source,
CC BY 4.0 license, changes, and limitations are retained in `GEONAMES-README.txt`.
The lookup factory is unchanged; the adapter adds only a globalThis root and
an ESM export. Exact aliases plus qualifiers are used. Ambiguous names require
the user to select a candidate; population is never used to choose silently.
Coordinates can be entered directly without loading the place catalog.
