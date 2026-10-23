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
chmod +x run.sh activate.sh restore-v6.sh rave.py fetch_gifs.py make-video.sh
systemctl --user daemon-reload

echo '== Lanceur « TRCC Rave Vidéo » (menu + bureau, dépose un MP3 dessus)'
mkdir -p ~/.local/share/applications
sed "s#@ROOT@#$root#g" desktop/trcc-rave-video.desktop.in > ~/.local/share/applications/trcc-rave-video.desktop
desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$desk" ]; then
  sed "s#@ROOT@#$root#g" desktop/trcc-rave-video.desktop.in > "$desk/TRCC Rave Vidéo.desktop"
  chmod +x "$desk/TRCC Rave Vidéo.desktop"
  gio set "$desk/TRCC Rave Vidéo.desktop" metadata::xfce-exe-checksum \
    "$(sha256sum "$desk/TRCC Rave Vidéo.desktop" | cut -d' ' -f1)" 2>/dev/null || true
  gio set "$desk/TRCC Rave Vidéo.desktop" metadata::trusted true 2>/dev/null || true
fi
echo
echo "Installé. Pour lancer maintenant et à chaque session : ./activate.sh"
echo "Test sans LCD : python3 rave.py --preview 12  (rendu dans preview/)"
