#!/usr/bin/env bash
# Installe TRCC RAVE : polices, GIFs d'anime, service systemd --user.
set -euo pipefail
root="$(dirname "$(readlink -f "$0")")"
cd "$root"

for dep in python3 ffmpeg pw-record trcc curl; do
  command -v "$dep" >/dev/null || { echo "Dépendance manquante : $dep" >&2; exit 1; }
done
python3 -c 'import numpy, PIL' || { echo 'Il faut python3-numpy et python3-pil' >&2; exit 1; }

echo '== Polices (Google Fonts, licence OFL)'
mkdir -p fonts
gf=https://github.com/google/fonts/raw/main/ofl
dl() { [ -s "fonts/$2" ] || curl -fsSL -o "fonts/$2" "$gf/$1"; }
dl 'orbitron/Orbitron%5Bwght%5D.ttf' Orbitron.ttf
dl sharetechmono/ShareTechMono-Regular.ttf ShareTechMono-Regular.ttf
dl dotgothic16/DotGothic16-Regular.ttf DotGothic16-Regular.ttf
for f in orbitron sharetechmono dotgothic16; do
  [ -s "fonts/OFL-$f.txt" ] || curl -fsSL -o "fonts/OFL-$f.txt" "$gf/$f/OFL.txt"
done

echo "== GIFs d'anime (nekos.best, SFW)"
python3 fetch_gifs.py

echo '== Service systemd --user'
mkdir -p ~/.config/systemd/user
sed "s#@ROOT@#$root#g" systemd/trcc-rave.service.in > ~/.config/systemd/user/trcc-rave.service
chmod +x run.sh activate.sh restore-v6.sh rave.py fetch_gifs.py
systemctl --user daemon-reload
echo
echo "Installé. Pour lancer maintenant et à chaque session : ./activate.sh"
echo "Test sans LCD : python3 rave.py --preview 12  (rendu dans preview/)"
