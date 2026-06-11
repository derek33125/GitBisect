#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TOOLS_DIR="${SCRIPT_DIR}/tools"
BIN_DIR="${TOOLS_DIR}/bin"
mkdir -p "${BIN_DIR}"

need_cmd() {
  local name=$1
  if command -v "${name}" >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

install_ninja() {
  if ! need_cmd ninja; then
    return 0
  fi
  if [[ -x "${BIN_DIR}/ninja" ]]; then
    return 0
  fi
  local url="https://github.com/ninja-build/ninja/releases/download/v1.13.1/ninja-linux.zip"
  local zip_path="${TOOLS_DIR}/ninja-linux.zip"
  curl -L --fail --retry 3 -o "${zip_path}" "${url}"
  unzip -qo "${zip_path}" -d "${BIN_DIR}"
  chmod +x "${BIN_DIR}/ninja"
}

install_ccache() {
  if ! need_cmd ccache; then
    return 0
  fi
  if [[ -x "${BIN_DIR}/ccache" ]]; then
    return 0
  fi
  local version="4.12.1"
  local url="https://github.com/ccache/ccache/releases/download/v${version}/ccache-${version}-linux-x86_64.tar.xz"
  local tarball="${TOOLS_DIR}/ccache-${version}-linux-x86_64.tar.xz"
  local unpack_dir="${TOOLS_DIR}/ccache-${version}"
  curl -L --fail --retry 3 -o "${tarball}" "${url}"
  rm -rf "${unpack_dir}"
  mkdir -p "${unpack_dir}"
  tar -xJf "${tarball}" -C "${unpack_dir}" --strip-components=1
  cp "${unpack_dir}/ccache" "${BIN_DIR}/ccache"
  chmod +x "${BIN_DIR}/ccache"
}

install_ninja
install_ccache

echo "Tools ready under ${BIN_DIR}"
if command -v ninja >/dev/null 2>&1; then
  command -v ninja
else
  echo "${BIN_DIR}/ninja"
fi
if command -v ccache >/dev/null 2>&1; then
  command -v ccache
else
  echo "${BIN_DIR}/ccache"
fi
