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
(<https://github.com/JetBrains/kotlin>). The build also uses, without shipping
them in the app: the Kotlin compiler (Apache-2.0), bundletool (Apache-2.0), an
Android platform `android.jar` (compile-time stubs), and JUnit (EPL-1.0) and
Hamcrest (BSD) for the unit tests.

## Card data and images

Card data and images come from Scryfall (<https://scryfall.com>) under its API
terms. Magic: The Gathering is © Wizards of the Coast. This project is
unofficial Fan Content permitted under the Fan Content Policy and is not
approved or endorsed by Wizards.
