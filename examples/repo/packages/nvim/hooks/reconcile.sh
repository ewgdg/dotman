#!/bin/sh
set -eu

# Example only: reconcile is the actual import action.
# Dotman passes both repo and live paths so the helper can open a review diff
# and then editable source buffers for actual reconciliation.
set -- \
  uv run dotman reconcile editor \
  --editor "${EDITOR:-vi}" \
  --review-repo-path "${DOTMAN_REVIEW_REPO_PATH:-$DOTMAN_REPO_PATH}" \
  --review-live-path "${DOTMAN_REVIEW_LIVE_PATH:-$DOTMAN_LIVE_PATH}" \
  --repo-path "$DOTMAN_REPO_PATH" \
  --live-path "$DOTMAN_LIVE_PATH"

exec "$@"
