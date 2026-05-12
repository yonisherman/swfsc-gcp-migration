#!/bin/bash
# ============================================================
# MUR42 Metadata-Aware Smart Sync (v4.1-aligned, download-only)
#
# Goal:
#   - If GCS already has FINAL -> skip.
#   - If GCS has NRT -> only download again when NASA has promoted that day to FINAL.
#   - If GCS missing -> download whatever NASA has (NRT or FINAL).
#
# Stage detection:
#   1) GCS stage: GDAL reads /vsigs and inspects product_version (and fallback title).
#   2) NASA stage: CMR granule search -> OPeNDAP .dmr -> parse product_version.
#
# Cloud Run:
#   - Uses NETRC/URS_COOKIES env vars (default /tmp paths) for Earthdata auth.
#   - Uses ROYLIB_CONFIG for config.yml mount.
# ============================================================
set -euo pipefail

gcloud config set storage/parallel_composite_upload_enabled False --quiet >/dev/null 2>&1

# ----------------------------
# Earthdata auth (Cloud Run-safe)
# ----------------------------
NETRC_FILE="${NETRC:-/tmp/.netrc}"
URS_COOKIES_FILE="${URS_COOKIES:-/tmp/.urs_cookies}"

# ----------------------------
# Logging helpers
# ----------------------------
ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log()   { echo "[$(ts)] $*"; }
info()  { log "[INFO] $*"; }
warn()  { log "[WARN] $*"; }
err()   { log "[ERR ] $*"; }
phase() { log "== $* =="; }

publish_dir_rsync() {
  local src_dir="$1" dest_dir="$2" log_file="$3"
  info "[PUBLISH] rsync -> $dest_dir"
  if gcloud storage rsync -r -c "$src_dir" "$dest_dir" >"$log_file" 2>&1; then
    info "[PUBLISH] rsync OK -> $dest_dir"
  else
    err  "[PUBLISH] rsync FAILED -> $dest_dir (tail)"
    tail -n 160 "$log_file" || true
    return 1
  fi
}

# ----------------------------
# 1) LOAD CONFIGURATION
# ----------------------------
CFG_YML="${ROYLIB_CONFIG:-/config/config.yml}"
YQ="yq"
cfg() { "$YQ" -r "$1" "$CFG_YML"; }

[[ -f "$CFG_YML" ]] || { err "Missing config: $CFG_YML"; exit 1; }

HOME_DIR="$(cfg '.HOME_DIR')"
PROD_BUCKET="$(cfg '.ERDPROD_BUCKET')"
PROD_ROOT="$(cfg '.PUBLISH_TARGETS.prod.root')"
INTERVAL_FMT="$(cfg '.PUBLISH_TARGETS.prod.interval_fmt')"

MUR_COLLECTION="$(cfg '.MUR42.collection')"
MUR_PROD_DST_DIR="$(cfg '.MUR42.prod_dst_dir')"
MUR_INTERVAL="$(cfg '.MUR42.interval')"

DEST_URI="gs://${PROD_BUCKET}/${PROD_ROOT}/${MUR_PROD_DST_DIR}/${INTERVAL_FMT/\{interval\}/$MUR_INTERVAL}"

# ----------------------------
# 2) STAGE SNIFFER (GCS via GDAL)
# ----------------------------
check_stage_gcs() {
  local uri="$1"
  local vsigs="$uri"
  [[ "$vsigs" == gs://* ]] && vsigs="/vsigs/${vsigs#gs://}"

  if ! command -v gdalinfo >/dev/null 2>&1; then
    echo "UNKNOWN"
    return 0
  fi

  # MUR commonly has analysed_sst; keep same variable as v4.1,
  # but add a fallback for title if product_version isn't found.
  local pv
  pv="$(
    gdalinfo -mdd all "HDF5:\"${vsigs}\"://analysed_sst" 2>/dev/null \
    | awk -F= '
        /^[[:space:]]*product_version=/ {
          gsub(/^[[:space:]]+/, "", $2)
          gsub(/[[:space:]]+$/, "", $2)
          print $2
          exit
        }'
  )" || true

  if [[ -z "${pv:-}" ]]; then
    # fallback: title contains interim/nrt markers for some products
    local title
    title="$(
      gdalinfo -mdd all "HDF5:\"${vsigs}\"://analysed_sst" 2>/dev/null \
      | awk -F= '
          /^[[:space:]]*title=/ {
            gsub(/^[[:space:]]+/, "", $2)
            gsub(/[[:space:]]+$/, "", $2)
            print $2
            exit
          }'
    )" || true
    if echo "$title" | grep -qiE 'nrt|interim'; then
      echo "NRT"
    else
      echo "UNKNOWN"
    fi
    return 0
  fi

  if echo "$pv" | grep -qi nrt; then
    echo "NRT"
  else
    echo "FINAL"
  fi
}

