#!/usr/bin/env bash
# dev.sh — run the Aesthesis dev stack.
#
# Three layers:
#   - TRIBE     (Modal serverless at $TRIBE_SERVICE_URL)  — tribe_service/modal_app.py
#   - aesthesis_app   (FastAPI, http://127.0.0.1:8000)          — aesthesis_app/aesthesis/main.py
#   - aesthesis-app  (Next.js, http://localhost:3000)          — aesthesis-app/
#
# Modal note: TRIBE doesn't run locally and isn't "started" by this script.
# Once `modal deploy tribe_service/modal_app.py` has been run, the app sits
# in Modal's cloud forever — scales to zero containers when idle, spins one
# up on the next HTTP request to $TRIBE_SERVICE_URL/process_video_timeline,
# tears it down after ~5 min idle. The local aesthesis_app just hits the URL like
# any other API. So this script only verifies the deployed URL responds; it
# does NOT push code or hold a local process for Modal. Re-deploy manually
# whenever you change tribe_service/:
#   modal deploy tribe_service/modal_app.py
#
# Usage:  ./dev.sh
# Stop:   Ctrl-C (trap kills the local processes + their child trees)

set -eu

cd "$(dirname "$0")"

# ── Load .env ───────────────────────────────────────────────────────────────
if [[ -f .env ]]; then
    set -a; source .env; set +a
    echo "[dev] .env loaded"
else
    echo "[dev] WARNING: no .env at repo root; backend will use defaults" >&2
fi

# ── TRIBE health check ──────────────────────────────────────────────────────
# Verify the deployed TRIBE responds. The aesthesis_app will fail every
# /api/analyze if this URL is unreachable, so catching it here gives a
# clearer error than waiting for the first user request to bounce.
#
# Timeout is 30 s, not 10 s, because Modal containers scale to zero when
# idle (min_containers=0). A cold-start /health ping has to wait for
# container spin-up before responding — usually ~5 s, occasionally more.
if [[ -n "${TRIBE_SERVICE_URL:-}" ]]; then
    if curl -sS --max-time 30 -o /dev/null "${TRIBE_SERVICE_URL}/health" 2>/dev/null; then
        echo "[dev] TRIBE healthy at ${TRIBE_SERVICE_URL}"
    else
        echo "[dev] WARNING: TRIBE at ${TRIBE_SERVICE_URL} is not responding within 30 s" >&2
        echo "[dev]          Either the deployment is gone, your network is offline," >&2
        echo "[dev]          or the cold start is unusually slow." >&2
        echo "[dev]          Re-deploy with: modal deploy tribe_service/modal_app.py" >&2
    fi
else
    echo "[dev] WARNING: TRIBE_SERVICE_URL not set; backend defaults to http://localhost:8001" >&2
fi

# ── Sanity checks ───────────────────────────────────────────────────────────
# Auto-install if a one-time piece is missing. Keeps `./dev.sh` working
# straight after a fresh clone without forcing the user to remember the
# bootstrap commands. Both installs are idempotent.
#
# Use importlib.util.find_spec, NOT a real `import aesthesis.main`, so we
# don't run main.py's module-level logging (CORS configured, etc.) here —
# uvicorn will run the real import and log it once when the server starts.
if ! python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('aesthesis.main') else 1)" 2>/dev/null; then
    echo "[dev] backend package not installed; running 'pip install -e backend/'…"
    pip install -e aesthesis_app/ >/dev/null
fi

if [[ ! -d aesthesis-app/node_modules ]]; then
    echo "[dev] frontend deps missing; running 'npm install'…"
    (cd aesthesis-app && npm install)
fi

# Refuse to start if either port is already in use. Avoids the confusing
# "aesthesis-app silently exits because :3000 is taken" failure mode (Next.js
# detects the conflict and exits with code 0, which looks like success).
if curl -sS --max-time 1 -o /dev/null http://127.0.0.1:8000/health 2>/dev/null; then
    echo "[dev] ERROR: something is already serving on :8000 — stop it first." >&2
    exit 1
fi
if curl -sS --max-time 1 -o /dev/null http://127.0.0.1:3000 2>/dev/null; then
    echo "[dev] ERROR: something is already serving on :3000 — stop it first." >&2
    exit 1
fi

# ── Process management ─────────────────────────────────────────────────────
PIDS=()

cleanup() {
    echo ""
    echo "[dev] stopping…"
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
        # Git Bash on Windows: bash's `kill` doesn't always propagate to the
        # full Windows process tree. `npm run dev` in particular spawns Node
        # children that survive a bare SIGTERM. taskkill /T walks the tree.
        if command -v taskkill.exe >/dev/null 2>&1; then
            taskkill.exe //F //T //PID "$pid" >/dev/null 2>&1 || true
        fi
    done
    wait 2>/dev/null || true
    echo "[dev] done"
}
trap cleanup EXIT INT TERM

# ── Launch ──────────────────────────────────────────────────────────────────
echo "[dev] starting backend  → http://127.0.0.1:8000"
python -m uvicorn aesthesis.main:app --host 127.0.0.1 --port 8000 &
PIDS+=($!)

echo "[dev] starting frontend → http://localhost:3000"
(cd aesthesis-app && npm run dev) &
PIDS+=($!)

echo "[dev] both up. Ctrl-C to stop both."
wait
