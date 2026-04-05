#!/bin/sh
set -eu

# Example only: reconcile is the actual import action.
# Dotman passes both repo and live paths so the script can inspect, diff, or
# open the repo file in an editor for manual reconciliation.
"${EDITOR:-vi}" "$DOTMAN_REPO_PATH"
