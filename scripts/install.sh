#!/bin/bash
# annealr install script
#
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail
export LC_ALL=C

PLUGIN_NAME="annealr"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_PATH="${REPO_DIR}/src/${PLUGIN_NAME}"

DEFAULT_KLIPPER_DIR="${HOME}/klipper"
DEFAULT_KLIPPY_ENV="${HOME}/klippy-env"
DEFAULT_PRINTER_DATA="${HOME}/printer_data"

KLIPPER_DIR="${DEFAULT_KLIPPER_DIR}"
KLIPPY_ENV="${DEFAULT_KLIPPY_ENV}"
PRINTER_DATA="${DEFAULT_PRINTER_DATA}"

INSTALL_KS=false
UNINSTALL=false

# ── Helpers ───────────────────────────────────────────────────────

function msg_info  { printf "[INFO]    %s\n" "$1"; }
function msg_ok    { printf "[OK]      %s\n" "$1"; }
function msg_warn  { printf "[WARN]    %s\n" "$1"; }
function msg_error { printf "[ERROR]   %s\n" "$1"; }

function display_help {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -k, --klipper <dir>       Klipper directory (default: ${DEFAULT_KLIPPER_DIR})"
    echo "  -e, --klippy-env <dir>    Klippy venv directory (default: ${DEFAULT_KLIPPY_ENV})"
    echo "  -d, --printer-data <dir>  Printer data directory (default: ${DEFAULT_PRINTER_DATA})"
    echo "  --klipperscreen           Also install KlipperScreen menu config"
    echo "  --uninstall               Remove the plugin"
    echo "  --help                    Show this help message"
    exit 0
}

# ── Argument parsing ──────────────────────────────────────────────

function parse_args {
    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            -k|--klipper)       KLIPPER_DIR="$2"; shift 2 ;;
            -e|--klippy-env)    KLIPPY_ENV="$2";  shift 2 ;;
            -d|--printer-data)  PRINTER_DATA="$2"; shift 2 ;;
            --klipperscreen)    INSTALL_KS=true;   shift   ;;
            --uninstall)        UNINSTALL=true;    shift   ;;
            --help)             display_help ;;
            *) msg_error "Unknown option: $1"; display_help ;;
        esac
    done
}

# ── Pre-flight checks ─────────────────────────────────────────────

function preflight_checks {
    if [ "${EUID}" -eq 0 ]; then
        msg_error "This script must not be run as root!"
        exit 1
    fi

    if [ ! -d "${KLIPPER_DIR}/klippy/extras" ]; then
        msg_error "Klipper not found at '${KLIPPER_DIR}'. Use -k to specify the path."
        exit 1
    fi

    if [ ! -d "${KLIPPY_ENV}" ]; then
        msg_error "Klippy venv not found at '${KLIPPY_ENV}'. Use -e to specify the path."
        exit 1
    fi

    if [ ! -d "${SOURCE_PATH}" ]; then
        msg_error "Plugin source not found at '${SOURCE_PATH}'"
        exit 1
    fi

    # Detect service name
    if systemctl list-unit-files --quiet klipper.service; then
        SERVICE_NAME="klipper"
    else
        msg_error "Klipper service not found. Please install Klipper/Kalico first."
        exit 1
    fi

    msg_ok "Found ${SERVICE_NAME} at ${KLIPPER_DIR}"

    # Python version check
    PY_MINOR=$("${KLIPPY_ENV}/bin/python" -c "import sys; print(sys.version_info[1])" 2>/dev/null || echo "0")
    if [ "${PY_MINOR}" -lt 9 ]; then
        msg_warn "Python 3.${PY_MINOR} detected. annealr targets Python 3.9+."
        msg_warn "Consider upgrading to Raspberry Pi OS Bookworm."
    else
        PY_VER=$("${KLIPPY_ENV}/bin/python" -c "import sys; print('%d.%d' % sys.version_info[:2])")
        msg_ok "Python ${PY_VER} in klippy-env"
    fi
}

# ── Service control ───────────────────────────────────────────────

