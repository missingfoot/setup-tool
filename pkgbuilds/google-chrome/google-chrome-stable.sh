#!/bin/bash
# Launch Chrome with any extra flags from ~/.config/chrome-flags.conf
# (one or more per line, lines starting with # ignored).

XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-~/.config}

if [[ -f $XDG_CONFIG_HOME/chrome-flags.conf ]]; then
    CHROME_USER_FLAGS="$(grep -v '^#' "$XDG_CONFIG_HOME/chrome-flags.conf")"
fi

exec /opt/google/chrome/google-chrome $CHROME_USER_FLAGS "$@"
