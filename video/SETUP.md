# MITOM Photo Video — local CPU mode

Open [MITOM Video](https://mitomyhy.github.io/#video), choose a product photo, select motion, aspect ratio and a 6–15 second duration, then export the clip. Processing and encoding happen in your browser on this machine. Product photos are not uploaded. There is no AI API, account, credit balance or model download.

This tool animates your existing photo. It does not invent unseen sides of the product, generate a new background, or create new scenes with AI. The optional motion brief selects a supported zoom/pan preset; use the Motion selector for exact control.

Use a current Chrome or Edge browser for MP4 export. MP4 support is checked on your device. If MP4 is unavailable, the page clearly offers WebM instead; a WebM is never renamed to MP4. Etsy's supported listing formats include MP4, so use an MP4-capable browser for Etsy uploads.

The default is an 8-second silent clip at 720p / 24 fps. Export takes approximately the clip's duration. Keep the tab open while it records. Low-powered CPUs can handle photo motion much more easily than a generative video model; actual performance still depends on the browser, image size and other apps running.

## Use without the website

Download [MITOM-Photo-Video-Local.html](./downloads/MITOM-Photo-Video-Local.html) and copy it to each computer. Open that file in Chrome or Edge. All scripts, styles and UI fonts are included in one HTML file. No server or installation is required, and it works without an internet connection.

## Sources

- [Etsy listing-video requirements](https://help.etsy.com/hc/en-in/articles/360053206073-How-to-Add-Listing-Videos): 3–15 seconds, maximum 100 MB, minimum 500 px, supported formats including MP4. Audio is removed by Etsy.
- [Chrome 126 release notes](https://developer.chrome.com/release-notes/126): MP4 support for MediaRecorder.
- [MediaRecorder capability check](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/isTypeSupported_static): codec support and available resources can differ between browsers and machines.