# ----------------------------
# 3) NASA HELPERS (CMR -> OPeNDAP DMR)  [same as v4.1]
# ----------------------------
cmr_get_collection_concept_id() {
  local short_name="$1"
  local cid=""

  cid="$(
    curl -fsS --max-time 20 "https://cmr.earthdata.nasa.gov/search/collections.json" --get \
      --data-urlencode "short_name=${short_name}" \
      --data-urlencode "provider=POCLOUD" \
      --data-urlencode "page_size=1" \
    | jq -r '.feed.entry[0].id // empty'
  )" || true

  if [[ -n "$cid" ]]; then
    echo "$cid"
    return 0
  fi

  curl -fsS --max-time 20 "https://cmr.earthdata.nasa.gov/search/collections.json" --get \
    --data-urlencode "short_name=${short_name}" \
    --data-urlencode "page_size=10" \
  | jq -r '.feed.entry[]?.id' \
  | head -n 1
}

cmr_get_opendap_url_for_day() {
  local concept_id="$1"
  local day="$2"  # YYYY-MM-DD

  curl -fsS --max-time 20 "https://cmr.earthdata.nasa.gov/search/granules.json" --get \
    --data-urlencode "collection_concept_id=${concept_id}" \
    --data-urlencode "temporal=${day}T09:00:00Z,${day}T09:00:01Z" \
    --data-urlencode "page_size=1" \
  | jq -r '.feed.entry[0].links[]?.href | select(test("opendap\\.earthdata\\.nasa\\.gov"))' \
  | head -n 1
}

check_stage_nasa_day() {
  local day="$1"        # YYYY-MM-DD
  local concept_id="$2" # Cxxxx-POCLOUD

  if ! command -v jq >/dev/null 2>&1 || ! command -v perl >/dev/null 2>&1; then
    echo "UNKNOWN"
    return 0
  fi

  local od
  od="$(cmr_get_opendap_url_for_day "$concept_id" "$day" 2>/dev/null || true)"
  if [[ -z "$od" ]]; then
    echo "MISSING"
    return 0
  fi

  local pvs
  pvs="$(
    curl -sS -L --netrc-file "$NETRC_FILE" -c "$URS_COOKIES_FILE" -b "$URS_COOKIES_FILE" \
      --connect-timeout 10 --max-time 60 "${od}.dmr" 2>/dev/null \
    | perl -0777 -ne '@m=/<Attribute name="product_version"[^>]*>.*?<Value>([^<]*)<\/Value>/sg; print join("\n",@m)'
  )" || true

  if [[ -z "$pvs" ]]; then
    echo "UNKNOWN"
    return 0
  fi

  if echo "$pvs" | grep -qi nrt; then
    echo "NRT"
  else
    echo "FINAL"
  fi
}

# ----------------------------
# 4) RUN SETTINGS
# ----------------------------
LOOKBACK="${LOOKBACK:-31}"          # match your v4.1 default behavior
MIN_NRT_AGE_DAYS="${MIN_NRT_AGE_DAYS:-1}"

phase "MUR42 smart sync start"
info "lookback=${LOOKBACK}d"
info "DEST_URI=${DEST_URI}"

# ----------------------------
# 5) RESOLVE CMR CONCEPT-ID (ONCE PER RUN)
# ----------------------------
# You should set this to the correct MUR42 concept-id if you want a fallback.
DEFAULT_MUR_CONCEPT_ID="${DEFAULT_MUR_CONCEPT_ID:-}"
MUR_CONCEPT_ID="$(cmr_get_collection_concept_id "$MUR_COLLECTION" 2>/dev/null || true)"

if [[ -n "${MUR_CONCEPT_ID:-}" ]]; then
  info "CMR concept-id resolved dynamically: ${MUR_CONCEPT_ID}"
else
  if [[ -n "${DEFAULT_MUR_CONCEPT_ID}" ]]; then
    MUR_CONCEPT_ID="${DEFAULT_MUR_CONCEPT_ID}"
    warn "CMR lookup failed; using fallback concept-id: ${MUR_CONCEPT_ID}"
  else
    warn "CMR lookup failed and no DEFAULT_MUR_CONCEPT_ID set; NASA stage will be UNKNOWN"
  fi
fi

