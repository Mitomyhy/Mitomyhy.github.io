# MiFont local API

All `/api/` requests require `X-MiFont-Token` from the initial URL fragment
`#token=…`. Fetch images, SVG and font assets with that header and create local
blob URLs; tokens never belong in asset query strings. The server accepts only
its own `127.0.0.1:<port>` Host/Origin. No artwork leaves the machine.

All JSON requests use `Content-Type: application/json`. Errors are
`{"error":"Readable explanation"}` with HTTP400/403/409. Successful edits
return the updated project. Jobs return HTTP202 and are polled until
`done`, `error` or `cancelled`.

## Project shape

```json
{
  "schema":1,"id":"32 lowercase hex","name":"My font","family":"My Font",
  "style":"Regular","charset":"ASCII32 through126 by default","revision":1,
  "units_per_em":1000,"created_at":"UTC ISO timestamp","modified_at":"UTC ISO timestamp",
  "images":[{"id":"…","name":"letters.png","width":1200,"height":600,
    "preview_url":"/api/image/PROJECT/IMAGE/preview",
    "source_url":"/api/image/PROJECT/IMAGE/source",
    "candidates":[{"id":"stable region id","box":[10,20,80,100],
      "line_id":0,"baseline_y":120,"reference_height":100}]}],
  "glyphs":[{"id":"…","char":"A","origin":"observed","image_id":"…",
    "box":[10,20,80,100],"advance_width":700,"lsb":20,"bbox":[0,0,660,700],
    "svg_url":"/api/glyph/PROJECT/GLYPH/svg","crop_url":"/api/glyph/PROJECT/GLYPH/crop"}],
  "coverage":{"observed_chars":"A","generated_chars":"","missing_chars":"…","unassigned_count":0},
  "builds":[{"id":"…","family":"My Font","style":"Regular","metrics":{}}]
}
```

Boxes use original oriented image pixels `[x,y,width,height]`. Glyph recordings
and metrics use1000UPM, Y-up. An unassigned glyph has `char:""`. Assigning an
already-used character unassigns the earlier mapping; observed outlines take
priority over generated proposals. Generated entries retain `approximate` and
`provenance`. `crop_url` is null when no source crop exists.

## Routes

| Route | Request | Response |
|---|---|---|
| GET `/api/config` | — | `{version,app_id:"mifont",charset,default_options,completion,max_upload}` |
| GET `/api/projects` | — | `{projects:[{id,name,family,style,revision,modified_at,image_count,glyph_count}]}` |
| POST `/api/project/new` | `{name?,family?,style?,charset?}` | project |
| GET `/api/project/ID` | — | project |
| POST `/api/project/update` | `{project_id,revision?,name?,family?,style?,charset?}` | project |
| POST `/api/upload` | raw image body; `X-Project-Id`; URL-encoded `X-File-Name` | `{project,image}` |
| POST `/api/image/remove` | `{project_id,image_id,revision?}` | project; detaches that image and associated active glyphs, preserves built fonts |
| POST `/api/generate` | `{project_id,metadata?:{family?,style?},options?:{threshold:"auto"\|1..254,foreground?,ocr?,offline?}}` | one cancellable automatic job over every uploaded image; no mapped glyphs required |
| POST `/api/analyze` | `{project_id,image_ids?:[],options?:{}}` | job; analyzes all images when omitted |
| POST `/api/extract` | `{project_id,image_id,regions:[{id?,box,char?,baseline_y?,reference_height?}],options?:{}}` | job; ordered bulk extraction |
| POST `/api/crop` | `{project_id,image_id,box,char?,baseline_y?,reference_height?,options?:{}}` | job; single manual extraction |
| POST `/api/glyph/update` | `{project_id,glyph_id,revision?,char?,advance_width?,lsb?}` | project |
| POST `/api/glyph/remove` | `{project_id,glyph_id,revision?}` | project; detaches mapping, preserves original image |
| POST `/api/complete` | `{project_id,options:{mode:"proposal",allow_approximate:true,...}}` | job; preserves observed glyphs |
| POST `/api/research` | `{project_id,query?,options?:{}}` | cancellable public catalogue lookup and local style comparison; source cards saved in `project.references` |
| POST `/api/build` | `{project_id,metadata?:{family?,style?}}` | job; creates genuine TTF/OTF; reports actual coverage |
| GET `/api/job/ID` | — | job |
| POST `/api/cancel` | `{job_id}` | job |
| POST `/api/export` | `{project_id,format:"ttf"\|"otf",build_id?}` | font download; latest build when ID omitted |
| POST `/api/export` | `{project_id,format:"fonts",build_id?}` | one ZIP with both TTF/OTF, coverage README, manifest and the selected build's full source/license notices |
| POST `/api/font-bundle` | `{project_id,build_id?}` | alias for the `fonts` bundle |
| POST `/api/export` | `{project_id,format:"project"}` | self-contained project ZIP |
| POST `/api/project/import` | raw ZIP body | imported project with a new ID |
| GET `/api/font-preview/PROJECT/BUILD/ttf` | — | authenticated font bytes; OTF also accepted |
| GET `/api/image/PROJECT/IMAGE/preview` | — | oriented thumbnail PNG |
| GET `/api/image/PROJECT/IMAGE/source` | — | canonical oriented PNG |
| GET `/api/glyph/PROJECT/GLYPH/svg` | — | safe outline SVG |
| GET `/api/glyph/PROJECT/GLYPH/crop` | — | source crop PNG |
| GET `/api/ping` | — | `{ok:true}` |
| POST `/api/shutdown` | `{}` | `{ok:true}` |

