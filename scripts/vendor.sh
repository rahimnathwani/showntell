#!/bin/zsh
# Download the pinned browser libraries into src/showntell/static/vendor, checking each
# tarball's sha256, so the player works offline.
set -euo pipefail
cd "${0:A:h}/.."
V=src/showntell/static/vendor
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fetch() { # name version sha256
  local name=$1 version=$2 sum=$3 base=${1##*/}
  mkdir -p "$TMP/$name"
  curl -fsSL "https://registry.npmjs.org/$name/-/$base-$version.tgz" -o "$TMP/$base.tgz"
  echo "$sum  $TMP/$base.tgz" | shasum -a 256 -c --quiet
  tar -xzf "$TMP/$base.tgz" -C "$TMP/$name"
}
fetch_url() { # url sha256 destination
  curl -fsSL "$1" -o "$3"
  echo "$2  $3" | shasum -a 256 -c --quiet
}
rm -rf "$V/hljs" "$V/katex" "$V/mermaid.min.js" "$V/drawio" "$V/rough" "$V/fonts" "$V/reladraw"
mkdir -p "$V/katex/fonts" "$V/hljs" "$V/drawio" "$V/LICENSES" "$V/rough" "$V/fonts"
fetch mermaid 11.4.1 ec7b61b391aaa24bf666fd20e48d0a626eeb4a95b95bc6b3016a122d7f0bc2af
cp "$TMP/mermaid/package/dist/mermaid.min.js" "$V/"
cp "$TMP/mermaid/package/LICENSE" "$V/LICENSES/mermaid.txt" 2>/dev/null || true
fetch katex 0.16.11 8c5494c5f7d8e8bf73ff3fe64d4bd09a340e4878b7eec9bbd9ef8d8f3e24193f
cp "$TMP/katex/package/dist/katex.min.js" "$TMP/katex/package/dist/katex.min.css" "$V/katex/"
cp "$TMP/katex/package/dist/fonts/"*.woff2 "$V/katex/fonts/"
cp "$TMP/katex/package/LICENSE" "$V/LICENSES/katex.txt"
fetch @highlightjs/cdn-assets 11.10.0 5df5f63aad9acb3660d0d008cd68bdf5a6b7121816f517e2434d1e2f027b220f
cp "$TMP/@highlightjs/cdn-assets/package/highlight.min.js" "$V/hljs/"
cp "$TMP/@highlightjs/cdn-assets/package/LICENSE" "$V/LICENSES/highlight.js.txt"
fetch roughjs 4.6.6 373873b2c3f1b7ddba878525c774816057c5818029780c7fba081f06018ba78c
cp "$TMP/roughjs/package/bundled/rough.js" "$V/rough/"
cp "$TMP/roughjs/package/LICENSE" "$V/LICENSES/roughjs.txt"
# Virgil, the hand-drawn font of drawings marked `sketch`, at a pinned commit of its repository.
VIRGIL=https://raw.githubusercontent.com/excalidraw/virgil/3d4c829dc85b455d4646631ac52bc1517df04217
curl -fsSL "$VIRGIL/Virgil.woff2" -o "$V/fonts/Virgil.woff2"
echo "9976295bfe709bdea64839a4d4e9a1d436dd6eb67538399a5a0e8b8fadbcf1cf  $V/fonts/Virgil.woff2" | shasum -a 256 -c --quiet
curl -fsSL "$VIRGIL/LICENSE.md" -o "$V/LICENSES/virgil.txt"
# The player's faces (OFL), Latin subsets of the variable fonts: text, titles and code look the
# same on every machine and in MP4 export.
fetch @fontsource-variable/inter 5.3.0 d26710dd38e7217484a1d47ee76c024977ec550548876f26d06ed1844af09a14
cp "$TMP/@fontsource-variable/inter/package/files/inter-latin-wght-normal.woff2" "$V/fonts/Inter.woff2"
cp "$TMP/@fontsource-variable/inter/package/LICENSE" "$V/LICENSES/inter.txt"
fetch @fontsource-variable/bricolage-grotesque 5.3.0 6e99e0d844f7bc152bb3b007f9b1493c53e60cc0744cc681bb744e0b5f14665e
cp "$TMP/@fontsource-variable/bricolage-grotesque/package/files/bricolage-grotesque-latin-wght-normal.woff2" "$V/fonts/Bricolage.woff2"
cp "$TMP/@fontsource-variable/bricolage-grotesque/package/LICENSE" "$V/LICENSES/bricolage-grotesque.txt"
fetch @fontsource-variable/jetbrains-mono 5.3.0 996fe6368a480c9ce15d4de22a2682b7c40b403718fee2b15e7272f244fd993f
cp "$TMP/@fontsource-variable/jetbrains-mono/package/files/jetbrains-mono-latin-wght-normal.woff2" "$V/fonts/JetBrainsMono.woff2"
cp "$TMP/@fontsource-variable/jetbrains-mono/package/LICENSE" "$V/LICENSES/jetbrains-mono.txt"
# Lucide icons (ISC), as one JSON file the builder reads: name -> inner SVG, plus search tags.
fetch lucide-static 1.50.0 b23b1b4c30dbf0877eee2087275f48310b45a098e537ba83598cce1ea553dce6
cp "$TMP/lucide-static/package/LICENSE" "$V/LICENSES/lucide.txt"
python3 - "$TMP/lucide-static/package" src/showntell/data/icons.json <<'EOF'
import json, sys
from pathlib import Path
from xml.sax.saxutils import quoteattr
src, out = Path(sys.argv[1]), Path(sys.argv[2])
nodes = json.loads((src / "icon-nodes.json").read_text())
tags = json.loads((src / "tags.json").read_text())
draw = lambda parts: "".join(f"<{t}{''.join(f' {k}={quoteattr(str(v))}' for k, v in a.items() if k != 'key')}/>" for t, a in parts)
icons = {name: draw(parts) for name, parts in sorted(nodes.items())}
out.write_text(json.dumps({"version": "lucide-static 1.50.0", "icons": icons, "tags": {k: tags.get(k, []) for k in icons}}, separators=(",", ":")))
EOF
# The draw.io viewer (Apache-2.0) is not on npm; it is pinned to a release tag of jgraph/drawio.
DRAWIO=https://raw.githubusercontent.com/jgraph/drawio/v31.7.0
fetch_url "$DRAWIO/src/main/webapp/js/viewer-static.min.js" 53a25e8f766e759835a3a6a35d7e88742cb41762ed631ddd6944e38723dade33 "$V/drawio/viewer-static.min.js"
curl -fsSL "$DRAWIO/LICENSE" -o "$V/LICENSES/drawio.txt"
# reladraw (Apache-2.0) ships ES modules; esbuild, pinned and run only here, bundles the browser
# entry (no command line or server) into one script that sets window.reladraw, as the others do.
fetch reladraw 0.29.0 d3afa9b0fd8d0d6dcc68012b19ac1f278e1a8c3f4b74431da5a0f935a1d0cdb6
mkdir -p "$V/reladraw"
cp scripts/vendor/reladraw-entry.js "$TMP/reladraw/entry.js"
npx --yes esbuild@0.25.12 "$TMP/reladraw/entry.js" --bundle --format=iife --global-name=reladraw --minify --legal-comments=none \
  --outfile="$V/reladraw/reladraw.min.js" --log-level=warning
cp "$TMP/reladraw/package/LICENSE" "$V/LICENSES/reladraw.txt"
cp "$TMP/reladraw/package/NOTICE" "$V/LICENSES/reladraw-NOTICE.txt"
du -sh "$V"
