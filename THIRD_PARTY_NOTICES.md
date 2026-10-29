# Third-party notices

showntell bundles these libraries in `src/showntell/static/vendor/`, unmodified:

| Library | Version | Licence | Source |
|---|---|---|---|
| **Mermaid** | 11.4.1 | MIT | https://github.com/mermaid-js/mermaid |
| **KaTeX** | 0.16.11 | MIT | https://github.com/KaTeX/KaTeX |
| **highlight.js** | 11.10.0 | BSD-3-Clause | https://github.com/highlightjs/highlight.js |
| **Rough.js** | 4.6.6 | MIT | https://github.com/rough-stuff/rough |
| **Virgil** (font) | 3d4c829 | OFL-1.1 | https://github.com/excalidraw/virgil |
| **draw.io viewer** (`viewer-static.min.js`) | 31.7.0 | Apache-2.0 | https://github.com/jgraph/drawio |
| **Inter** (font, Latin subset via Fontsource) | 5.3.0 | OFL-1.1 | https://github.com/rsms/inter |
| **Bricolage Grotesque** (font, Latin subset via Fontsource) | 5.3.0 | OFL-1.1 | https://github.com/ateliertriay/bricolage |
| **JetBrains Mono** (font, Latin subset via Fontsource) | 5.3.0 | OFL-1.1 | https://github.com/JetBrains/JetBrainsMono |

The Lucide icons (`lucide-static` 1.50.0, ISC, https://github.com/lucide-icons/lucide) are bundled as `src/showntell/data/icons.json`: each icon's SVG elements and search tags, converted from the package's `icon-nodes.json` and `tags.json` by `scripts/vendor.sh`. The licence is in `static/vendor/LICENSES/lucide.txt`.

showntell downloads these models on first run; they are not part of the package:

| Model | Licence | Source |
|---|---|---|
| **Kokoro-82M** (weights and voices) | Apache-2.0 | https://huggingface.co/hexgrad/Kokoro-82M, via mlx-community/Kokoro-82M-bf16 and onnx-community/Kokoro-82M-v1.0-ONNX-timestamped |
| **spaCy en_core_web_sm** 3.8.0 | MIT | https://github.com/explosion/spacy-models |

`deploy/chromium-seccomp.json` is Docker's default seccomp profile (moby/profiles `seccomp/default.json`, commit 6fe7deb, Apache-2.0, https://github.com/moby/profiles) with one rule added for Chromium's sandbox.

Runtime dependencies are installed from PyPI under their own licences. The optional `espeak` extra installs espeak-ng (GPL-3.0); it is not installed by default.

The full licence texts of the bundled libraries are in `src/showntell/static/vendor/LICENSES/`.
