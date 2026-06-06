#!/usr/bin/env bash
# V1-E.4 — Install / uninstall / status helper for the V1 launchd agent.
#
# Usage:
#   ./install_v1.sh install      # render template + load via launchctl
#   ./install_v1.sh uninstall    # bootout + remove the plist
#   ./install_v1.sh status       # print load state + next-fire estimate
#   ./install_v1.sh test-fire    # one-off manual fire (without launchd)
#
# Why a shell wrapper and not a Python installer
# ----------------------------------------------
# launchctl is the canonical macOS interface for LaunchAgents, and
# the install/uninstall flow is a handful of `cp` + `launchctl` calls.
# A shell wrapper keeps the bootstrap zero-dependency — you can run
# it from a fresh shell with no virtualenv, before the runtime
# pip install has even happened.
#
# The script is deliberately STRICT (set -euo pipefail) so a failed
# substitution or missing prereq aborts the install cleanly instead
# of leaving a half-installed plist behind.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
RUNTIME_DIR="${REPO_ROOT}/phase3/autotrade/runtime"

# V1-F: two agents installed together.
#   * t7    — 09:00 prefetch (T7 + own email; cache fresh while US is closed)
#   * trade — 22:35 trade (intent + paper_submit + apply; reuses the morning T7 run)
# Each entry is "label|template_basename".
AGENTS=(
    "com.autotrade.v1.t7|com.autotrade.v1.t7.plist.template"
    "com.autotrade.v1.daily|com.autotrade.v1.daily.plist.template"
)

target_plist_for_label() {
    printf '%s/%s.plist\n' "${LAUNCH_AGENTS_DIR}" "$1"
}
template_path_for_basename() {
    printf '%s/%s\n' "${HERE}" "$1"
}

log()  { printf '[install_v1] %s\n' "$*" ; }
die()  { printf '[install_v1][ERROR] %s\n' "$*" >&2 ; exit 1 ; }

require_macos() {
    [[ "$(uname -s)" == "Darwin" ]] \
        || die "launchd is macOS-only (uname=$(uname -s))"
}

require_python() {
    if [[ -n "${PYTHON:-}" ]] ; then
        [[ -x "${PYTHON}" ]] || die "PYTHON=${PYTHON} not executable"
        return
    fi
    # Probe in order: framework Python 3.13 (matches dev), `python3` on PATH.
    for c in \
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
        /opt/homebrew/bin/python3 \
        /usr/local/bin/python3 \
        "$(command -v python3 2>/dev/null || true)" ; do
        if [[ -n "${c:-}" && -x "${c}" ]] ; then
            PYTHON="${c}" ; return
        fi
    done
    die "no python3 found; set PYTHON=... explicitly"
}

warn_timezone() {
    # launchd's StartCalendarInterval reads from the Mac's *local*
    # tz; the plists embed 09:00 (T7) and 22:35 (trade), so the
    # Mac MUST be in KST for the schedule to mean what the operator
    # thinks it means.
    local tz
    tz="$(systemsetup -gettimezone 2>/dev/null | awk -F': ' '{print $2}')"
    if [[ "${tz}" != "Asia/Seoul" && "${tz}" != "Asia/Pyongyang" ]] ; then
        log "WARNING: local timezone is '${tz}', not Asia/Seoul."
        log "         launchd fires at 07:20 / 22:35 LOCAL — your"
        log "         schedule will drift vs KST. Adjust the plist"
        log "         Hour/Minute manually if a non-KST schedule"
        log "         is what you actually want."
    fi
}

