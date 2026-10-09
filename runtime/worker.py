"""Execute one isolated processing job without importing QGIS or plugin startup."""

import argparse
import importlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import tempfile
import types


PROTOCOL = 1
_DLL_HANDLES = []


def _configure_native_libraries():
    """Retain conda DLL search directories for Windows Python 3.8 and newer."""
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        for relative in ("Library/bin", "DLLs"):
            directory = Path(sys.prefix) / relative
            if directory.is_dir():
                _DLL_HANDLES.append(os.add_dll_directory(str(directory)))


class Feedback:
    def _emit(self, event):
        print(json.dumps(event, allow_nan=False), flush=True)

    def pushInfo(self, message):
        self._emit({"type": "log", "message": str(message)})

    def setProgress(self, value):
        self._emit({"type": "progress", "value": min(100.0, max(0.0, float(value)))})

    def isCanceled(self):
        return False


def _safe_json(value):
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_result(path, result):
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".worker-result-", suffix=".json", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(_safe_json(result), stream, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_jobs():
    root = Path(__file__).resolve().parents[1]
    name = "_pyforestscan_worker_plugin"
    package = types.ModuleType(name)
    package.__path__ = [str(root)]
    package.__file__ = str(root / "__init__.py")
    sys.modules[name] = package
    return importlib.import_module(name + ".runtime.jobs")


def _terminated(signum, frame):
    # SIGTERM unwinds ordinary Python stages and cleans their temporary files.
    # A forced kill during a native operation may leave a hidden staging file.
    raise InterruptedError("Processing canceled.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _terminated)
    try:
        _configure_native_libraries()
        with open(args.request, encoding="utf-8") as stream:
            request = json.load(stream)
        if not isinstance(request, dict) or request.get("protocol") != PROTOCOL:
            raise ValueError("Unsupported worker protocol; expected protocol 1.")
        if not isinstance(request.get("operation"), str) or not isinstance(
            request.get("payload", {}), dict
        ):
            raise ValueError(
                "A worker request requires an operation and object payload."
            )
        result = _load_jobs().run_job(
            request["operation"], request.get("payload", {}), Feedback()
        )
        document = {"protocol": PROTOCOL, "ok": True, "result": result}
        status = 0
    except Exception as exc:
        document = {
            "protocol": PROTOCOL,
            "ok": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc).replace("\n", " ")[:4000],
            },
        }
        status = 1
    try:
        _write_result(args.result, document)
    except Exception as exc:
        print(
            f"Unable to write worker result: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
