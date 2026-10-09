"""Install a versioned, per-user engine without changing QGIS or shell settings.

Conda prefixes never move: repair builds a new owned generation and atomically
changes a pointer only after a real worker self-test. Old working generations
remain available if installation, validation, or cancellation fails.
"""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import ssl
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener
import uuid

from .client import EngineCanceled, EngineError, call_with_python, run_process

ENGINE_VERSION = "0.4.1-1"
OWNER = "org.pyforestscan.qgis.engine"
MARKER = ".pyforestscan-owned.json"
HERE = Path(__file__).resolve().parent


def platform_tag():
    machine = platform.machine().lower()
    system = platform.system()
    if system == "Darwin":
        if machine in ("arm64", "aarch64"):
            return "osx-arm64"
        if machine in ("x86_64", "amd64"):
            return "osx-64"
    if machine in ("x86_64", "amd64"):
        if system == "Windows":
            return "win-64"
        if system == "Linux":
            return "linux-64"
    raise EngineError(
        f"The private engine is not available for {system} {machine}. Use a supported 64-bit QGIS installation."
    )


def engine_root():
    override = os.environ.get("PYFORESTSCAN_ENGINE_ROOT")
    if override:
        return Path(override).expanduser().absolute()
    try:
        from qgis.PyQt.QtCore import QStandardPaths

        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.GenericDataLocation
        )
        if not base:
            raise EngineError(
                "QGIS could not locate your writable application-data folder."
            )
        return Path(base) / "pyforestscan" / "engines"
    except ImportError:
        if os.name == "nt":
            base = Path(
                os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
            )
        elif platform.system() == "Darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(
                os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
            )
        return base / "pyforestscan" / "engines"


def version_root():
    return engine_root() / f"{ENGINE_VERSION}-{platform_tag()}"


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".record-", suffix=".json", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _owned(path, kind):
    path = Path(path)
    marker = path / MARKER
    if path.is_symlink() or not path.is_dir() or marker.is_symlink():
        return False
    try:
        record = _json(marker)
        return record == {"owner": OWNER, "kind": kind, "schema": 1}
    except (OSError, ValueError):
        return False


def _ensure_owned(path, kind):
    path = Path(path)
    if path.exists():
        if not _owned(path, kind):
            raise EngineError(
                f"Refusing to change an unrecognized engine folder: {path}"
            )
        return
    path.mkdir(parents=True, exist_ok=False)
    _atomic_json(path / MARKER, {"owner": OWNER, "kind": kind, "schema": 1})


def _remove_generation(path, base):
    path, base = Path(path), Path(base)
    if (
        not re.fullmatch(r"[0-9a-f]{32}", path.name)
        or path.parent != base / "instances"
        or path.parent.is_symlink()
        or not _owned(base, "version")
        or not _owned(path, "generation")
    ):
        raise EngineError("Refusing to remove an unrecognized engine generation.")
    shutil.rmtree(path)


