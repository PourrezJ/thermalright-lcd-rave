#!/usr/bin/env bash
set -euo pipefail
k='0402:3922'
root="$(dirname "$(readlink -f "$0")")"
for try in $(seq 1 120); do
  if trcc status --json 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); assert any(p["key"]=="0402:3922" for p in d.get("lcd_devices",[]))' 2>/dev/null; then
    break
  fi
  if [ "$try" -eq 120 ]; then echo 'TRCC LCD indisponible après 120 s' >&2; exit 1; fi
  sleep 1
done
# Ce moteur possède l'image entière : rien d'autre ne doit écrire sur le LCD.
systemctl --user stop trcc-nova-fullscroll.service trcc-nova-colors.service 2>/dev/null || true
trcc display slideshow "$k" off
exec /usr/bin/python3 "$root/rave.py"