Upload multiple images by repeating `/api/upload` before Generate. Image uploads
are bounded to32MiB each; project ZIP imports to64MiB. Project changes save
automatically. Edits to a project with an active job return409; other projects
remain available. Optional revision checks reject stale edits with409.

Job shape: `{id,project_id,kind,status,progress,message,message_key,result,project?}`.
`project` appears after a successful mutating job. Build results contain
`build_id`, font metrics and actual observed/generated/missing coverage.

`generate` snapshots all images, family/style and normalized threshold options.
It runs OCR/layout analysis, traces supported observed glyphs, optionally
researches verified public OFL references locally, proposes remaining characters
and creates both real font formats. No screenshot or outline is uploaded.
An offline or failed reference lookup can use clearly marked CPU proposals.
Automatic reference-derived fonts retain full OFL source notices. The requested
family name is preserved unless it conflicts with a source Reserved Font Name;
any required rename is reported in the result. Generated glyphs remain proposals
rather than recovered originals.

The worker's complete result must pass closed-outline, source-crop, license,
1000UPM, actual TTF-glyf/OTF-CFF and ASCII95cmap validation. Only then does one
atomic project revision attach new glyphs and the completed build. Failed or
cancelled generation preserves previous project mappings and downloadable
builds. Old builds remain available after image removal or another generation.
Every automatic build stores immutable `source_image_ids` and normalized
`generation_options` (including its requested `threshold`). On reopening the
project, the UI can restore that threshold and detect images changed since
the latest build instead of presenting an old result as current.

Automatic progress keys are `generate_queued`, `generate_ocr`, `generate_analyze`,
`generate_trace`, `generate_research`, `generate_complete`, `generate_build`,
`generate_save`, `generate_ready`, `generate_cancelled`, `generate_error`.
Each includes an English fallback `message`; the UI can translate keys to VI.

The engine's exact accepted foreground/threshold/text options are exposed as
`default_options`; supported foreground modes are `auto`, `dark`, `light`,
`color` with `ink_color:"#RRGGBB"`. `options.text` may guide ordered analysis;
explicit per-region `char` labels take precedence during extraction.

Research fetches public font catalogue metadata and open-license font candidates,
then compares locally. Generate may perform that research automatically unless
`offline:true` is selected by its caller.
It never uploads screenshots, glyph recordings or project archives. Source
cards retain source URL/license/provenance and do not claim recovered originals.

CPU completion produces **approximate proposals** and is never presented as a
trained model. The one-step Generate action includes this automatic completion;
the internal manual completion route retains its explicit opt-in fields.
Verified OFL reference outlines can be selected automatically with their actual
source and license retained. No large model, cloud API or OS font installation
happens automatically. The user downloads and installs the finished fonts.
