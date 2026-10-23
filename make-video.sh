#!/usr/bin/env bash
# TRCC RAVE : transforme ton MP3 en vidéo de démo synchronisée (4:3 et/ou verticale).
#
#   ./make-video.sh morceau.mp3                      # les deux formats, morceau entier
#   ./make-video.sh morceau.mp3 -f vertical          # vertical | 4x3 | both
#   ./make-video.sh morceau.mp3 -s 45 -d 30          # extrait : 30 s à partir de 0:45
#   ./make-video.sh morceau.mp3 --no-outro           # sans « SIGNAL LOST » final
#   ./make-video.sh --gui [fichiers...]              # fenêtres (icône du bureau)
#
# Vidéos dans ~/Vidéos/TRCC-Rave/. Le début montre le monitoring, l'OVERLOAD se déclenche
# tout seul quand la musique devient énervée (intro HACKED), et la fin repasse en
# monitoring avec SIGNAL LOST.
set -euo pipefail
root="$(dirname "$(readlink -f "$0")")"
outdir="$(xdg-user-dir VIDEOS 2>/dev/null || echo "$HOME/Videos")/TRCC-Rave"

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

gui=0; fmt=both; start=''; dur=''; outro=1; files=()
while [ $# -gt 0 ]; do
  case "$1" in
    --gui) gui=1 ;;
    -f|--format) fmt="$2"; shift ;;
    -s|--start) start="$2"; shift ;;
    -d|--duration) dur="$2"; shift ;;
    --no-outro) outro=0 ;;
    -h|--help) usage ;;
    *) files+=("$1") ;;
  esac
  shift
done

err() {
  if [ "$gui" = 1 ]; then zenity --error --title='TRCC Rave' --text="$1" 2>/dev/null; fi
  echo "Erreur : $1" >&2; exit 1
}

if [ "$gui" = 1 ]; then
  if [ ${#files[@]} -eq 0 ]; then
    f=$(zenity --file-selection --title='TRCC Rave : choisis ton morceau' \
          --file-filter='Audio | *.mp3 *.MP3 *.wav *.flac *.ogg *.m4a *.opus *.aac' 2>/dev/null) || exit 0
    files=("$f")
  fi
  form=$(zenity --forms --title='TRCC Rave : vidéo' --text="${#files[@]} morceau(x) : $(basename "${files[0]}")" \
          --add-combo='Format' --combo-values='Les deux|Vertical (Reels, TikTok, Shorts)|4:3 (YouTube)' \
          --add-entry='Début en secondes (vide = 0)' \
          --add-entry='Durée en secondes (vide = tout)' \
          --add-combo='Fin SIGNAL LOST' --combo-values='Oui|Non' \
          --separator='|' 2>/dev/null) || exit 0
  IFS='|' read -r f_fmt start dur f_out <<< "$form"
  case "$f_fmt" in Vertical*) fmt=vertical ;; 4:3*) fmt=4x3 ;; *) fmt=both ;; esac
  [ "$f_out" = 'Non' ] && outro=0
fi

[ ${#files[@]} -gt 0 ] || usage 1
case "$fmt" in both|vertical|4x3) ;; *) err "format inconnu : $fmt (both, vertical ou 4x3)" ;; esac
num='^[0-9]+([.][0-9]+)?$'
[ -z "$start" ] || [[ "$start" =~ $num ]] || err "début invalide : $start"
[ -z "$dur" ] || [[ "$dur" =~ $num ]] || err "durée invalide : $dur"
for dep in ffmpeg ffprobe python3; do command -v "$dep" >/dev/null || err "$dep manquant"; done
[ -s "$root/fonts/Orbitron.ttf" ] || err "polices absentes : lance d'abord ./install.sh"
ls "$root"/gifs/*.gif >/dev/null 2>&1 || err "aucun GIF : lance d'abord ./install.sh"
mkdir -p "$outdir"

made=()
for src in "${files[@]}"; do
  [ -f "$src" ] || err "fichier introuvable : $src"
  name="$(basename "${src%.*}" | tr -c 'A-Za-z0-9._-' '_' | sed 's/_\+/_/g; s/_$//')"
  [ -n "$start" ] && name="${name}_${start%.*}s"
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  wav="$tmp/audio.wav"

  # 1. audio : extrait, fondu de fin, silence final pour l'outro
  total=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$src")
  s0=${start:-0}
  len=$(python3 -c "t=float('$total')-float('$s0'); d='$dur'; print(max(1.0, min(t, float(d)) if d else t))")
  fade=$(python3 -c "print(max(0, $len - 1.5))")
  af="aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:d=0.3,afade=t=out:st=$fade:d=1.5"
  [ "$outro" = 1 ] && af="$af,apad=pad_dur=6"
  ffmpeg -v error -y -ss "$s0" -t "$len" -i "$src" -af "$af" -c:a pcm_s16le "$wav" \
    || err "impossible de lire $src"

  # 2. rendu synchronisé + encodage HD
  extra=(); [ "$outro" = 1 ] && extra=(--outro)
  echo "== $src -> $outdir/$name-*.mp4"
  run() { nice -n 10 python3 "$root/rave.py" --render "$wav" --out "$outdir/$name" --format "$fmt" "${extra[@]}"; }
  set +e
  if [ "$gui" = 1 ]; then
    run 2>"$tmp/log" | sed -u -n 's/^PROGRESS //p' \
      | zenity --progress --title='TRCC Rave' --text="Rendu de $(basename "$src")…" --percentage=0 --auto-close 2>/dev/null
    st=${PIPESTATUS[0]}
  else
    run 2>"$tmp/log" | sed -u -n 's/^PROGRESS \([0-9]*\)$/\1/p' | while read -r p; do printf '\r  rendu %3d %%' "$p"; done
    st=${PIPESTATUS[0]}
    echo
  fi
  set -e
  [ "$st" = 0 ] || err "le rendu a échoué : $(grep -v drawn_on "$tmp/log" | tail -3)"
  for f in "$outdir/$name"-*.mp4; do [ -f "$f" ] && made+=("$f"); done
  rm -rf "$tmp"; trap - EXIT
done

printf 'Vidéo : %s\n' "${made[@]}"
if [ "$gui" = 1 ]; then
  (zenity --notification --text="TRCC Rave : ${#made[@]} vidéo(s) prête(s)" 2>/dev/null &)
  xdg-open "$outdir" >/dev/null 2>&1 &
fi
