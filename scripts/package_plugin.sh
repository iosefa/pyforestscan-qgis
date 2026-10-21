#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ID="pyforestscan_qgis"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist"
BUILD_DIR="${DIST_DIR}/${PLUGIN_ID}"
ZIP_FILE="${DIST_DIR}/${PLUGIN_ID}.zip"

if [[ ! -s "${ROOT_DIR}/LICENSE" ]]; then
  echo "A plugin-level LICENSE is required before building a release ZIP. Choose a GPL-compatible license and save its full text as LICENSE." >&2
  exit 1
fi

rm -rf "${BUILD_DIR}" "${ZIP_FILE}"
mkdir -p "${BUILD_DIR}"

cp "${ROOT_DIR}/metadata.txt" "${BUILD_DIR}/"
cp "${ROOT_DIR}/LICENSE" "${BUILD_DIR}/"
cp "${ROOT_DIR}/README.md" "${BUILD_DIR}/"
cp "${ROOT_DIR}/CHANGELOG.md" "${BUILD_DIR}/"
cp "${ROOT_DIR}/environment.yml" "${BUILD_DIR}/"
mkdir -p "${BUILD_DIR}/tests"
cp "${ROOT_DIR}/tests/README.md" "${BUILD_DIR}/tests/"
cp "${ROOT_DIR}/__init__.py" "${BUILD_DIR}/"
cp "${ROOT_DIR}/pyforestscan_plugin.py" "${BUILD_DIR}/"
cp "${ROOT_DIR}/provider.py" "${BUILD_DIR}/"
cp "${ROOT_DIR}/setup_dialog.py" "${BUILD_DIR}/"
cp -R "${ROOT_DIR}/vendor" "${BUILD_DIR}/"
cp -R "${ROOT_DIR}/runtime" "${BUILD_DIR}/"
cp -R "${ROOT_DIR}/algorithms" "${BUILD_DIR}/"
cp -R "${ROOT_DIR}/icons" "${BUILD_DIR}/"
cp -R "${ROOT_DIR}/docs" "${BUILD_DIR}/"
find "${BUILD_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "${BUILD_DIR}" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

(
  cd "${DIST_DIR}"
  zip -qr "${ZIP_FILE}" "${PLUGIN_ID}"
)

python3 "${ROOT_DIR}/scripts/check_plugin_zip.py" "${ZIP_FILE}"

echo "${ZIP_FILE}"
