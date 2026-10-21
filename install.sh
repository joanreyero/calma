#!/usr/bin/env bash
# Calma installer and upgrader. Idempotent: safe to run again at any time.
#
#   sudo ./install.sh            install or upgrade
#   sudo ./install.sh --plugin   also install the Omarchy bar plugin for your user
#   ./install.sh --destdir DIR   testing only: prefix every path with DIR, skip systemctl
#
# See ARCHITECTURE.md, section "Packaging", for the contract this script follows.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTDIR=""
WITH_PLUGIN=0
EXT_PATH="/usr/share/calma/extension"   # the real path; never prefixed inside config lines
PLUGIN_ID="io.github.joanreyero.calma"

usage() {
  sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --plugin) WITH_PLUGIN=1 ;;
    --destdir)
      [ $# -ge 2 ] || { echo "install.sh: --destdir needs a directory" >&2; exit 2; }
      DESTDIR="${2%/}"; shift ;;
    --destdir=*) DESTDIR="${1#--destdir=}"; DESTDIR="${DESTDIR%/}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$DESTDIR" ] && [ "$(id -u)" -ne 0 ]; then
  echo "Calma installs system-wide files and systemd units, so it needs root." >&2
  if [ "$WITH_PLUGIN" -eq 1 ]; then
    echo "Run it again as:  sudo $0 --plugin" >&2
  else
    echo "Run it again as:  sudo $0" >&2
  fi
  exit 1
fi

say()  { printf '  %s\n' "$*"; }
step() { printf '\n%s\n' "$*"; }

# Run systemctl only on a real install.
sysctl() {
  if [ -n "$DESTDIR" ]; then
    say "(skipped: systemctl $*)"
  else
    systemctl "$@"
  fi
}

# --- sanity: are we in a Calma checkout? -----------------------------------
for required in calma bin/calma bin/calmad extension packaging/calma.service \
                packaging/calma-watchdog.service packaging/calma-watchdog.timer; do
  if [ ! -e "$SRC/$required" ]; then
    echo "install.sh: $SRC/$required is missing; run this from a complete Calma checkout." >&2
    exit 1
  fi
done

# Paths (all prefixed by DESTDIR when testing).
LIB="$DESTDIR/usr/lib/calma"
BIN="$DESTDIR/usr/bin"
SHARE_EXT="$DESTDIR$EXT_PATH"
UNITS="$DESTDIR/etc/systemd/system"
ETC="$DESTDIR/etc/calma"
VAR="$DESTDIR/var/lib/calma"
FLAGS="$DESTDIR/etc/chromium-flags.conf"

UPGRADE=0
[ -e "$LIB/calmad" ] && UPGRADE=1

if [ "$UPGRADE" -eq 1 ]; then
  step "Upgrading Calma${DESTDIR:+ (into $DESTDIR)}"
else
  step "Installing Calma${DESTDIR:+ (into $DESTDIR)}"
fi

# --- copy a directory tree, replacing what was there ------------------------
# rsync --delete keeps the destination an exact mirror; without rsync we
# remove and copy, which is equivalent for these small trees.
sync_tree() {
  local from="$1" to="$2"
  install -d -m 0755 "$to"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' --exclude '.ruff_cache' \
      "$from/" "$to/"
  else
    rm -rf "$to"
    cp -a "$from" "$to"
    find "$to" \( -name '__pycache__' -o -name '*.pyc' \) -exec rm -rf {} + 2>/dev/null || true
  fi
  if [ -z "$DESTDIR" ]; then
    chown -R root:root "$to"
  fi
}

# --- 1. program files -------------------------------------------------------
step "Program files"
sync_tree "$SRC/calma" "$LIB/calma"
say "$LIB/calma/"
install -d -m 0755 "$BIN"
install -m 0755 "$SRC/bin/calma"  "$BIN/calma"
install -m 0755 "$SRC/bin/calmad" "$LIB/calmad"
say "$BIN/calma"
say "$LIB/calmad"

# --- 2. extension -------------------------------------------------------------
step "Chromium extension"
sync_tree "$SRC/extension" "$SHARE_EXT"
say "$SHARE_EXT/"

# --- 3. systemd units ---------------------------------------------------------
step "systemd units"
install -d -m 0755 "$UNITS"
for unit in calma.service calma-watchdog.service calma-watchdog.timer; do
  install -m 0644 "$SRC/packaging/$unit" "$UNITS/$unit"
  say "$UNITS/$unit"
done

# --- 4. configuration and state ---------------------------------------------
step "Configuration and state"
install -d -m 0755 "$ETC" "$VAR"
if [ ! -e "$ETC/rules.json" ]; then
  # Default: no rules. Written atomically so the daemon never reads a partial file.
  tmp="$(mktemp "$ETC/.rules.json.XXXXXX")"
  printf '{\n  "version": 1,\n  "settings": {\n    "day_starts_at": 4\n  },\n  "rules": {}\n}\n' > "$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$ETC/rules.json"
  say "$ETC/rules.json (new, empty)"
else
  say "$ETC/rules.json (kept)"
fi
say "$VAR/"

