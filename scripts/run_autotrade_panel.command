#!/bin/zsh
# V2 — double-click launcher for the SIMPLE autotrade operator dashboard
# (phase3.autotrade.auto_panel).
#
# This is the day-to-day, read-mostly monitor for the continuous full-auto
# trader: today's fire status in plain Korean, a ±15-day fire calendar,
# portfolio return, and a few control buttons (STOP / Clear-halt / standing
# arm on-off / open logs / open advanced panel).
#
# Safety contract (same as the advanced launcher):
#   * Loads only identity/secret keys from .env so the UI + subprocesses can
#     authenticate against KIS.
#   * NEVER auto-sets danger gates (KIS_PAPER_SUBMIT_OK / KIS_PAPER_CANCEL_OK
#     / AUTOTRADE_T10_APPLY_OK / KIS_CONFIRM_LIVE).
#   * Do NOT bake credentials, account ids, or danger gates into this file.

set -e

REPO_ROOT="/Users/shin-il/PyCharmMiscProject/0316-"
cd "$REPO_ROOT"

export PYTHONPATH="."

PYTHON_BIN="${PYTHON_BIN:-python3}"

# ── Auto-load identity / secret keys from .env ─────────────────────────
_ALLOW_AUTO=("KIS_APP_KEY" "KIS_APP_SECRET" "KIS_ACCOUNT_NO"
             "KIS_ACCOUNT_PRODUCT_CODE" "KIS_ENV"
             "KIS_TOKEN_CACHE_PATH" "KIS_LOG_DIR"
             "GMAIL_APP_PASSWORD")
# The danger gates — explicitly NEVER auto-loaded.
_DENY_AUTO=("KIS_PAPER_SUBMIT_OK" "KIS_PAPER_CANCEL_OK"
            "AUTOTRADE_T10_APPLY_OK" "KIS_CONFIRM_LIVE")

if [[ -f .env ]]; then
    echo "[launcher] loading identity keys from .env (danger gates skipped)"
    while IFS= read -r line; do
        [[ -z "${line//[[:space:]]/}" || "${line:0:1}" == "#" ]] && continue
        key="${line%%=*}"
        key="${key//[[:space:]]/}"
        val="${line#*=}"
        val="${val%\"}"; val="${val#\"}"
        val="${val%\'}"; val="${val#\'}"
        deny=0
        for d in "${_DENY_AUTO[@]}"; do [[ "$key" == "$d" ]] && deny=1; done
        if [[ $deny -eq 1 ]]; then
            echo "[launcher]   skip danger gate: $key (export manually if needed)"
            continue
        fi
        allow=0
        for a in "${_ALLOW_AUTO[@]}"; do [[ "$key" == "$a" ]] && allow=1; done
        if [[ $allow -eq 1 ]]; then
            export "$key=$val"
        fi
    done < .env
else
    echo "[launcher] .env not found at $REPO_ROOT/.env — UI will show KIS_ENV=(unset)"
fi

echo "[launcher] repo:    $REPO_ROOT"
echo "[launcher] python:  $($PYTHON_BIN --version 2>&1)"
echo "[launcher] KIS_ENV: ${KIS_ENV:-(unset)}"
echo "[launcher] starting phase3.autotrade.auto_panel (simple dashboard) …"

exec "$PYTHON_BIN" -m phase3.autotrade.auto_panel
