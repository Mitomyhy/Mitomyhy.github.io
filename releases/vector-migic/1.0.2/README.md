# Vector Migic 1.0.2

Independent local raster-to-vector application by MITOM for Windows x64.

This update improves local ink assignment at blended edges, orders opaque
regions using their boundary relationships, and distinguishes hard aliased
scans from blended artwork. The precision logo pipeline uses the unmodified
Potrace 1.16 CLI. Default settings preserve small holes and thin regions; the
comparison preview uses aligned source coordinates. Other input types and
path styles continue to use VTracer.

These are independent algorithms. The application does not contain Vector
Magic's proprietary engine and does not promise identical results.

Install or update Vector Migic from MITOM Launcher 2026.10.24.5, or extract the
portable ZIP and run VectorMigic.exe. Existing 1.0.0 installations are recognized
as updates. The compact launcher needs the initial app download; conversion and
SVG/PDF/EPS export run locally and work offline afterward.

English is the default language; Vietnamese is available in settings. No API
credits or GPU are required. For logo conversion begin with Logo & lettering
and default settings. Large small-region values can remove holes and lettering.

The complete application source, third-party notices and corresponding Potrace
source are supplied in the source ZIP and inside the app's _internal/notices.
Potrace is distributed unmodified with its GPL-2.0-or-later license. The original
copyrights and the remaining dependency licenses are included.

User artwork and reference comparison files are not included in this release.
