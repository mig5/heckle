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
  debfile=$(ls -1 dist/${release}/*.deb)
  reprepro -b /home/user/git/repo includedeb "${release}" "${debfile}"
done

# RPM packages. rpmsign signs the RPM itself; detached signatures are useful for
# release/download verification too. Uploading/updating yum repositories is separate.
sudo apt-get -y install createrepo-c rpm
RPM_DISTS=(
  fedora:43
)
KEYID="54A91143AE0AB4F7743B01FE888ED1B423A3BC99"
REPO_ROOT="${HOME}/git/repo_rpm"
REMOTE="ashpool.mig5.net:/opt/repo_rpm"
BUILD_OUTPUT="${HOME}/git/heckle/dist"
mkdir -p dist/rpm
for dist in "${RPM_DISTS[@]}"; do
  release=$(echo ${dist} | cut -d: -f2)
  REPO_RELEASE_ROOT="${REPO_ROOT}/${release}"
  RPM_REPO="${REPO_RELEASE_ROOT}/rpm/x86_64"
  mkdir -p "$RPM_REPO"
  docker build -f Dockerfile.rpmbuild -t "heckle-rpm:${release}" \
    --no-cache --progress=plain --build-arg BASE_IMAGE="$dist" .

  rm -rf "$PWD/dist/rpm"/*
  mkdir -p "$PWD/dist/rpm"

  docker run --rm -v "$PWD":/src -v "$PWD/dist/rpm":/out "heckle-rpm:${release}"
  sudo chown -R "${USER}" "$PWD/dist"

  for file in `ls -1 "${BUILD_OUTPUT}/rpm"`; do
    rpmsign --addsign "${BUILD_OUTPUT}/rpm/$file"
  done

  cp "${BUILD_OUTPUT}/rpm/"*.rpm "$RPM_REPO/"

  createrepo_c "$RPM_REPO"

  echo "==> Signing repomd.xml..."
  qubes-gpg-client --local-user "$KEYID" --detach-sign --armor "$RPM_REPO/repodata/repomd.xml" > "$RPM_REPO/repodata/repomd.xml.asc"  
done

# If every local artifact built and signed successfully, publish Python artifacts.
poetry publish

echo "==> Syncing rpm repo to server..."
rsync -aHPvz --exclude=.git --delete "$REPO_ROOT/" "$REMOTE/"

echo "Done."
