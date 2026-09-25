#!/usr/bin/env bash
set -euo pipefail

# Install the CLI during the Render build so it is available at runtime.
SCREAMING_FROG_DEB_URL="https://download.screamingfrog.co.uk/products/seo-spider/screamingfrogseospider_24.3_amd64.deb"
DEB_FILE="$(mktemp --suffix=.deb)"
trap 'rm -f "$DEB_FILE"' EXIT

if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=()
elif command -v sudo >/dev/null 2>&1; then
    SUDO=(sudo)
else
    echo "Build requires root or sudo to install Screaming Frog and Java." >&2
    exit 1
fi

"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y ca-certificates curl openjdk-17-jre-headless
curl --fail --location --silent --show-error "$SCREAMING_FROG_DEB_URL" --output "$DEB_FILE"
"${SUDO[@]}" dpkg --install "$DEB_FILE" || {
    "${SUDO[@]}" apt-get install --fix-broken --yes
}

command -v screamingfrogseospider
uv sync --frozen