function stop_service  { msg_info "Stopping ${SERVICE_NAME}...";  sudo systemctl stop  "${SERVICE_NAME}" || true; }
function start_service { msg_info "Starting ${SERVICE_NAME}...";  sudo systemctl start "${SERVICE_NAME}" || true; }

# ── Install ───────────────────────────────────────────────────────

function link_module {
    local extras_dir="${KLIPPER_DIR}/klippy/extras"
    local exclude_file="${KLIPPER_DIR}/.git/info/exclude"

    # Link 1: annealr/ package directory
    local pkg_symlink="${extras_dir}/${PLUGIN_NAME}"
    if [ -L "${pkg_symlink}" ]; then
        rm "${pkg_symlink}"
    elif [ -e "${pkg_symlink}" ]; then
        msg_error "${pkg_symlink} exists but is not a symlink. Remove it manually."
        exit 1
    fi
    ln -frsn "${SOURCE_PATH}" "${pkg_symlink}"
    msg_ok "Linked: ${pkg_symlink} -> ${SOURCE_PATH}"

    # Link 2: annealr_profile.py (handles [annealr_profile <name>] sections)
    local profile_src="${REPO_DIR}/src/annealr_profile.py"
    local profile_symlink="${extras_dir}/annealr_profile.py"
    if [ -L "${profile_symlink}" ]; then
        rm "${profile_symlink}"
    elif [ -e "${profile_symlink}" ]; then
        msg_error "${profile_symlink} exists but is not a symlink. Remove it manually."
        exit 1
    fi
    ln -frsn "${profile_src}" "${profile_symlink}"
    msg_ok "Linked: ${profile_symlink} -> ${profile_src}"

    # Prevent Klipper marking its own repo as dirty
    if [ -f "${exclude_file}" ]; then
        for rel_path in "klippy/extras/${PLUGIN_NAME}" "klippy/extras/annealr_profile.py"; do
            if ! grep -qF "${rel_path}" "${exclude_file}"; then
                echo "${rel_path}" >> "${exclude_file}"
                msg_ok "Added '${rel_path}' to git exclude"
            fi
        done
    fi
}

function add_moonraker_updater {
    local moonraker_conf="${PRINTER_DATA}/config/moonraker.conf"

    if [ ! -f "${moonraker_conf}" ]; then
        msg_warn "moonraker.conf not found at ${moonraker_conf}, skipping update manager."
        return
    fi

    if grep -q "\[update_manager annealr\]" "${moonraker_conf}"; then
        msg_info "Moonraker update manager already configured, skipping."
        return
    fi

    msg_info "Adding update manager to moonraker.conf..."
    cat <<EOF >> "${moonraker_conf}"

## annealr automatic update management
[update_manager annealr]
type: git_repo
path: ${REPO_DIR}
origin: https://github.com/YOUR_USERNAME/annealr.git
managed_services: ${SERVICE_NAME}
primary_branch: main
install_script: scripts/install.sh
EOF
    msg_ok "Added [update_manager annealr] to moonraker.conf"
    msg_warn "Remember to update the 'origin' URL in moonraker.conf with your actual repo URL."
}