# ----------------------------
# 6) MAIN LOOP (download-only)
# ----------------------------
for i in $(seq "$LOOKBACK" -1 0); do
  CURRENT_DAY="$(date -u -d "$i days ago" +%Y-%m-%d)"
  FILE_DATE="$(date -u -d "$i days ago" +%Y%m%d)"

  if [[ "$i" -eq 0 ]]; then
    info "$CURRENT_DAY [WAIT] same-day; skip NASA probe"
    continue
  fi

  # 1) Probe GCS
  GCS_FILE="$(gcloud storage ls "${DEST_URI}/${FILE_DATE}*.nc" 2>/dev/null | head -n 1 || true)"

  if [[ -n "$GCS_FILE" ]]; then
    STAGE="$(check_stage_gcs "$GCS_FILE")"
    if [[ "$STAGE" == "FINAL" ]]; then
      info "$CURRENT_DAY [SKIP] FINAL already in GCS"
      continue
    fi
    if [[ "$STAGE" == "UNKNOWN" ]]; then
      info "$CURRENT_DAY [SKIP] exists but stage UNKNOWN (no gdalinfo/attrs)"
      continue
    fi
    if [[ "$i" -lt "$MIN_NRT_AGE_DAYS" ]]; then
      info "$CURRENT_DAY [WAIT] NRT (<${MIN_NRT_AGE_DAYS}d old); check later"
      continue
    fi
  else
    STAGE="MISSING"
  fi

  # 2) Probe NASA stage (only when needed)
  NASA_STAGE="UNKNOWN"
  if [[ -n "${MUR_CONCEPT_ID:-}" ]]; then
    NASA_STAGE="$(check_stage_nasa_day "$CURRENT_DAY" "$MUR_CONCEPT_ID")"
  fi

  # 3) Decide action (same as v4.1)
  if [[ -n "$GCS_FILE" ]]; then
    if [[ "$NASA_STAGE" == "FINAL" ]]; then
      info "$CURRENT_DAY [UPDATE] GCS=NRT, NASA=FINAL -> download"
    elif [[ "$NASA_STAGE" == "NRT" ]]; then
      info "$CURRENT_DAY [SKIP] GCS=NRT, NASA=NRT"
      continue
    elif [[ "$NASA_STAGE" == "MISSING" ]]; then
      info "$CURRENT_DAY [SKIP] NASA missing granule"
      continue
    else
      info "$CURRENT_DAY [SKIP] NASA stage UNKNOWN; avoid thrash"
      continue
    fi
  else
    if [[ "$NASA_STAGE" == "NRT" || "$NASA_STAGE" == "FINAL" ]]; then
      info "$CURRENT_DAY [MISS] GCS missing; NASA=${NASA_STAGE} -> download"
    elif [[ "$NASA_STAGE" == "MISSING" ]]; then
      info "$CURRENT_DAY [IDLE] NASA has no granule"
      continue
    else
      info "$CURRENT_DAY [IDLE] NASA stage UNKNOWN; skipping"
      continue
    fi
  fi

  # ----------------------------
  # DOWNLOAD + PUBLISH (no processing)
  # ----------------------------
  DAY_CACHE="${HOME_DIR}/mur42_sync_${FILE_DATE}"
  DL_DIR="${DAY_CACHE}/dl"
  LOG_DIR="${DAY_CACHE}/logs"

  rm -rf "$DAY_CACHE"
  mkdir -p "$DL_DIR" "$LOG_DIR"

  phase "$CURRENT_DAY FETCH"
  DL_LOG="${LOG_DIR}/podaac_download.log"
  info "[FETCH] collection=${MUR_COLLECTION} window=${CURRENT_DAY}T09:00:00Z..09:00:01Z -> ${DL_DIR}"

  if podaac-data-downloader -c "$MUR_COLLECTION" -d "$DL_DIR" \
       -sd "${CURRENT_DAY}T09:00:00Z" -ed "${CURRENT_DAY}T09:00:01Z" --limit 1 \
       >"$DL_LOG" 2>&1; then
    if grep -q 'SUCCESS:' "$DL_LOG"; then
      grep 'SUCCESS:' "$DL_LOG" | sed 's/^.*SUCCESS: /[INFO] [FETCH] /'
    else
      info "[FETCH] done (no SUCCESS line found; check $DL_LOG if needed)"
    fi
  else
    err "[FETCH] failed (tail follows)"
    tail -n 120 "$DL_LOG" || true
    rm -rf "$DAY_CACHE"
    exit 1
  fi

  RAW_FILE="$(ls "$DL_DIR"/*.nc 2>/dev/null | head -n 1 || true)"
  if [[ -z "$RAW_FILE" ]]; then
    info "[FETCH] no .nc produced; skipping day"
    rm -rf "$DAY_CACHE"
    continue
  fi
  info "[FETCH] file=$(basename "$RAW_FILE")"

  phase "$CURRENT_DAY PUBLISH"
  RSYNC_LOG="${LOG_DIR}/rsync.log"
  publish_dir_rsync "${DL_DIR}/" "${DEST_URI}/" "$RSYNC_LOG"

  rm -rf "$DAY_CACHE"
done

phase "Done"