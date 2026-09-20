#!/bin/bash
set -eo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# tests -> Poetry build -> AppImage -> DEBs -> RPMs/signing -> Poetry publish.
bash ./tests.sh

if command -v filedust >/dev/null 2>&1; then
  filedust -y .
fi

mkdir -p dist

# Python wheel + sdist.
poetry build

# AppImage from the same Poetry environment.
poetry run pyproject-appimage --output dist/Heckle.AppImage

# Detached signatures for Python/AppImage artifacts.
for file in dist/*; do
  [ -f "$file" ] || continue
  case "$file" in *.asc) continue ;; esac
  qubes-gpg-client --batch --armor --detach-sign "$file" > "$file.asc"
done

# Debian/Ubuntu packages. Repository publication is deliberately separate.
DISTS=(
  debian:bookworm
  debian:trixie
  ubuntu:noble
)
for dist in "${DISTS[@]}"; do
  release=${dist#*:}
  mkdir -p "dist/${release}"
  docker build -f Dockerfile.debbuild -t "heckle-deb:${release}" \
    --no-cache --progress=plain --build-arg BASE_IMAGE="$dist" .
  docker run --rm \
    -e SUITE="$release" \
    -v "$PWD":/src \
    -v "$PWD/dist/${release}":/out \
    "heckle-deb:${release}"
  for file in "dist/${release}"/*.deb; do
    [ -f "$file" ] || continue
    qubes-gpg-client --batch --armor --detach-sign "$file" > "$file.asc"
  done
done

# RPM packages. rpmsign signs the RPM itself; detached signatures are useful for
# release/download verification too. Uploading/updating yum repositories is separate.
RPM_DISTS=(fedora:43)
mkdir -p dist/rpm
for dist in "${RPM_DISTS[@]}"; do
  release=${dist#*:}
  out="dist/rpm/${release}"
  mkdir -p "$out"
  docker build -f Dockerfile.rpmbuild -t "heckle-rpm:${release}" \
    --no-cache --progress=plain --build-arg BASE_IMAGE="$dist" .
  docker run --rm -v "$PWD":/src -v "$PWD/$out":/out "heckle-rpm:${release}"
  sudo chown -R "${USER}" "$out"
  for file in "$out"/*.rpm; do
    [ -f "$file" ] || continue
    rpmsign --addsign "$file"
    qubes-gpg-client --batch --armor --detach-sign "$file" > "$file.asc"
  done
done

# If every local artifact built and signed successfully, publish Python artifacts.
poetry publish

echo "Done. Package/repository uploads and Forgejo release creation are intentionally separate."