# --- 5. /etc/chromium-flags.conf ---------------------------------------------
# Ensure exactly one --load-extension= line that includes our path. If the
# file already has such a line (another extension), append ours to it.
step "Chromium flags"
install -d -m 0755 "$(dirname "$FLAGS")"
[ -e "$FLAGS" ] || : > "$FLAGS"
if grep -qE "^--load-extension=(.*,)?${EXT_PATH}(,.*)?$" "$FLAGS"; then
  say "$FLAGS already loads the extension"
elif grep -qE '^--load-extension=' "$FLAGS"; then
  tmp="$(mktemp "$(dirname "$FLAGS")/.chromium-flags.XXXXXX")"
  awk -v ext="$EXT_PATH" '
    !done && /^--load-extension=/ { print $0 "," ext; done = 1; next }
    { print }
  ' "$FLAGS" > "$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$FLAGS"
  say "$FLAGS: appended $EXT_PATH to the existing --load-extension line"
else
  # Make sure the file ends with a newline before appending.
  if [ -s "$FLAGS" ] && [ "$(tail -c1 "$FLAGS" | od -An -c | tr -d ' ')" != '\n' ]; then
    printf '\n' >> "$FLAGS"
  fi
  cat "$SRC/packaging/chromium-flags.conf" >> "$FLAGS"
  chmod 0644 "$FLAGS"
  say "$FLAGS: added --load-extension=$EXT_PATH"
fi

# --- 6. services --------------------------------------------------------------
step "Services"
sysctl daemon-reload
sysctl enable --now calma.service calma-watchdog.timer
if [ "$UPGRADE" -eq 1 ]; then
  # The unit refuses stop/restart on purpose; a SIGTERM makes it flush and exit,
  # and Restart=always brings the new code up within a second.
  sysctl kill -s SIGTERM calma.service
  say "asked the running daemon to exit; systemd restarts it with the new code"
fi

# --- 7. optional: bar plugin for the invoking user -------------------------
if [ "$WITH_PLUGIN" -eq 1 ]; then
  step "Omarchy bar plugin"
  if [ ! -d "$SRC/plugin" ]; then
    say "$SRC/plugin is missing; skipping the plugin"
  else
    target_user="${SUDO_USER:-}"
    if [ -z "$target_user" ] || [ "$target_user" = "root" ]; then
      target_user="$(id -un)"
    fi
    target_home="$(getent passwd "$target_user" | cut -d: -f6)"
    if [ -z "$target_home" ]; then
      say "could not find the home directory of $target_user; skipping the plugin"
    else
      plugin_dir="$DESTDIR$target_home/.config/omarchy/plugins/$PLUGIN_ID"
      install -d -m 0755 "$plugin_dir"
      if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete "$SRC/plugin/" "$plugin_dir/"
      else
        rm -rf "$plugin_dir"
        cp -a "$SRC/plugin" "$plugin_dir"
      fi
      if [ -z "$DESTDIR" ] && [ "$(id -un)" != "$target_user" ]; then
        chown -R "$target_user:" "$DESTDIR$target_home/.config/omarchy/plugins"
      fi
      say "$plugin_dir/"
      say ""
      say "Calma never edits shell.json for you. Add this to bar.layout.right in"
      say "$target_home/.config/omarchy/shell.json:"
      say ""
      say "    {\"id\": \"$PLUGIN_ID\"}"
    fi
  fi
fi

# --- 8. agent skill for the invoking user -----------------------------------
# Links skills/calma-setup into ~/.agents/skills (read by Claude Code, Codex and
# Pi) so an agent can set up and operate Calma for the person.
if [ -z "$DESTDIR" ] && [ -d "$SRC/skills/calma-setup" ]; then
  step "Agent skill"
  skill_user="${SUDO_USER:-}"
  if [ -z "$skill_user" ] || [ "$skill_user" = "root" ]; then
    skill_user="$(id -un)"
  fi
  skill_home="$(getent passwd "$skill_user" | cut -d: -f6)"
  if [ -n "$skill_home" ]; then
    skills_dir="$skill_home/.agents/skills"
    install -d -m 0755 "$skills_dir"
    ln -sfn "$SRC/skills/calma-setup" "$skills_dir/calma-setup"
    [ "$(id -un)" = "$skill_user" ] || chown -h "$skill_user:" "$skills_dir/calma-setup" "$skills_dir" 2>/dev/null || true
    say "$skills_dir/calma-setup -> $SRC/skills/calma-setup"
  fi
fi

# --- done -------------------------------------------------------------------
step "Done."
cat <<EOF

Next steps:

  1. Set the password that gates every loosening (optional, but do it now if
     you want one; without a password nothing can ever be loosened):

       sudo calma password

  2. Add your first rule, for example X twice a day, five minutes each:

       sudo calma add x --preset x --sessions 2 --minutes 5

  3. Restart Chromium so it picks up the policy and loads the extension.

  Then: calma status, calma open x, calma doctor.
EOF
if [ -n "$DESTDIR" ]; then
  echo
  echo "(test run: everything went under $DESTDIR and systemctl was not called)"
fi
