#!/usr/bin/env bash
# Pull derived artifacts without raw data, environments, or tuning caches.
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-hetzner_prod}"
SOURCE_SHA="${1:-}"
DESTINATION="${2:-}"

if [[ ! "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]] || [[ -z "${DESTINATION}" ]]; then
  echo "usage: $0 <full-lowercase-source-sha> <local-destination>" >&2
  exit 2
fi
if [[ ! "${REMOTE_HOST}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "REMOTE_HOST contains unsupported characters" >&2
  exit 2
fi

REMOTE_RUN="/home/deploy/task-a/runs/${SOURCE_SHA}"
mkdir -p "${DESTINATION}"
rsync -az \
  --exclude '.venv/' \
  --exclude '.cache/' \
  --exclude 'tuning/' \
  --exclude 'optuna/' \
  --exclude 'data/' \
  "${REMOTE_HOST}:${REMOTE_RUN}/work/" "${DESTINATION}/"

echo "derived evidence copied to ${DESTINATION}"

