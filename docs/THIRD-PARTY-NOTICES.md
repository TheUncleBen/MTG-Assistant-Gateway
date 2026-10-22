# Third-party notices

The gateway is licensed under PolyForm Noncommercial 1.0.0 (see `LICENSE`).
It builds on the following third-party work, each under its own licence.

## Mystic Forge (research service image)

The research service is **Mystic Forge** by Kautiontape,
<https://github.com/Kautiontape/mystic-forge>, MIT License (Copyright (c) 2026
Kautiontape). `docker/mystic-forge/Dockerfile` builds it from a pinned
upstream commit with the small patches in `docker/mystic-forge/patches/`, and
copies upstream's `LICENSE` file into the image. It runs as a separate
service; none of its code is part of the gateway itself.

## Forge (simulation image)

Deck simulations use **Forge**, <https://github.com/Card-Forge/forge>, under
the GNU General Public License version 3. `docker/forge/Dockerfile` puts
Forge's release archive into the image unmodified, beside the gateway's own
small job service, which runs Forge as a separate program and includes none
of its code. `docker/forge/NOTICE.md`, copied into the image, says where
Forge's licence and source are.

## ntfy (notification server)

The stack runs the upstream **ntfy** image (`binwiederhier/ntfy`),
<https://github.com/binwiederhier/ntfy>, unmodified. Its project is
dual-licensed under the Apache License 2.0 and the GNU General Public
License version 2. It runs as a separate service; none of its code is part
of the gateway.

## Card artwork hash (scan)

`src/mtg_gateway/scan/art.py` and `src/mtg_gateway/scan/static/scan-art.js`
reimplement the artwork hash specification ("algo_version 1") of
**mtg-scanner-art-index** by neotoxicfr, <https://github.com/neotoxicfr/mtg-scanner-art-index>,
released under the MIT License:

> MIT License
>
> Copyright (c) 2026 neotoxicfr
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

The project's published index file (`art_hashes.sqlite`) is not used or
redistributed; the gateway builds its own hashes from Scryfall images.

## Python packages

The gateway image installs these Python packages directly, at the versions in
`constraints.txt`, together with the packages they depend on. Each one's
licence file is in its installed distribution in the image.

| Package | Version | Licence |
| --- | --- | --- |
| `mcp` | 2.3.0 | MIT |
| `httpx` | 0.28.1 | BSD-3-Clause |
| `joserfc` | 1.7.5 | BSD-3-Clause |
| `cryptography` | 50.0.2 | Apache-2.0 or BSD-3-Clause |
| `uvicorn` | 0.54.0 | BSD-3-Clause |
| `pillow` | 12.3.0 | MIT-CMU (more below) |

## Image decoding (scan)

The gateway decodes Scryfall's card images with **Pillow**
(<https://python-imaging.github.io/>), distributed under the MIT-CMU licence
(the "HPND" style licence; see the `LICENSE` file in the Pillow distribution
installed in the image).

## OCR engine (scan)

The `/scan` page serves Tesseract.js (MIT/Apache-2.0), its WebAssembly core and
the Tesseract English language model (Apache-2.0), fetched at image build time
by `scripts/fetch_ocr_assets.py`; their licence files are stored next to the
assets under `src/mtg_gateway/scan/static/vendor/`.

## Android app

The Android app (`android/`, and the APK the gateway image and GitHub
releases carry) includes the **Kotlin standard library** by JetBrains,
distributed under the Apache License 2.0
(<https://github.com/JetBrains/kotlin>), and these AndroidX libraries by Google,
also under the Apache License 2.0
(<https://developer.android.com/jetpack/androidx>), with the libraries they
depend on:

| Library | Version |
| --- | --- |
| `androidx.activity:activity-ktx` | 1.13.0 |
| `androidx.browser:browser` | 1.10.0 |
| `androidx.camera:camera-core`, `camera-camera2`, `camera-lifecycle`, `camera-view` | 1.6.2 |
| `androidx.window:window`, `window-java` | 1.5.1 |

The build also uses, without shipping
them in the app: the Kotlin compiler (Apache-2.0), bundletool (Apache-2.0), an
Android platform `android.jar` (compile-time stubs), and JUnit (EPL-1.0),
Hamcrest (BSD) and org.json 20240303 (public domain) for the unit tests.

## Card data and images

Card data and images come from Scryfall (<https://scryfall.com>) under its API
terms. Magic: The Gathering is © Wizards of the Coast. This project is
unofficial Fan Content permitted under the Fan Content Policy and is not
approved or endorsed by Wizards.

## Connected app marks (`src/mtg_gateway/static/logos/`)

The rows that name a connected app show that app's own mark beside its name,
only to identify the app, and only when the gateway can tell where the app comes
from: its client metadata address is on claude.ai or anthropic.com (or
chatgpt.com or openai.com), or, for a registered app, every address it sends
members back to after sign-in is. Any other app, whatever name it gives itself,
shows the gateway's plain app icon. `claude.svg` is the Claude spark from Anthropic's
media resources (anthropic.com/press-kit, "Claude Spark - Clay.svg");
`openai-black.svg` and `openai-white.svg` are the OpenAI blossom from OpenAI's
logo pack (openai.com/brand, "OAI_OpenAI-Blossom_Black.svg" and
"OAI_OpenAI-Blossom_White.svg"). Claude and the Claude spark are trademarks of
Anthropic PBC; ChatGPT, Codex, OpenAI and the OpenAI blossom are trademarks of
OpenAI. The files are the owner's official downloads, used byte for byte and
unmodified (no recolouring, cropping or effects), and their use here implies no
endorsement of, or partnership with, this gateway. They are not covered by the
gateway's licence.
