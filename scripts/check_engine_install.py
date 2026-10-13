#!/usr/bin/env python3
"""Exercise a real engine install, repair and canceled repair in a test root."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import manager  # noqa: E402
from runtime.client import EngineCanceled, sanitized_environment  # noqa: E402


def check(keep=False):
    if not os.environ.get("PYFORESTSCAN_ENGINE_ROOT"):
        raise RuntimeError("Set PYFORESTSCAN_ENGINE_ROOT to a dedicated test location.")
    if manager.status()["installed"]:
        raise RuntimeError("This test requires a root without an active engine.")

    def progress(value, message):
        print(f"{value:.0f}% {message}", flush=True)

    try:
        first = manager.install_engine(progress=progress)
        assert first["installed"], first
        previous_python = manager.engine_python()
        repaired = manager.install_engine(progress=progress)
        assert repaired["installed"], repaired
        assert previous_python.is_file(), "Repair removed the previous generation"
        assert manager.engine_python() != previous_python, "Repair did not activate a new generation"

        base = manager.version_root()
        before = (base / "active.json").read_bytes()
        generations = set((base / "instances").iterdir())
        cancel_requested = False

        def cancel_during_link(value, message):
            nonlocal cancel_requested
            if "Linking " in message:
                cancel_requested = True

        try:
            manager.install_engine(
                progress=cancel_during_link, cancel=lambda: cancel_requested
            )
        except EngineCanceled:
            pass
        else:
            raise AssertionError("Repair was not canceled during package linking")
        assert (base / "active.json").read_bytes() == before
        assert set((base / "instances").iterdir()) == generations
        assert not manager._installer_cache(manager.engine_root()).exists()

        interpreter = manager.engine_python()
        environment = sanitized_environment(interpreter)
        environment["PYFORESTSCAN_TEST_ENGINE"] = "1"
        subprocess.run(
            [str(interpreter), "-I", "-m", "unittest", "discover", "-s", str(ROOT / "tests"),
             "-p", "test_worker.py", "-v"],
            env=environment, check=True,
        )
        print(f"PASS: {first['platform']} install, repair, cancellation and worker tests", flush=True)
    finally:
        if not keep:
            manager.remove_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Keep the validated engine for QGIS integration tests")
    check(parser.parse_args().keep)
