# MITOM web

Static, dependency-free website for GitHub Pages. Serve this folder with any local HTTP server to preview. All asset paths are relative, so the same folder works at a Pages root or a project subpath.

## Public configuration

Edit `site-config.js` to set the repository, release page, launcher asset URL, and the existing launcher's registered protocol. Empty release/download values intentionally show an honest preparation dialog instead of a broken download.

Add additional real apps to `MITOM_CONFIG.apps`. Each entry supports `id`, `name`, `category`, `platform`, `icon`, `status`, `statusLabel`, `summary`, `detail`, `version`, and `githubUrl`. Only `status: 'available'` offers the launcher installation action; other statuses show app details. No unreleased apps are represented as available.

## Assets

The supplied PNG logo is preserved in `assets/mitom-logo.png`. Its transparency preserves the original red symbol; the header frames the symbol next to the MITOM wordmark without modifying the PNG. The supplied `.ico` is used as the favicon and Photoshop Panel avatar. Typography uses the supplied Akzidenz Grotesk Bold and OCR A Extended TTF files, with system font fallbacks for missing glyphs.

## Deployment

Publish the contents of this folder using GitHub Pages in `Mitomyhy/Mitomyhy.github.io`. The configured canonical address is `https://mitomyhy.github.io/`; the website name is Mitomyhy and its visual wordmark is MITOM. All paths are relative so this project prefix works without a build step.

No third-party scripts, analytics, package installation, or build step are needed. Theme preference is kept in browser local storage. Search, grid/table views, app details, downloads, launch dialog, copy/share, mobile navigation, and FAQ work in the browser.

## Readability and language

OCR labels are at least 12 px with a synthesized bold weight; desktop controls
use 13 px and header icons use a 24 px outline. English is the default language.
The header Settings button lets visitors choose English or Tiếng Việt, and saves
that explicit preference in local storage as `mitom-language`. Theme and language
preferences are independent.

`i18n.js` contains the interface dictionaries. Keep it before `app.js` in the page.
The authoring source retains its assets directory. For the existing GitHub
browser-upload deployment, run `scripts/mitom-pages-flat.py OUTPUT_DIRECTORY` and
upload the generated files to the public repository root on `main`. GitHub Pages
serves that root; no additional build is required.
