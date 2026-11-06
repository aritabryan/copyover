#!/usr/bin/env bash
# Installs CopyOver on Fedora (or any Linux with systemd), in one line:
#   curl -fsSL https://raw.githubusercontent.com/aritabryan/copyover/main/install.sh | bash
# or, from a copy of the repository:  ./install.sh
# Each step is one line with a spinner or a progress bar. What the commands print goes to a log that is
# only shown when something fails. Texts are in Spanish when the session is, in English otherwise.
set -euo pipefail

REPO_GH="aritabryan/copyover"
REPO="https://github.com/$REPO_GH.git"
DEST="${COPYOVER_DIR:-$HOME/copyover}"
LOG_FILE="$(mktemp -t copyover-install.XXXXXX)"

# ---------- texts ----------
case "${LC_ALL:-${LANG:-}}" in es*) ES=1 ;; *) ES=0 ;; esac
t() { if [ "$ES" = 1 ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }  # t <English> <Spanish>

# ---------- look ----------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    TTY=1; N=$'\033[0m'; B=$'\033[1m'; D=$'\033[2m'; V=$'\033[32m'; R=$'\033[31m'; A=$'\033[36m'
else
    TTY=0; N=''; B=''; D=''; V=''; R=''; A=''
fi
case "${LC_ALL:-${LANG:-}}" in
    *[Uu][Tt][Ff]*) FULL='█'; EMPTY='░'; OK='✔'; BAD='✖'; DOT='●'; SPIN_FRAMES='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' ;;
    *)              FULL='#'; EMPTY='-'; OK='+'; BAD='x'; DOT='*'; SPIN_FRAMES='|/-\' ;;
esac
WIDTH=26
SPINNER=''
LABEL=''

clear_line() { if [ "$TTY" = 1 ]; then printf '\r\033[K'; fi; }

start_step() {  # <label>: a line with a spinner while the step runs
    LABEL="$1"
    if [ "$TTY" = 1 ]; then
        ( i=0; while :; do printf '\r  %s%s%s %s' "$A" "${SPIN_FRAMES:i++%${#SPIN_FRAMES}:1}" "$N" "$LABEL"; sleep 0.1; done ) &
        SPINNER=$!
    else
        printf '  - %s\n' "$LABEL"
    fi
}

stop_spinner() {
    if [ -n "$SPINNER" ]; then
        kill "$SPINNER" 2>/dev/null || true
        wait "$SPINNER" 2>/dev/null || true
        SPINNER=''
        clear_line
    fi
}

finish_step() {  # ok|failed [detail]: the step's final line
    stop_spinner
    clear_line
    if [ "$1" = ok ]; then
        printf '  %s%s%s %s%s\n' "$V" "$OK" "$N" "$LABEL" "${2:+  $D$2$N}"
    else
        printf '  %s%s%s %s\n' "$R" "$BAD" "$N" "$LABEL"
    fi
}

bar() {  # <label> <percent>: a progress bar on the current line
    local pct=$2 filled b='' i
    [ "$pct" -gt 100 ] && pct=100
    filled=$(( pct * WIDTH / 100 ))
    for ((i = 0; i < WIDTH; i++)); do
        if [ "$i" -lt "$filled" ]; then b+="$FULL"; else b+="$EMPTY"; fi
    done
    if [ "$TTY" = 1 ]; then
        printf '\r\033[K  %s%s%s %s  %s%s%s %3d%%' "$A" "$DOT" "$N" "$1" "$A" "$b" "$N" "$pct"
    fi
}

fail() {  # <message>: shows the end of the log and stops
    stop_spinner
    printf '\n  %s%s%s %s\n' "$R" "Error:" "$N" "$*" >&2
    if [ -s "$LOG_FILE" ]; then
        printf '\n  %s%s%s\n' "$D" "$(t 'Last lines of the log:' 'Últimas líneas del registro:')" "$N" >&2
        tail -n 15 "$LOG_FILE" | sed 's/^/    /' >&2
        printf '  %s%s %s%s\n' "$D" "$(t 'Full log:' 'Registro completo:')" "$LOG_FILE" "$N" >&2
    fi
    exit 1
}

run() {  # <label> <command...>: runs it behind a spinner, its output in the log
    start_step "$1"; shift
    if "$@" >>"$LOG_FILE" 2>&1; then
        finish_step ok
    else
        finish_step failed
        fail "$(t 'failed:' 'falló:') $*"
    fi
}