@contextmanager
def _operation_lock(root):
    """OS-owned locks are released after crashes; there is no stale-lock deletion."""
    root = Path(root)
    path = root / ".setup.lock"
    if path.is_symlink():
        raise EngineError(
            "The engine setup lock is a symbolic link; refusing to use it."
        )
    with open(path, "a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            if stream.read(1) == b"":
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise EngineError(
                    "Another PyForestScan setup operation is running."
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise EngineError(
                    "Another PyForestScan setup operation is running."
                ) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _https(url):
    if not isinstance(url, str):
        raise EngineError("Engine download URLs must be HTTPS strings.")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise EngineError(
            "Engine downloads require a valid HTTPS URL without credentials."
        )
    return url


def _lock_data():
    path = HERE / "locks" / f"{platform_tag()}.json"
    try:
        lock = _json(path)
    except (OSError, ValueError) as exc:
        raise EngineError(
            "The plugin is missing a valid engine package manifest. Reinstall the plugin ZIP."
        ) from exc
    if (
        lock.get("platform") != platform_tag()
        or lock.get("engine_version") != ENGINE_VERSION
    ):
        raise EngineError(
            "The engine manifest does not match this platform or plugin version."
        )
    packages = lock.get("packages", [])
    if not packages:
        raise EngineError("The engine manifest contains no packages.")
    names = set()
    for package in packages:
        if not isinstance(package, dict):
            raise EngineError("The engine manifest contains an invalid package entry.")
        _https(package.get("url"))
        name = package.get("filename", "")
        if (
            not isinstance(name, str)
            or not re.fullmatch(
                r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\.(?:conda|tar\.bz2)", name
            )
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or name in names
            or not re.fullmatch(r"[0-9a-f]{64}", package.get("sha256", ""))
            or not isinstance(package.get("size"), int)
            or package["size"] <= 0
        ):
            raise EngineError("The engine manifest contains an invalid package entry.")
        names.add(name)
    return lock, hashlib.sha256(path.read_bytes()).hexdigest()


def _bootstrap_data():
    try:
        data = _json(HERE / "bootstrap.json")["platforms"][platform_tag()]
        _https(data["url"])
        if not re.fullmatch(r"[0-9a-f]{64}", data["sha256"]) or data["size"] <= 0:
            raise ValueError("Invalid checksum")
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EngineError(
            "The plugin has no valid installer manifest for this platform."
        ) from exc


def _python(prefix):
    return Path(prefix) / ("python.exe" if os.name == "nt" else "bin/python")


def _active():
    base = version_root()
    if not _owned(engine_root(), "root") or not _owned(base, "version"):
        return None
    try:
        path = base / "active.json"
        if path.is_symlink():
            return None
        record = _json(path)
        generation = record.get("generation", "")
        if not isinstance(generation, str) or not re.fullmatch(
            r"[0-9a-f]{32}", generation
        ):
            return None
        instance = base / "instances" / generation
        if instance.parent.is_symlink() or not _owned(instance, "generation"):
            return None
        _, digest = _lock_data()
        if record.get("protocol") != 1 or record.get("lock_sha256") != digest:
            return None
        prefix = instance / "env"
        if prefix.is_symlink() or not _python(prefix).is_file():
            return None
        return record, prefix
    except (OSError, ValueError, EngineError):
        return None


def engine_python():
    """Cheap read only; installation and diagnostics are explicit operations."""
    try:
        active = _active()
        return _python(active[1]) if active else None
    except EngineError:
        return None


def status():
    result = {
        "installed": False,
        "path": str(engine_root()),
        "platform": "unsupported",
        "download_bytes": 0,
        "engine_version": ENGINE_VERSION,
        "error": "",
        "removable": False,
    }
    try:
        result["platform"] = platform_tag()
        result["path"] = str(version_root())
        result["removable"] = _owned(version_root(), "version")
        lock, _ = _lock_data()
        bootstrap = _bootstrap_data()
        result["download_bytes"] = (
            sum(item["size"] for item in lock["packages"]) + bootstrap["size"]
        )
        active = _active()
        if active:
            result.update(
                installed=True,
                python=str(_python(active[1])),
                details=active[0].get("selftest", {}),
            )
    except (EngineError, OSError, ValueError) as exc:
        result["error"] = str(exc)
    return result


def _check_cancel(cancel):
    if cancel and cancel():
        raise EngineCanceled(
            "Engine setup was canceled; the previous engine has been preserved."
        )


class _HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_standard(url, destination, size, cancel, progress):
    opener = build_opener(
        _HTTPSRedirect(), HTTPSHandler(context=ssl.create_default_context())
    )
    with (
        opener.open(_https(url), timeout=30) as response,
        open(destination, "wb") as stream,
    ):
        _https(response.geturl())
        count = 0
        while True:
            _check_cancel(cancel)
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            count += len(chunk)
            if count > size:
                raise EngineError(
                    "The downloaded engine package is larger than its trusted manifest."
                )
            stream.write(chunk)
            if progress:
                progress(count)


def _download_qgis(url, destination, size, cancel, progress):
    """Use QGIS's per-thread network manager, including configured proxies/TLS."""
    from qgis.PyQt.QtCore import QEventLoop, QTimer, QUrl
    from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
    from qgis.core import QgsNetworkAccessManager

    loop = QEventLoop()
    request = QNetworkRequest(QUrl(_https(url)))
    request.setAttribute(
        QNetworkRequest.Attribute.RedirectPolicyAttribute,
        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
    )
    reply = QgsNetworkAccessManager.instance().get(request)
    timer = QTimer()
    timer.setInterval(100)
    state = {"bytes": 0, "error": None, "last_data": time.monotonic()}
    with open(destination, "wb") as stream:

        def consume():
            try:
                data = bytes(reply.readAll())
                state["bytes"] += len(data)
                if state["bytes"] > size:
                    raise EngineError(
                        "The downloaded engine package exceeds its trusted size."
                    )
                stream.write(data)
                if data:
                    state["last_data"] = time.monotonic()
                if progress:
                    progress(state["bytes"])
            except Exception as exc:
                state["error"] = exc
                reply.abort()

        def heartbeat():
            try:
                _check_cancel(cancel)
                if time.monotonic() - state["last_data"] > 60:
                    raise EngineError(
                        "Engine download timed out. Check the network/proxy settings and retry."
                    )
            except Exception as exc:
                state["error"] = exc
                reply.abort()

        reply.readyRead.connect(consume)
        reply.finished.connect(loop.quit)
        timer.timeout.connect(heartbeat)
        timer.start()
        try:
            loop.exec()
            consume()
            if state["error"]:
                raise state["error"]
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise EngineError(
                    f"Engine download failed: {reply.errorString()}. Check QGIS network/proxy settings."
                )
            _https(reply.url().toString())
        finally:
            timer.stop()
            reply.deleteLater()


def _download(url, destination, size, cancel, progress):
    try:
        from qgis.core import QgsNetworkAccessManager  # noqa: F401
        from qgis.PyQt.QtCore import QCoreApplication
    except ImportError:
        return _download_standard(url, destination, size, cancel, progress)
    if QCoreApplication.instance() is None:
        return _download_standard(url, destination, size, cancel, progress)
    return _download_qgis(url, destination, size, cancel, progress)


def _verified(path, digest, size):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        return False
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest() == digest


def _download_verified(url, destination, sha256, size, cancel=None, progress=None):
    _https(url)
    destination = Path(destination)
    if destination.is_symlink() or destination.parent.is_symlink():
        raise EngineError(
            "Refusing to use a symbolic link in the engine download cache."
        )
    if _verified(destination, sha256, size):
        if progress:
            progress(size)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".download-", dir=destination.parent
    )
    os.close(descriptor)
    try:
        _download(url, temporary, size, cancel, progress)
        _check_cancel(cancel)
        if not _verified(temporary, sha256, size):
            raise EngineError(
                "Engine download checksum/size mismatch. The file was discarded; retry setup."
            )
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def _sign_relocated_macos(prefix, cancel=None):
    """Restore ad-hoc signatures invalidated by conda prefix relocation.

    Conda does the same for osx-arm64 prefix replacement. Sign only regular
    Mach-O files in this newly installed, SHA256-verified private generation;
    QGIS and operating-system security configuration are never modified.
    """
    if platform.system() != "Darwin":
        return
    prefix = Path(prefix)
    if prefix.is_symlink() or not _owned(prefix.parent, "generation"):
        raise EngineError("Refusing to finalize an unrecognized engine prefix.")
    magic = {
        bytes.fromhex(value)
        for value in (
            "feedface",
            "cefaedfe",
            "feedfacf",
            "cffaedfe",
            "cafebabe",
            "bebafeca",
            "cafebabf",
            "bfbafeca",
        )
    }
    invalid = []
    for directory, dirs, files in os.walk(prefix, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in files:
            _check_cancel(cancel)
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                if stream.read(4) not in magic:
                    continue
            code, _ = run_process(
                ["/usr/bin/codesign", "--verify", path], cancel=cancel, timeout=30
            )
            if code:
                invalid.append(path)
    for start in range(0, len(invalid), 32):
        code, logs = run_process(
            [
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                *invalid[start : start + 32],
            ],
            cancel=cancel,
            timeout=60,
        )
        if code:
            raise EngineError(
                f"macOS engine finalization failed; the previous engine is unchanged. {logs[-2000:]}"
            )


def install_engine(progress=None, cancel=None):
    """Install or repair; call on a worker thread, never the QGIS GUI thread.

    progress receives (percentage, human_readable_message). All network content
    is HTTPS and SHA256-verified before the installer can execute or unpack it.
    """
    lock, digest = _lock_data()
    bootstrap = _bootstrap_data()
    root, base = engine_root(), version_root()
    _ensure_owned(root, "root")
    with _operation_lock(root):
        _ensure_owned(base, "version")
        cache = base / "downloads"
        instances = base / "instances"
        for folder in (cache, instances):
            if folder.is_symlink():
                raise EngineError(
                    "An engine data directory is a symbolic link; refusing to change it."
                )
            folder.mkdir(exist_ok=True)
        total = bootstrap["size"] + sum(item["size"] for item in lock["packages"])
        # Archives, expanded package cache and copied prefix coexist during setup.
        required_space = max(2_000_000_000, total * 12)
        if shutil.disk_usage(base).free < required_space:
            raise EngineError(
                f"Not enough free disk space for the engine. Allow at least {math.ceil(required_space / 1e8) / 10:.1f} GB and retry."
            )
        complete = 0

        def report(value, message):
            _check_cancel(cancel)
            if progress:
                progress(value, message)

        def fetch(item, filename):
            nonlocal complete
            target = cache / filename
            _download_verified(
                item["url"],
                target,
                item["sha256"],
                item["size"],
                cancel,
                lambda count: report(
                    75 * (complete + count) / total, f"Downloading {filename}"
                ),
            )
            complete += item["size"]
            return target

        report(0, "Downloading verified engine packages")
        executable = fetch(
            bootstrap, "micromamba.exe" if os.name == "nt" else "micromamba"
        )
        executable.chmod(0o700)
        package_paths = [
            (fetch(item, item["filename"]), item) for item in lock["packages"]
        ]
        generation = uuid.uuid4().hex
        instance = instances / generation
        _ensure_owned(instance, "generation")
        activated = False
        try:
            explicit = instance / "explicit.txt"
            explicit.write_text(
                "@EXPLICIT\n"
                + "\n".join(
                    path.as_uri() + ("#" + item["md5"] if item.get("md5") else "")
                    for path, item in package_paths
                )
                + "\n",
                encoding="utf-8",
            )
            prefix = instance / "env"
            # Installing into a fresh owned conda prefix avoids the create command
            # registering it in the user's global ~/.conda/environments.txt.
            (prefix / "conda-meta").mkdir(parents=True)
            (prefix / "conda-meta" / "history").touch()
            config = instance / "installer-config.yaml"
            config.write_text(
                json.dumps(
                    {
                        "pkgs_dirs": [str(instance / "mamba" / "pkgs")],
                        "envs_dirs": [str(instance / "mamba" / "envs")],
                    }
                ),
                encoding="utf-8",
            )
            report(
                76,
                "Installing the isolated engine; QGIS and shell settings stay unchanged",
            )
            code, logs = run_process(
                [
                    executable,
                    "install",
                    "--yes",
                    "--rc-file",
                    config,
                    "--no-env",
                    "--offline",
                    "--always-copy",
                    "--no-shortcuts",
                    "--no-pyc",
                    "--prefix",
                    prefix,
                    "--root-prefix",
                    instance / "mamba",
                    "--file",
                    explicit,
                ],
                cancel=cancel,
                timeout=1800,
                event=lambda line: report(80, line[-500:]),
            )
            if code != 0:
                raise EngineError(
                    f"The private engine installer failed. The previous engine is unchanged. {logs[-3000:]}"
                )
            report(88, "Finalizing the engine installation")
            _sign_relocated_macos(prefix, cancel)
            report(92, "Checking real point-cloud and forest metric operations")
            result = call_with_python(
                _python(prefix),
                "selftest",
                {},
                cancel=cancel,
                timeout=120,
                event=lambda message: report(
                    95, str(message.get("message", "Validating engine"))
                ),
            )
            if result.get("ready") is not True:
                raise EngineError(
                    "The new engine did not pass its scientific self-test; the previous engine is unchanged."
                )
            report(97, "Cleaning temporary setup files")
            installer_cache = instance / "mamba"
            if installer_cache.exists():
                if installer_cache.is_symlink() or not _owned(instance, "generation"):
                    raise EngineError(
                        "Refusing to remove an unrecognized installer cache."
                    )
                shutil.rmtree(installer_cache)
            _check_cancel(cancel)
            _atomic_json(
                base / "active.json",
                {
                    "protocol": 1,
                    "generation": generation,
                    "engine_version": ENGINE_VERSION,
                    "lock_sha256": digest,
                    "selftest": result,
                    "created": time.time(),
                },
            )
            activated = True
            if progress:
                progress(100, "The PyForestScan engine is ready")
            return status()
        finally:
            if not activated:
                _remove_generation(instance, base)


def remove_engine(progress=None, cancel=None):
    """Remove only sentinel-owned generations for this engine/platform."""
    root, base = engine_root(), version_root()
    if not base.exists():
        return status()
    if not _owned(root, "root") or not _owned(base, "version"):
        raise EngineError("Refusing to remove an unrecognized engine directory.")
    with _operation_lock(root):
        _check_cancel(cancel)
        instances = base / "instances"
        if instances.is_symlink():
            raise EngineError("Refusing to remove linked engine data.")
        generations = list(instances.iterdir()) if instances.is_dir() else []
        # Validate every generation before deleting anything, including activation.
        if any(
            not re.fullmatch(r"[0-9a-f]{32}", path.name)
            or not _owned(path, "generation")
            for path in generations
        ):
            raise EngineError(
                "The engine folder contains unrecognized data; removal was stopped."
            )
        active = base / "active.json"
        if active.is_symlink():
            raise EngineError(
                "The engine activation record is a symbolic link; removal was stopped."
            )
        active.unlink(missing_ok=True)
        for index, generation in enumerate(generations):
            _remove_generation(generation, base)
            if progress:
                progress(
                    100 * (index + 1) / max(1, len(generations)),
                    "Removing private engine files",
                )
        if progress:
            progress(
                100,
                "The engine was removed; verified download files are retained for reinstall",
            )
    return status()
