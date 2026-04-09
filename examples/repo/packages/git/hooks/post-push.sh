#!/bin/sh
set -eu

sh "$DOTMAN_REPO_ROOT/scripts/log-package-event.sh" "applied" "$DOTMAN_PACKAGE_ID"
