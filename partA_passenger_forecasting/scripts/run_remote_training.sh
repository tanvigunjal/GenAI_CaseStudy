#!/usr/bin/env bash
# Copy one committed source revision to Hetzner and run the bounded full-data workflow.
set -euo pipefail

UV_VERSION="0.10.2"
REMOTE_HOST="${REMOTE_HOST:-hetzner_prod}"
SOURCE_SHA="${1:-}"

if [[ ! "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: $0 <full-lowercase-source-sha>" >&2
  exit 2
fi
if [[ ! "${REMOTE_HOST}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "REMOTE_HOST contains unsupported characters" >&2
  exit 2
fi

REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
PROJECT_ROOT="${REPOSITORY_ROOT}/partA_passenger_forecasting"
PROJECT_RELATIVE="partA_passenger_forecasting"
RAW_DATA_DIR="${RAW_DATA_DIR:-${PROJECT_ROOT}/data/raw}"
REMOTE_RUN="/home/deploy/ommax-task-a/runs/${SOURCE_SHA}"

if [[ "$(git -C "${REPOSITORY_ROOT}" rev-parse HEAD)" != "${SOURCE_SHA}" ]]; then
  echo "check out the requested source revision before copying it" >&2
  exit 1
fi
if [[ -n "$(git -C "${REPOSITORY_ROOT}" status --porcelain --untracked-files=all -- "${PROJECT_RELATIVE}")" ]]; then
  echo "the Part A source tree must be clean at the recorded revision" >&2
  exit 1
fi

for name in station_metadata.csv timeseries_with_target.csv traffic_hourly.csv weather_hourly.csv; do
  if [[ ! -f "${RAW_DATA_DIR}/${name}" ]]; then
    echo "missing raw input: ${RAW_DATA_DIR}/${name}" >&2
    exit 1
  fi
done

ssh "${REMOTE_HOST}" "mkdir -p '${REMOTE_RUN}/code' '${REMOTE_RUN}/data/raw' '${REMOTE_RUN}/work/logs' '${REMOTE_RUN}/tools'"

# History is never deleted: deliberately no --delete or other destructive synchronization flag.
rsync -az \
  --exclude '.venv/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.mypy_cache/' \
  --exclude 'data/raw/' \
  --exclude 'artifacts/' \
  --exclude 'presentation/node_modules/' \
  --exclude 'presentation/dist/' \
  "${PROJECT_ROOT}/" "${REMOTE_HOST}:${REMOTE_RUN}/code/"
rsync -az \
  "${RAW_DATA_DIR}/station_metadata.csv" \
  "${RAW_DATA_DIR}/timeseries_with_target.csv" \
  "${RAW_DATA_DIR}/traffic_hourly.csv" \
  "${RAW_DATA_DIR}/weather_hourly.csv" \
  "${REMOTE_HOST}:${REMOTE_RUN}/data/raw/"

ssh "${REMOTE_HOST}" "cd '${REMOTE_RUN}' && sha256sum -c -" <<'CHECKSUMS'
f619def0c760d9b9d01b1614252a15ea22dc3db987cfac1227c34db78ab9ff93  data/raw/station_metadata.csv
60693930f3de3415124c9334188eae51e34a0545f39098c3508463b5421f13b0  data/raw/timeseries_with_target.csv
723ea8ccb6e2e886ee028771b3a1853f8f0f77a8b6afe76ac598d2bdf2fb830a  data/raw/traffic_hourly.csv
1e34227dbc2b65f375e20d0ca19ac50a4ca65d24f9267dded015266edfa64698  data/raw/weather_hourly.csv
CHECKSUMS

ssh "${REMOTE_HOST}" "SOURCE_SHA='${SOURCE_SHA}' REMOTE_RUN='${REMOTE_RUN}' UV_VERSION='${UV_VERSION}' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

if [[ -e "${REMOTE_RUN}/SOURCE_SHA" ]] && [[ "$(tr -d '\n' < "${REMOTE_RUN}/SOURCE_SHA")" != "${SOURCE_SHA}" ]]; then
  echo "remote run directory is bound to a different source revision" >&2
  exit 1
fi
printf '%s\n' "${SOURCE_SHA}" > "${REMOTE_RUN}/SOURCE_SHA"

if [[ ! -x "${REMOTE_RUN}/tools/uv/uv" ]]; then
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | env UV_INSTALL_DIR="${REMOTE_RUN}/tools/uv" sh
fi
UV="${REMOTE_RUN}/tools/uv/uv"
cd "${REMOTE_RUN}/code"
"${UV}" --version
"${UV}" sync --frozen

export FORECAST_RAW_DATA_DIR="${REMOTE_RUN}/data/raw"
export FORECAST_OUTPUT_DIR="${REMOTE_RUN}/work"
export FORECAST_MAX_RSS_GIB="8"
export FORECAST_MAX_TUNING_SECONDS="7200"
export FORECAST_CPU_THREADS="6"
export FORECAST_SEED="20260808"
export FORECAST_EXECUTION_TARGET="hetzner_prod"
export SOURCE_REVISION="${SOURCE_SHA}"

run_step() {
  local command="$1"
  /usr/bin/time -v -o "${REMOTE_RUN}/work/logs/${command}.resources.txt" \
    "${UV}" run forecast "${command}" \
    --config "${REMOTE_RUN}/code/config/hetzner_prod.toml" \
    --data-dir "${REMOTE_RUN}/data/raw" \
    --output-dir "${REMOTE_RUN}/work" \
    > "${REMOTE_RUN}/work/logs/${command}.jsonl" 2>&1
}

run_step data-check
run_step backtest
run_step train
run_step evaluate
REMOTE_SCRIPT

echo "remote run completed at ${REMOTE_HOST}:${REMOTE_RUN}"