trap 'stop_spinner' EXIT
trap 'stop_spinner; printf "\n"; exit 130' INT TERM

printf '\n  %sCopyOver%s\n' "$B" "$N"
printf '  %s%s%s\n\n' "$D" "$(t 'Your clipboard on every device' 'Tu portapapeles en todos tus equipos')" "$N"

# 1. Python 3.10 or newer
LABEL="Python"
command -v python3 >/dev/null || { finish_step failed; fail "$(t 'python3 is missing.' 'falta python3.')"; }
python3 - <<'PY' || { finish_step failed; fail "$(t 'CopyOver needs Python 3.10 or newer.' 'CopyOver necesita Python 3.10 o superior.')"; }
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
finish_step ok "$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

# 2. System packages: git, clipboard (wl-clipboard on Wayland, xclip on X11), notifications and Tk for the
#    dialogs. Names for dnf (Fedora) or apt (Ubuntu, Debian, Mint); apt also needs venv and the tray library.
if command -v dnf >/dev/null; then PKG_MANAGER=dnf
elif command -v apt-get >/dev/null; then PKG_MANAGER=apt
else PKG_MANAGER=''
fi
pkg_name() {  # <dnf name> <apt name>
    if [ "$PKG_MANAGER" = apt ]; then printf '%s' "$2"; else printf '%s' "$1"; fi
}
missing=()
command -v git >/dev/null || missing+=(git)
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    command -v wl-copy >/dev/null || missing+=(wl-clipboard)
else
    command -v xclip >/dev/null || command -v wl-copy >/dev/null || missing+=(xclip)
fi
command -v notify-send >/dev/null || missing+=("$(pkg_name libnotify libnotify-bin)")
python3 -c "import gi" 2>/dev/null || missing+=("$(pkg_name python3-gobject python3-gi)")
python3 -c "import tkinter" 2>/dev/null || missing+=("$(pkg_name python3-tkinter python3-tk)")
if [ "$PKG_MANAGER" = apt ]; then
    python3 -c "import ensurepip" 2>/dev/null || missing+=(python3-venv)
    python3 -c "import gi; gi.require_version('AyatanaAppIndicator3', '0.1')" 2>/dev/null \
        || missing+=(gir1.2-ayatanaappindicator3-0.1)
fi