function install_klipperscreen {
    local ks_dir="${HOME}/KlipperScreen"
    local ks_conf="${PRINTER_DATA}/config/KlipperScreen.conf"
    local menu_src="${REPO_DIR}/klipperscreen/KlipperScreen_anneal_menu.conf"
    local panel_src="${REPO_DIR}/klipperscreen/panels/annealr.py"

    if [ ! -d "${ks_dir}" ]; then
        msg_warn "KlipperScreen not found at ${ks_dir}, skipping."
        return
    fi

    # Custom panel symlink (Path 2 — added when panel is implemented)
    if [ -f "${panel_src}" ]; then
        local panel_link="${ks_dir}/panels/annealr.py"
        if [ -L "${panel_link}" ]; then
            rm "${panel_link}"
        elif [ -e "${panel_link}" ]; then
            msg_warn "${panel_link} exists but is not a symlink. Skipping panel install."
        fi
        if [ ! -e "${panel_link}" ]; then
            ln -frsn "${panel_src}" "${panel_link}"
            msg_ok "Linked KlipperScreen panel: ${panel_link}"
        fi
    fi

    # Menu config
    if [ -f "${menu_src}" ]; then
        if [ ! -f "${ks_conf}" ]; then
            cp "${menu_src}" "${ks_conf}"
            msg_ok "Created KlipperScreen.conf with annealr menu"
        elif grep -q "menu __main annealr" "${ks_conf}"; then
            msg_info "Annealr menu already present in KlipperScreen.conf, skipping."
        else
            printf "\n# -- annealr menu (added by install.sh) --\n" >> "${ks_conf}"
            cat "${menu_src}" >> "${ks_conf}"
            msg_ok "Appended annealr menu to KlipperScreen.conf"
        fi
    fi

    msg_info "Restarting KlipperScreen..."
    sudo systemctl restart KlipperScreen 2>/dev/null || true
}

# ── Uninstall ─────────────────────────────────────────────────────

function unlink_module {
    local extras_dir="${KLIPPER_DIR}/klippy/extras"

    # Remove package symlink
    local pkg_symlink="${extras_dir}/${PLUGIN_NAME}"
    if [ -L "${pkg_symlink}" ]; then
        rm "${pkg_symlink}"
        msg_ok "Removed symlink: ${pkg_symlink}"
    else
        msg_info "No symlink found at ${pkg_symlink}, skipping."
    fi

    # Remove annealr_profile.py symlink
    local profile_symlink="${extras_dir}/annealr_profile.py"
    if [ -L "${profile_symlink}" ]; then
        rm "${profile_symlink}"
        msg_ok "Removed symlink: ${profile_symlink}"
    fi

    # Remove KlipperScreen panel symlink if present
    local panel_link="${HOME}/KlipperScreen/panels/annealr.py"
    if [ -L "${panel_link}" ]; then
        rm "${panel_link}"
        msg_ok "Removed KlipperScreen panel symlink"
    fi

    # Clean up git exclude entries
    local exclude_file="${KLIPPER_DIR}/.git/info/exclude"
    if [ -f "${exclude_file}" ]; then
        for rel_path in "klippy/extras/${PLUGIN_NAME}" "klippy/extras/annealr_profile.py"; do
            sed -i "\|^${rel_path}\$|d" "${exclude_file}" 2>/dev/null || true
        done
        msg_ok "Removed git exclude entries"
    fi
}

# ── Main ──────────────────────────────────────────────────────────

printf "\n=============================================\n"
printf " annealr install script\n"
printf "=============================================\n\n"

parse_args "$@"
preflight_checks
stop_service

if [ "${UNINSTALL}" = true ]; then
    msg_info "Uninstalling annealr..."
    unlink_module
    start_service
    sudo systemctl restart moonraker 2>/dev/null || true
    printf "\n"
    msg_ok "Uninstall complete."
    echo ""
    echo "Remember to remove from your config files:"
    echo "  - [annealr] section from printer.cfg"
    echo "  - Any [annealr_profile ...] sections from printer.cfg"
    echo "  - [update_manager annealr] from moonraker.conf"
    echo "  - Annealr menu entries from KlipperScreen.conf (if added)"
else
    msg_info "Installing annealr..."
    link_module
    add_moonraker_updater
    if [ "${INSTALL_KS}" = true ]; then
        install_klipperscreen
    fi
    start_service
    sudo systemctl restart moonraker 2>/dev/null || true

    printf "\n"
    msg_ok "Installation complete."
    echo ""
    echo "Next steps:"
    echo "  1. Add to your printer.cfg:"
    echo ""
    echo "       [annealr]"
    echo "       heater: annealer"
    echo ""
    if [ "${INSTALL_KS}" = false ]; then
        echo "  2. For KlipperScreen integration, re-run with --klipperscreen"
        echo ""
    fi
    echo "  3. Restart ${SERVICE_NAME}:"
    echo "       sudo systemctl restart ${SERVICE_NAME}"
fi
