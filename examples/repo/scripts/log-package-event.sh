#!/bin/sh
set -eu

event_name="${1:-event}"
package_id="${2:-unknown}"

printf '%s: %s\n' "$event_name" "$package_id"
