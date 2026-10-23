#!/usr/bin/env bash
# Revient à l'affichage Nova V6 (carrousel capteurs calme).
set -e
systemctl --user disable --now trcc-rave.service 2>/dev/null || true
systemctl --user enable --now trcc-nova-fullscroll.service
systemctl --user --no-pager status trcc-nova-fullscroll.service | head -5
