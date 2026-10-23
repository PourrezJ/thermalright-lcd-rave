#!/usr/bin/env bash
# Active RAVE V7 (désactive la V6 Nova fullscroll).
set -e
systemctl --user disable --now trcc-nova-fullscroll.service trcc-nova-colors.service 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now trcc-rave.service
systemctl --user --no-pager status trcc-rave.service | head -5
