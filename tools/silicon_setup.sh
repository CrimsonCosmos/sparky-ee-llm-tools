#!/usr/bin/env bash
# Install the open silicon toolchain for sparky WITHOUT Docker or building anything: the exact tool builds LibreLane publishes (Nix-built OpenROAD, Yosys, Magic,
# Netgen, KLayout, Verilator, OpenSTA, ...) are fetched from LibreLane's container image on ghcr.io (a registry download, no container runtime needed), unpacked into
# /nix/store, and described by an env file that `silicon.toolchain` reads. Linux x86_64; needs /nix (Nix installed) and ~10 GB. Resumable and idempotent.
#
#   tools/silicon_setup.sh [version]         (default: the LibreLane version below)
#
# Where Docker works, `librelane --dockerized` is the supported alternative and needs none of this. Nothing here is a substitute for LibreLane's own installation docs.
set -euo pipefail

VERSION="${1:-3.0.17}"
ROOT="${SPARKY_SILICON_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/sparky/silicon}"
IMAGE="librelane/librelane"
CIEL_SOURCE="${CIEL_DATA_SOURCE:-static-web:https://www-archive.fossi-foundation.org/ciel-releases}"   # the default source moved; this one serves the PDK manifests
mkdir -p "$ROOT"

[ -d /nix ] || { echo "/nix not found: install Nix (https://nixos.org/download) or use 'librelane --dockerized'"; exit 1; }
[ "$(uname -m)" = x86_64 ] || { echo "this script fetches the amd64 image; on other machines use Docker or Nix per LibreLane's docs"; exit 1; }

token() { curl -fsS "https://ghcr.io/token?service=ghcr.io&scope=repository:$IMAGE:pull" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])"; }

echo "== manifest for $IMAGE:$VERSION"
T=$(token)
INDEX=$(curl -fsS -H "Authorization: Bearer $T" -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" "https://ghcr.io/v2/$IMAGE/manifests/$VERSION")
DIGEST=$(printf '%s' "$INDEX" | python3 -c "import sys,json;print(next(m['digest'] for m in json.load(sys.stdin)['manifests'] if m['platform']['architecture']=='amd64'))")
curl -fsS -H "Authorization: Bearer $T" -H "Accept: application/vnd.docker.distribution.manifest.v2+json" "https://ghcr.io/v2/$IMAGE/manifests/$DIGEST" > "$ROOT/manifest.json"
read -r LAYER SIZE CONFIG < <(python3 - "$ROOT/manifest.json" <<'PY'
import json,sys
m=json.load(open(sys.argv[1]))
big=max(m['layers'],key=lambda l:l['size'])        # the layer that holds /nix/store
print(big['digest'],big['size'],m['config']['digest'])
PY
)

if [ ! -f "$ROOT/layer.done" ]; then
  echo "== downloading $((SIZE/1000000)) MB (resumable)"
  for i in $(seq 1 60); do
    T=$(token)
    curl -sL -C - -H "Authorization: Bearer $T" -o "$ROOT/layer.tar.gz" "https://ghcr.io/v2/$IMAGE/blobs/$LAYER" || true
    [ "$(stat -c %s "$ROOT/layer.tar.gz" 2>/dev/null || echo 0)" -ge "$SIZE" ] && break
    sleep 3
  done
  [ "$(stat -c %s "$ROOT/layer.tar.gz")" -ge "$SIZE" ] || { echo "download incomplete: rerun this script to resume"; exit 1; }
  echo "== unpacking into /nix/store (existing paths are kept)"
  gunzip -c "$ROOT/layer.tar.gz" | tar -x -C / --skip-old-files --no-same-owner
  touch "$ROOT/layer.done"
  rm -f "$ROOT/layer.tar.gz"
fi

echo "== LibreLane $VERSION (pure Python, from PyPI)"
python3 -m pip install -q --no-deps --target "$ROOT/librelane-$VERSION" "librelane==$VERSION"

echo "== environment file"
T=$(token)
curl -fsS -L -H "Authorization: Bearer $T" "https://ghcr.io/v2/$IMAGE/blobs/$CONFIG" > "$ROOT/image_config.json"
python3 - "$ROOT" "$VERSION" "$CIEL_SOURCE" <<'PY'
import json,sys,pathlib
root,version,src=pathlib.Path(sys.argv[1]),sys.argv[2],sys.argv[3]
cfg=json.load(open(root/'image_config.json'))['config']
env=dict(e.split('=',1) for e in cfg['Env'])
path=[p for p in env['PATH'].split(':') if p.startswith('/nix/store/')]          # the tools' own directories, not the image's profile dirs
site=[p for p in env.get('NIX_PYTHONPATH','').split(':') if p.startswith('/nix/store/')]
out={"PATH":path,"PYTHONPATH":[str(root/f'librelane-{version}')]+site,"PDK_ROOT":str(root/'pdk'),"CIEL_HOME":str(root/'pdk'),"CIEL_DATA_SOURCE":src,"librelane_version":version}
(root/'pdk').mkdir(exist_ok=True)
(root/'env.json').write_text(json.dumps(out,indent=1))
print("wrote",root/'env.json')
PY
echo "== done. Check with: sparky doctor   (PDKs are downloaded on the first flow run, ~2 GB each family)"
