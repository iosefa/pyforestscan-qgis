# Publishing to the QGIS plugin repository

PyForestScan is a Processing provider. The same ZIP supports the declared QGIS
3.40–4.99 range; native point-cloud processing requires 4.2+, while older QGIS
versions use the optional isolated engine. Keep the initial public release
experimental until the platform checks below support a stable designation.

## Prepare the release

1. Choose and include a plugin-level GPL-compatible `LICENSE` at the package
   root. Bundled PyForestScan and laspy retain their original licenses. QGIS
   requires a file named exactly `LICENSE`, without an extension.
2. Keep the GitHub repository public, enable its Issues feature, and check the
   homepage, repository and tracker URLs in `metadata.txt` while signed out.
   Push the release's source commits so the public repository matches the ZIP.
3. Run the checks in [tests/README.md](../tests/README.md). The GitHub Actions
   **QGIS compatibility** workflow tests Linux QGIS 3.40/4.2 and Windows installer
   guards. Use **Run workflow → engine_platforms** for real engine installation
   and worker checks on Linux, Windows and macOS. Review the actual results;
   a configured workflow is not a passing run. Smoke-test installation,
   dependency reporting, loaded-cloud HAG, and a forest metric on each target
   operating system. Record remaining limitations.
4. Update `metadata.txt`, `CHANGELOG.md`, README and bundled help together. Each
   uploaded version must be unique. Retain the author names **Nicholas Rogers,
   Joseph Percival** and a reachable maintainer email. Set `experimental=False`
   only when declaring a stable release is supported by the validation record.
5. Build with `scripts/package_plugin.sh` and validate the staged package. The
   ZIP must contain one `pyforestscan_qgis` directory, `metadata.txt`,
   `__init__.py`, `LICENSE`, dependency notices and help. Exclude compiled
   binaries, bytecode, hidden directories and development artifacts. Keep it
   below QGIS's 25 MB limit. Archive the exact upload ZIP and its SHA256; do not
   overwrite an already distributed version with different bytes.

These checks follow QGIS's [publication requirements](https://plugins.qgis.org/docs/publish)
and [required package files](https://docs.qgis.org/4.2/en/docs/pyqgis_developer_cookbook/plugins/releasing.html).

## Submit the ZIP

1. Sign in at [Upload a plugin](https://plugins.qgis.org/plugins/add/). The login
   page offers GitHub, GitLab, Google and OSGeo accounts. Complete any email
   verification sent to the plugin's maintainer address.
2. Upload the verified versioned ZIP. Its metadata supplies the description,
   authors, version, compatibility range and public source links. Review them
   before submitting. On later releases, upload a new version to the existing
   plugin entry rather than creating another plugin.
3. Inspect the version's **Security** and **Qt6 Check** results. Uploads undergo
   automated validation before volunteer approval. Fix critical findings and
   upload a new version; do not assume passing local tests guarantees approval.
4. After approval, confirm PyForestScan appears in QGIS's Plugin Manager. For
   an experimental version, testers must enable **Show also experimental
   plugins** under Plugin Manager settings, then find and install PyForestScan.
   Run the first-layer workflow from the bundled help on a fresh QGIS profile.

The [approval process](https://plugins.qgis.org/docs/approval) describes security
validation and volunteer review. The [QGIS 4 migration guidance](https://plugins.qgis.org/docs/migrate-qgis4)
explains how `qgisMaximumVersion=4.99` advertises QGIS 4 compatibility; metadata
does not replace testing. The QGIS site also requires a
[verified maintainer email](https://blog.qgis.org/2026/07/06/coming-soon-qgis-plugins-website-v4-0-0/).

## Explain the optional engine to reviewers

The upload contains Python source and data manifests, with no compiled binaries.
Native processing uses the installed QGIS libraries. Users explicitly choose
**Set up PyForestScan → Install engine** when additional capabilities are needed;
the plugin does not install an engine on activation. Downloads use the QGIS
network manager, HTTPS, exact package versions and SHA256 verification. The
engine has a separate Python environment and passes a real processing self-test
before activation. It does not modify QGIS's Python installation.

Metadata and bundled help disclose download, disk-space and platform requirements.
Reviewers still decide whether this external-runtime design satisfies repository
policy. If they request changes or an exception for the downloaded runtime,
resolve that with the QGIS plugin reviewers before advertising repository
availability. Neither a local test pass nor an upload is publication approval.