LABEL="$(t 'System packages' 'Paquetes del sistema')"
if [ ${#missing[@]} -eq 0 ]; then
    finish_step ok "$(t 'already installed' 'ya estaban instalados')"
elif [ -n "$PKG_MANAGER" ]; then
    # The password is asked before the spinner starts, so the prompt isn't drawn over. If sudo still
    # remembers it, nothing is printed.
    sudo -v -p "  $(t 'Password to install system packages: ' 'Contraseña para instalar paquetes del sistema: ')" || fail "$(t 'no permission to install packages.' 'sin permiso para instalar paquetes.')"
    pkgs_label="$LABEL  ${D}${missing[*]}${N}"
    if [ "$PKG_MANAGER" = dnf ]; then
        run "$pkgs_label" sudo dnf install -y "${missing[@]}"
    else
        run "$(t 'Package list (apt)' 'Lista de paquetes (apt)')" sudo apt-get update
        run "$pkgs_label" sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
    fi
else
    finish_step failed
    fail "$(t "install these packages with your distribution's package manager and run the script again: ${missing[*]}" \
              "instala estos paquetes con el gestor de tu distribución y vuelve a correr el script: ${missing[*]}")"
fi

# 3. The code: the current copy if the script runs inside it; otherwise it's cloned or updated
clone_repo() {  # git clone with a progress bar: «Receiving objects» fills up to 90 %, «Resolving deltas» the rest
    local label="$1"
    bar "$label" 0
    if git clone --depth 1 --progress "$REPO" "$DEST" 2>&1 | tee -a "$LOG_FILE" | tr '\r' '\n' | {
        while IFS= read -r l; do
            case "$l" in
                *"Receiving objects:"*)
                    p=$(printf '%s' "$l" | sed -n 's/.*Receiving objects: *\([0-9]*\)%.*/\1/p')
                    [ -n "$p" ] && bar "$label" $(( p * 90 / 100 )) ;;
                *"Resolving deltas:"*)
                    p=$(printf '%s' "$l" | sed -n 's/.*Resolving deltas: *\([0-9]*\)%.*/\1/p')
                    [ -n "$p" ] && bar "$label" $(( 90 + p / 10 )) ;;
            esac
        done
        true
    }; then
        bar "$label" 100; LABEL="$label"; finish_step ok "$DEST"
    else
        LABEL="$label"; finish_step failed
        fail "$(t 'could not download CopyOver. If the repository is private, sign in to GitHub (sudo dnf install gh && gh auth login) and run the script again.' \
                  'no se pudo descargar CopyOver. Si el repositorio es privado, inicia sesión en GitHub (sudo dnf install gh && gh auth login) y vuelve a correr el script.')"
    fi
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$HERE" ] && [ -f "$HERE/copyover.py" ]; then
    DEST="$HERE"
    LABEL="$(t 'CopyOver code' 'Código de CopyOver')"; finish_step ok "$DEST"
elif [ -d "$DEST/.git" ]; then
    run "$(t 'Updating CopyOver' 'Actualizando CopyOver')" git -C "$DEST" pull --ff-only
elif command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    # With a GitHub session (gh auth login) it also works while the repository is private.
    run "$(t 'Downloading CopyOver' 'Descargando CopyOver')" gh repo clone "$REPO_GH" "$DEST" -- --depth 1
else
    # No password prompt: GitHub no longer accepts them and a private repository would only ask in vain.
    export GIT_TERMINAL_PROMPT=0
    clone_repo "$(t 'Downloading CopyOver' 'Descargando CopyOver')"
fi
cd "$DEST"

# 4. Virtual environment: Fedora doesn't ship pip for the system Python.
#    --system-site-packages allows PyGObject, which the tray icon needs.
if [ ! -x .venv/bin/python ]; then
    run "$(t 'Python environment (.venv)' 'Entorno de Python (.venv)')" python3 -m venv --system-site-packages .venv
fi
run "$(t 'Preparing pip' 'Preparando pip')" .venv/bin/python -m pip install -q --upgrade pip

install_libraries() {  # pip install with a progress bar: one step per package pip collects, dependencies included
    local label="$1" total
    # requirements.txt lists the direct ones; with their dependencies pip collects about twice as many.
    total=$(grep -c '^[^#[:space:]]' requirements.txt)
    bar "$label" 0
    if .venv/bin/python -m pip install --progress-bar off -r requirements.txt 2>&1 | tee -a "$LOG_FILE" | {
        done=0
        while IFS= read -r l; do
            case "$l" in
                Collecting*|"Requirement already satisfied"*)
                    done=$(( done + 1 ))
                    p=$(( done * 100 / (total * 2) )); [ "$p" -gt 95 ] && p=95
                    bar "$label" "$p" ;;
                "Installing collected packages"*) bar "$label" 97 ;;
            esac
        done
        true
    }; then
        bar "$label" 100; LABEL="$label"; finish_step ok
    else
        LABEL="$label"; finish_step failed
        fail "$(t 'could not install the Python libraries.' 'no se pudieron instalar las librerías de Python.')"
    fi
}
install_libraries "$(t 'Python libraries' 'Librerías de Python')"

# 5. Start at login (systemd user service) and «Send with CopyOver» in Nautilus/Dolphin
run "$(t 'Shortcuts, start at login and right-click menu' 'Accesos directos, arranque al iniciar sesión y clic derecho')" .venv/bin/python copyover.py --install

rm -f "$LOG_FILE"
printf '\n  %s%s%s %s%s%s\n' "$V" "$OK" "$N" "$B" "$(t 'Done. CopyOver is running.' 'Listo. CopyOver ya está corriendo.')" "$N"
printf '    %s\n\n' "$(t 'You will find it in the applications menu, in the dock and on the desktop.' 'Lo encuentras en el menú de aplicaciones, en el dock y en el escritorio.')"
printf '  %s%s%s\n    %s\n' "$D" "$(t 'Address and key for the shortcuts:' 'Dirección y clave para los atajos:')" "$N" "$DEST/.venv/bin/python $DEST/copyover.py --info"
printf '  %s%s%s\n    %s\n' "$D" "$(t 'Follow the log:' 'Ver el registro:')" "$N" "journalctl --user -u copyover -f"
printf '  %s%s%s\n    %s\n' "$D" "$(t 'Uninstall:' 'Desinstalar:')" "$N" "$DEST/.venv/bin/python $DEST/copyover.py --uninstall && rm -rf $DEST"
printf '\n  %s%s%s\n\n' "$D" "$(t 'On GNOME, the tray icon needs the AppIndicator extension.' 'En GNOME, el ícono de la bandeja necesita la extensión AppIndicator.')" "$N"
