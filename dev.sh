#!/usr/bin/env bash
# dev.sh — run the Aesthesis dev stack.
#
# Three layers:
#   - TRIBE     (Modal serverless at $TRIBE_SERVICE_URL)  — tribe_service/modal_app.py
#   - backend   (FastAPI, http://127.0.0.1:8000)          — aesthesis_app/aesthesis/main.py
#   - frontend  (Next.js, http://localhost:3000)          — aesthesis-app/
#
# Modal note: TRIBE doesn't run locally and isn't "started" by this script.
# Once `modal deploy tribe_service/modal_app.py` has been run, the app sits
# in Modal's cloud forever — scales to zero containers when idle, spins one
# up on the next HTTP request to $TRIBE_SERVICE_URL/process_video_timeline,
# tears it down after ~5 min idle. The local backend just hits the URL like
# any other API. So this script only verifies the deployed URL responds; it
# does NOT push code or hold a local process for Modal. Re-deploy manually
# whenever you change tribe_service/:
#   modal deploy tribe_service/modal_app.py
#
# Usage:  ./dev.sh                       (Git Bash / WSL / Linux / macOS)
#         dev.cmd  or  .\dev.cmd         (Windows cmd.exe / PowerShell — wrapper)
# Stop:   Ctrl-C (trap kills the local processes + their child trees)

set -eu

cd "$(dirname "$0")"

# ── Load .env ───────────────────────────────────────────────────────────────
# Strip carriage returns first. .env on Windows is usually CRLF, and bash's
# `source` attaches a literal \r to every value — so `TRIBE_SERVICE_URL`
# becomes `https://...modal.run\r` and curl/the backend silently 404 on
# every request. Process-substitute through `tr -d '\r'` to neutralise it.
if [[ -f .env ]]; then
    set -a
    source <(tr -d '\r' < .env)
    set +a
    echo "[dev] .env loaded"
else
    echo "[dev] WARNING: no .env at repo root; backend will use defaults" >&2
fi

# ── TRIBE health check ──────────────────────────────────────────────────────
# Verify the deployed TRIBE responds. The backend will fail every
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
    echo "[dev] backend package not installed; running 'pip install -e aesthesis_app/'…"
    pip install -e aesthesis_app/ >/dev/null
fi

# ── Phase 2 capture pipeline bootstrap (D23) ────────────────────────────────
# Playwright's chromium binary is a one-time ~170-450MB download outside
# pip. Without it, BrowserUse exits at first launch with "Executable
# doesn't exist". We probe via Python's playwright registry rather than
# `find` because the cache path varies by OS (Linux ~/.cache/ms-playwright,
# Windows %LOCALAPPDATA%\ms-playwright, macOS ~/Library/Caches/ms-playwright).
if ! python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('playwright') else 1)" 2>/dev/null; then
    echo "[dev] playwright python package not installed (Phase 2 capture path will fail); already pinned in requirements-app.txt — re-run 'pip install -e aesthesis_app/'" >&2
fi
# Detect chromium binary by asking playwright's CLI. If it can't find one,
# auto-install. ~170-450MB download, one time per OS profile.
if python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('playwright') else 1)" 2>/dev/null; then
    if ! python -m playwright install --dry-run chromium 2>/dev/null | grep -q "is already installed"; then
        if ! python -c "from playwright.sync_api import sync_playwright; pw=sync_playwright().start(); pw.chromium.executable_path; pw.stop()" 2>/dev/null; then
            echo "[dev] playwright Chromium binary missing; running 'python -m playwright install chromium' (~170-450MB)…"
            python -m playwright install chromium
        fi
    fi
fi

# ── ffmpeg sanity (Phase 2 needs it for screencast -> H.264 MP4) ────────────
# browser_agent.py falls back to imageio_ffmpeg's bundled binary if system
# ffmpeg isn't on PATH, so this is a soft warning rather than a hard stop.
if ! command -v ffmpeg >/dev/null 2>&1; then
    if python -c "import imageio_ffmpeg" 2>/dev/null; then
        echo "[dev] system ffmpeg not on PATH — capture will fall back to imageio_ffmpeg bundled binary"
    else
        echo "[dev] WARNING: neither system ffmpeg nor imageio_ffmpeg available — Phase 2 capture (URL -> MP4) will FAIL LOUDLY at runtime" >&2
    fi
fi

if [[ ! -d aesthesis-app/node_modules ]]; then
    echo "[dev] frontend deps missing; running 'npm install'…"
    (cd aesthesis-app && npm install)
fi

# Refuse to start if either port is already in use. Avoids the confusing
# "frontend silently exits because :3000 is taken" failure mode (Next.js
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