require_env_for_runtime() {
    # The launchd gui session does NOT inherit ~/.zshrc. v1_runner
    # hydrates os.environ from ``${REPO_ROOT}/.env`` at startup, so
    # any key listed below MUST exist either in this shell (so the
    # operator catches the gap NOW) or in .env (so 22:35 picks it
    # up). We check both — only warn if the secret is missing from
    # BOTH places, since that's the only state where the launchd
    # fire would actually halt.
    #
    # GMAIL_APP_PASSWORD has a third fallback (config.local.yaml's
    # ``email.gmail_app_password`` — read by smtp_mailer), so we
    # don't warn on it here even if both shell + .env lack it.
    local dotenv="${REPO_ROOT}/.env"
    local has_dotenv=false
    [[ -f "${dotenv}" ]] && has_dotenv=true

    local check_in_dotenv
    check_in_dotenv() {
        ${has_dotenv} || return 1
        grep -q "^${1}=" "${dotenv}"
    }

    local missing=()
    # KIS canonical names (NOT the ``KIS_PAPER_*`` strawman — the
    # adapter reads ``KIS_APP_KEY``/``KIS_APP_SECRET``/``KIS_ACCOUNT_NO``
    # /``KIS_ACCOUNT_PRODUCT_CODE`` regardless of paper/live env).
    for v in FMP_API_KEY \
              KIS_APP_KEY KIS_APP_SECRET \
              KIS_ACCOUNT_NO KIS_ACCOUNT_PRODUCT_CODE ; do
        if [[ -z "${!v:-}" ]] && ! check_in_dotenv "${v}" ; then
            missing+=("${v}")
        fi
    done

    if (( ${#missing[@]} > 0 )) ; then
        log "WARNING: the following keys are missing from BOTH this"
        log "         shell AND ${dotenv}:"
        for v in "${missing[@]}" ; do log "  - ${v}" ; done
        log "         The launchd 22:35 fire reads .env at startup"
        log "         (gui session does NOT inherit ~/.zshrc), so"
        log "         these MUST be added to .env or the run will"
        log "         halt at the first stage that needs them."
        log "         Append to .env (one per line):"
        log "             FMP_API_KEY=<your-key>"
        log "             KIS_APP_KEY=<your-key>"
        log "             ... etc."
    fi
}

render_plist_one() {
    local template="$1" target="$2"
    [[ -f "${template}" ]] || die "template missing: ${template}"
    # ``sed`` substitution. We use a delimiter unlikely to appear in
    # any path (``|``) and bail out loudly if either token survived
    # the rewrite, which would mean install would silently produce
    # a broken plist.
    sed \
        -e "s|__PYTHON__|${PYTHON}|g" \
        -e "s|__REPO_ROOT__|${REPO_ROOT}|g" \
        "${template}" > "${target}.tmp"
    if grep -q "__PYTHON__\|__REPO_ROOT__" "${target}.tmp" ; then
        rm -f "${target}.tmp"
        die "template substitution left placeholders unresolved: ${template}"
    fi
    mv "${target}.tmp" "${target}"
    log "wrote ${target}"
}

render_plists() {
    mkdir -p "${LAUNCH_AGENTS_DIR}"
    mkdir -p "${RUNTIME_DIR}"
    local entry label template_base
    for entry in "${AGENTS[@]}" ; do
        label="${entry%|*}"
        template_base="${entry#*|}"
        render_plist_one \
            "$(template_path_for_basename "${template_base}")" \
            "$(target_plist_for_label "${label}")"
    done
}

launchctl_load_all() {
    # Idempotent: bootout first (ignore error if not loaded yet),
    # then bootstrap. Done for every agent in AGENTS.
    local entry label target
    for entry in "${AGENTS[@]}" ; do
        label="${entry%|*}"
        target="$(target_plist_for_label "${label}")"
        launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "${target}"
        log "loaded into launchd as gui/$(id -u)/${label}"
    done
}

launchctl_unload_all() {
    local entry label target
    for entry in "${AGENTS[@]}" ; do
        label="${entry%|*}"
        target="$(target_plist_for_label "${label}")"
        if [[ -f "${target}" ]] ; then
            launchctl bootout "gui/$(id -u)/${label}" 2>/dev/null || true
            rm -f "${target}"
            log "removed ${target}"
        else
            log "no plist at ${target}; nothing to remove"
        fi
    done
}

cmd_install() {
    require_macos
    require_python
    warn_timezone
    require_env_for_runtime
    render_plists
    launchctl_load_all
    log "DONE. Daily schedule:"
    log "  07:20 KST — com.autotrade.v1.t7    (T7 prefetch + own email)"
    log "  22:35 KST — com.autotrade.v1.daily (trade fire; reuses morning T7)"
    log "To arm tonight: ${PYTHON} -m phase3.autotrade.v1_runner arm-today"
    log "To check      : ${PYTHON} -m phase3.autotrade.v1_runner status"
    log "To inspect    : launchctl print gui/$(id -u)/com.autotrade.v1.t7"
    log "                launchctl print gui/$(id -u)/com.autotrade.v1.daily"
    log ""
    log "NOTE: macOS may show a one-time 'python wants to make changes'"
    log "permission popup on the FIRST fire of each agent. Click Allow;"
    log "subsequent fires will not prompt."
}

cmd_uninstall() {
    launchctl_unload_all
}

cmd_status() {
    local entry label target installed_any=false
    for entry in "${AGENTS[@]}" ; do
        label="${entry%|*}"
        target="$(target_plist_for_label "${label}")"
        if [[ -f "${target}" ]] ; then
            log "plist present at ${target}"
            launchctl print "gui/$(id -u)/${label}" 2>/dev/null \
                | sed -n '1,30p'
            log "---"
            installed_any=true
        else
            log "plist NOT installed: ${target}"
        fi
    done
    ${installed_any} || return 1
}

cmd_test_fire() {
    # Run the V1 runner manually with the SAME env the launchd plist
    # will see — but with --no-arm so we don't depend on an arm token
    # being present right now. NEVER use this against a live broker.
    require_python
    log "test-fire (no arm gate, no real submit gates)"
    PYTHONPATH="${REPO_ROOT}" \
    KIS_ENV=paper \
    AUTOTRADE_V1_SUPPRESS_T7_MAIL=true \
    "${PYTHON}" -m phase3.autotrade.v1_runner run \
        --no-arm --no-mail --skip-t7 --run-id "${1:-}" \
        --output-dir "${2:-}"
}

main() {
    local cmd="${1:-}"
    case "${cmd}" in
        install)     shift ; cmd_install   "$@" ;;
        uninstall)   shift ; cmd_uninstall "$@" ;;
        status)      shift ; cmd_status    "$@" ;;
        test-fire)   shift ; cmd_test_fire "$@" ;;
        *)
            echo "usage: $0 {install|uninstall|status|test-fire}" >&2
            exit 2
            ;;
    esac
}

main "$@"
