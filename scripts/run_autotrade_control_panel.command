#!/bin/zsh
# R10-3 / R10-fix / R10D — double-click launcher for the autotrade
# operator control panel.
#
# Safety contract:
#   * This script loads identity/secret keys from .env so the UI's
#     KIS_ENV gate stops reading "(unset)" and so the daily_runner
#     subprocess can authenticate against KIS.
#   * It does NOT auto-set any of the danger gates
#     (KIS_PAPER_SUBMIT_OK / KIS_PAPER_CANCEL_OK / AUTOTRADE_T10_APPLY_OK /
#      KIS_CONFIRM_LIVE) even if they happen to be present in .env.
#
# Arming the danger gates is done IN-UI via the "Activation (this
# session)" toggles added in R10-ARM. After launching the panel:
#   1. tick `Arm Paper Submit gate`   (sets KIS_PAPER_SUBMIT_OK /
#                                       KIS_PAPER_CANCEL_OK for this
#                                       process only)
#   2. tick `Arm T10 Apply gate`      (sets AUTOTRADE_T10_APPLY_OK)
#
# The previous terminal-export recipe is still supported (just export
# the var before launching) but is no longer the recommended path.
#
# Do NOT bake credentials, account ids, or danger gates into this file.

set -e

REPO_ROOT="/Users/shin-il/PyCharmMiscProject/0316-"
cd "$REPO_ROOT"

export PYTHONPATH="."

PYTHON_BIN="${PYTHON_BIN:-python3}"

# ── Auto-load identity / secret keys from .env ─────────────────────────
# These are the keys the UI / daily_runner / KIS broker adapter need to
# *function*. They are NOT the ones that authorize order submission.
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
        # Skip blanks and comments.
        [[ -z "${line//[[:space:]]/}" || "${line:0:1}" == "#" ]] && continue
        key="${line%%=*}"
        key="${key//[[:space:]]/}"
        val="${line#*=}"
        # Strip surrounding single or double quotes if present.
        val="${val%\"}"; val="${val#\"}"
        val="${val%\'}"; val="${val#\'}"
        # Hard-deny danger gates regardless of file content.
        deny=0
        for d in "${_DENY_AUTO[@]}"; do [[ "$key" == "$d" ]] && deny=1; done
        if [[ $deny -eq 1 ]]; then
            echo "[launcher]   skip danger gate: $key (export manually if needed)"
            continue
        fi
        # Only export keys on the allow-list. Anything else stays out of
        # the UI process environment so .env additions don't silently
        # change UI behaviour.
        allow=0
        for a in "${_ALLOW_AUTO[@]}"; do [[ "$key" == "$a" ]] && allow=1; done
        if [[ $allow -eq 1 ]]; then
            export "$key=$val"
        fi
    done < .env
else
    echo "[launcher] .env not found at $REPO_ROOT/.env — UI will show KIS_ENV=(unset)"
fi

echo "[launcher] repo:                       $REPO_ROOT"
echo "[launcher] python:                     $($PYTHON_BIN --version 2>&1)"
echo "[launcher] KIS_ENV:                    ${KIS_ENV:-(unset)}"
echo "[launcher] KIS_PAPER_SUBMIT_OK:        ${KIS_PAPER_SUBMIT_OK:-(unset — required for Paper Submit)}"
echo "[launcher] KIS_PAPER_CANCEL_OK:        ${KIS_PAPER_CANCEL_OK:-(unset — required for cancel path)}"
echo "[launcher] AUTOTRADE_T10_APPLY_OK:     ${AUTOTRADE_T10_APPLY_OK:-(unset — required for T10 Apply Real)}"
echo "[launcher] starting phase3.autotrade.control_panel …"

exec "$PYTHON_BIN" -m phase3.autotrade.control_panel
