#!/usr/bin/env bash
# CBS Transcriber API-only enhancement supervisor, generated per run. Contains no credentials.
# It communicates only through files in its owned run folder, which the app reads via the API.
set -u
umask 077
RUN=__RUN__
RT=__RUNTIME__
PREPARE=__PREPARE__
BATCH=__BATCH__
PREPARE_ONLY=__PREPARE_ONLY__
GENERATION=__GENERATION__
MODELS="__MODELS__"
# Folders are mounted under /work/<name>; look a level deeper if an app nests them.
locate() {
  [ -d "$1" ] && { echo "$1"; return; }
  found=$(find /work -maxdepth 3 -type d -name "$(basename "$1")" 2>/dev/null | head -n 1)
  echo "${found:-$1}"
}
RUN=$(locate "$RUN")
RT=$(locate "$RT")
export HF_HOME="$RT/hf" NLTK_DATA="$RT/nltk" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 PYANNOTE_METRICS_ENABLED=0 TOKENIZERS_PARALLELISM=false
unset HF_TOKEN HUGGING_FACE_HUB_TOKEN
PY="$RT/venv/bin/python"

phase() {
  printf '{"phase":"%s","code":"%s","generation":"%s"}' "$1" "${2:-}" "$GENERATION" \
    > "$RUN/supervisor.pending" && mv -f "$RUN/supervisor.pending" "$RUN/supervisor.json"
}

fail() {
  phase failed "$1"
  exit 0
}

LOCK="$RT/.prepare-lock"
HOLDING_LOCK=0
release_lock() {
  [ "$HOLDING_LOCK" = 1 ] && rm -rf "$LOCK"
  HOLDING_LOCK=0
}
trap release_lock EXIT
trap 'release_lock; exit 143' INT TERM

take_lock() {
  # Age does not prove the previous preparer has stopped. Never steal a live lock.
  mkdir "$LOCK" 2>/dev/null || return 1
  HOLDING_LOCK=1
  echo "$GENERATION" > "$LOCK/owner"
}

prepared() {
  grep -qs '"requirements_sha256":"__REQ_SHA256__"' "$RT/stamp.json"
}

install_runtime() {
  (
    set -euo pipefail
    rm -rf "$RT/bin" "$RT/python" "$RT/venv" "$RT/stamp.json"
    mkdir -p "$RT/bin" "$RT/hf" "$RT/nltk"
    curl -fsSL --retry 3 __UV_URL__ -o "$RT/uv.tar.gz"
    echo "__UV_SHA256__  $RT/uv.tar.gz" | sha256sum -c --status
    tar -xzf "$RT/uv.tar.gz" -C "$RT/bin" --strip-components=1
    rm -f "$RT/uv.tar.gz"
    export UV_PYTHON_INSTALL_DIR="$RT/python" UV_CACHE_DIR="$RT/uv-cache" UV_NO_CONFIG=1 \
      UV_PYTHON_PREFERENCE=only-managed
    "$RT/bin/uv" venv --python __PYTHON__ "$RT/venv"
    "$RT/bin/uv" pip install --python "$RT/venv/bin/python" --require-hashes \
      --index-strategy unsafe-best-match --extra-index-url __TORCH_INDEX__ \
      -r "$RUN/requirements.txt"
    HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 "$PY" -I "$RUN/enhancement_worker.py" --prefetch \
      --models "$MODELS" > /dev/null
    rm -rf "$RT/uv-cache"
    cp "$RUN/requirements.txt" "$RT/requirements.txt"
    printf '{"requirements_sha256":"%s"}' "__REQ_SHA256__" > "$RT/stamp.pending"
    mv -f "$RT/stamp.pending" "$RT/stamp.json"
  ) > "$RUN/prepare.log" 2>&1
}

# Called only while holding the preparation lock; immutable model readers keep running.
fetch_models() {
  (
    set -euo pipefail
    HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 "$PY" -I "$RUN/enhancement_worker.py" --prefetch \
      --models "$MODELS" > /dev/null
  ) >> "$RUN/prepare.log" 2>&1
}

prepare() {
  mkdir -p "$RT" || return 1
  take_lock || return 2
  # Create once without truncation; replacing this inode would bypass existing readers.
  if [ ! -e "$RT/.in-use" ]; then
    (set -o noclobber; : > "$RT/.in-use") 2>/dev/null || {
      release_lock
      return 1
    }
  fi
  if [ ! -f "$RT/.in-use" ] || [ -L "$RT/.in-use" ]; then
    release_lock
    return 1
  fi
  exec 9< "$RT/.in-use" || { release_lock; return 1; }
  # The stamp may have become valid while this job was waiting to take ownership.
  if prepared; then
    fetch_models
  elif flock -xn 9; then
    install_runtime
  else
    release_lock
    return 2
  fi
  local status=$?
  # Keep a shared lock through doctor, the controller and its inherited worker children.
  if [ "$status" = 0 ]; then
    flock -s 9 || status=1
  fi
  release_lock
  return "$status"
}

hold_runtime() {
  # Prepared runtimes mount read-only. Missing legacy locks must be migrated before launch.
  [ -f "$RT/.in-use" ] && [ ! -L "$RT/.in-use" ] || return 3
  exec 9< "$RT/.in-use" || return 3
  flock -sn 9 || return 2
  prepared || return 1
}

[ -d "$RUN" ] || exit 1
phase started
if [ "$PREPARE" = 1 ]; then
  phase preparing
  prepare
  case $? in
    0) ;;
    2) fail runtime-busy ;;
    *) fail runtime-prepare ;;
  esac
else
  prepared || fail runtime-missing
  hold_runtime
  case $? in
    0) ;;
    2) fail runtime-busy ;;
    3) fail runtime-lock-missing ;;
    *) fail runtime-missing ;;
  esac
fi
export CBS_RUNTIME_LOCK_FD=9
"$PY" -I "$RUN/enhancement_worker.py" --doctor --hardware > "$RUN/doctor.pending" 2>/dev/null \
  && mv -f "$RUN/doctor.pending" "$RUN/doctor.json" || fail runtime
"$PY" -I "$RUN/enhancement_worker.py" --cache-status > "$RUN/cache.pending" 2>/dev/null \
  && mv -f "$RUN/cache.pending" "$RUN/cache.json" || fail runtime
if [ "$BATCH" = 1 ]; then
  "$PY" -I "$RUN/remote_audio_input.py" capability > "$RUN/decoder.pending" 2>/dev/null \
    && mv -f "$RUN/decoder.pending" "$RUN/decoder.json" || fail runtime
fi
phase ready
if [ "$PREPARE_ONLY" = 1 ]; then
  phase finished
  exit 0
fi
if [ "$BATCH" = 1 ]; then
  "$PY" -I "$RUN/runtime_batch_remote.py" run "$RUN"
  exit $?
fi
waited=0
while [ ! -f "$RUN/manifest.sha256" ]; do
  [ "$waited" -ge __WAIT_SECONDS__ ] && fail manifest-timeout
  sleep 2
  waited=$((waited + 2))
done
(cd "$RUN" && sha256sum -c --status manifest.sha256) || fail manifest
phase running
"$PY" -I "$RUN/enhancement_remote.py" run "$RUN" "$GENERATION"
phase finished
exit 0
