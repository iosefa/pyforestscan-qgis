"""Authoritative readiness contract for the managed processing engine."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .paths import BackendPaths
from .process_env import build_processing_engine_environment, hidden_subprocess_kwargs
from .runtime_manifest import PRODUCT_CAPABILITIES

PROCESSING_ENGINE_CONTRACT_VERSION = "2"
REQUIRED_PYFORESTSCAN_MODULES = (
    "pyforestscan",
    "pyforestscan.handlers",
    "pyforestscan.calculate",
    "pyforestscan.filters",
    "pyforestscan.process",
)


class ProcessingEngineState(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    READY = "READY"
    CHECKING = "CHECKING"
    SETUP_REQUIRED = "SETUP_REQUIRED"
    UPDATING = "UPDATING"
    REPAIR_REQUIRED = "REPAIR_REQUIRED"
    INCOMPATIBLE = "INCOMPATIBLE"
    FAILED = "FAILED"


class ProcessingEngineError(RuntimeError):
    """A setup/runtime problem detected before scientific job creation."""

    def __init__(self, code: str, message: str, technical_message: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.technical_message = technical_message or message


@dataclass(frozen=True)
class ProcessingEngineReport:
    state: ProcessingEngineState
    summary: str
    executable: str
    contract: dict[str, Any]
    failed_components: tuple[str, ...] = ()
    from_cache: bool = False

    @property
    def ready(self) -> bool:
        return self.state is ProcessingEngineState.READY


@dataclass(frozen=True)
class ProcessingRuntimeToken:
    executable: str
    environment_fingerprint: str
    contract_hash: str
    protocol: str
    verified_at: str
    product_capability_hash: str
    engine_id: str = ""
    backend_runner_hash: str = ""
    plugin_build_id: str = ""
    dependency_manifest_hash: str = ""
    runtime_generation_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ProcessingRuntimeToken | None":
        if not value:
            return None
        return cls(**{field: str(value.get(field, "")) for field in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ProcessingEngineStateModel:
    status: ProcessingEngineState
    last_verified: str
    contract_hash: str
    engine_version: str
    setup_needed: bool
    repair_needed: bool
    runtime_available: bool
    message: str
    engine_id: str = ""
    executable: str = ""
    environment_fingerprint: str = ""
    protocol_version: str = ""
    backend_runner_hash: str = ""
    plugin_build_id: str = ""
    product_capability_hash: str = ""
    dependency_manifest_hash: str = ""
    ready_for_processing: bool = False
    failure_code: str = ""
    runtime_token: ProcessingRuntimeToken | None = None
    runtime_generation_id: str = ""

    @classmethod
    def from_report(cls, report: ProcessingEngineReport) -> "ProcessingEngineStateModel":
        contract = report.contract
        token = _token_from_contract(contract) if report.ready else None
        return cls(
            report.state,
            str(contract.get("verified_at", "")),
            contract_hash(contract) if contract else "",
            str(contract.get("versions", {}).get("pyforestscan", "unknown")),
            report.state is ProcessingEngineState.SETUP_REQUIRED,
            report.state in {ProcessingEngineState.REPAIR_REQUIRED, ProcessingEngineState.INCOMPATIBLE},
            report.ready,
            report.summary,
            str(contract.get("engine_id", "")),
            report.executable,
            str(contract.get("environment_fingerprint", "")),
            str(contract.get("protocol_version", "")),
            str(contract.get("runner_sha256", "")),
            str(contract.get("plugin_build_id", "")),
            str(contract.get("product_capability_hash", "")),
            str(contract.get("dependency_manifest_hash", "")),
            report.ready,
            "" if report.ready else _failure_code(report.state),
            token,
            str(contract.get("runtime_generation_id", "")),
        )


def processing_engine_manifest_path(paths: BackendPaths) -> Path:
    return paths.backend_root / "processing_engine.json"


class ProcessingEngineSetupLock:
    """Cross-process lock preventing two QGIS sessions from modifying one engine."""

    def __init__(self, paths: BackendPaths) -> None:
        self.path = paths.backend_root / "processing_engine.setup.lock"
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.write(self._fd, json.dumps({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}).encode("utf-8"))
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self):
        if not self.acquire():
            raise ProcessingEngineError("ENGINE_BUSY", "Processing Engine is being prepared by another QGIS session.")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.release()


def environment_fingerprint(paths: BackendPaths) -> str:
    """Fingerprint execution-critical files without importing scientific packages."""
    digest = hashlib.sha256()
    candidates = (paths.python_executable, paths.config_file, *_critical_runtime_files(paths))
    for candidate in candidates:
        digest.update(str(candidate).encode("utf-8"))
        try:
            stat = candidate.stat()
        except OSError:
            digest.update(b"missing")
        else:
            digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode("ascii"))
    return digest.hexdigest()


def _critical_runtime_files(paths: BackendPaths) -> tuple[Path, ...]:
    """Return inexpensive package sentinels that invalidate stale READY manifests."""
    roots = [paths.environment_path / "Lib" / "site-packages"]
    roots.extend((paths.environment_path / "lib").glob("python*/site-packages"))
    relative = (
        Path("pyforestscan/__init__.py"), Path("pyforestscan/handlers.py"),
        Path("pyforestscan/calculate.py"), Path("pyforestscan/filters.py"),
        Path("pyforestscan/process.py"), Path("pdal/__init__.py"),
        Path("rasterio/__init__.py"), Path("numpy/__init__.py"),
        Path("scipy/__init__.py"), Path("shapely/__init__.py"),
        Path("pyproj/__init__.py"), Path("pandas/__init__.py"),
        Path("osgeo/gdal.py"),
    )
    return tuple(root / item for root in roots for item in relative)


def contract_hash(contract: dict[str, Any]) -> str:
    """Hash stable runtime identity and capability fields, excluding process-local data."""
    stable = {
        key: contract.get(key)
        for key in (
            "backend_api_version", "protocol_version", "plugin_version", "runner_sha256",
            "plugin_build_id",
            "python_version", "python_executable", "versions", "module_locations",
            "required_functions", "required_function_signatures", "product_capabilities",
            "capability_smoke_results", "dependency_manifest_hash", "product_capability_hash",
            "setup_completed_at", "setup_plugin_build_id",
            "runtime_generation_id",
        )
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def product_capability_hash(products: tuple[str, ...]) -> str:
    payload = {name: PRODUCT_CAPABILITIES.get(name, ()) for name in sorted(set(products))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def dependency_manifest_hash() -> str:
    from .runtime_manifest import PROCESSING_ENGINE_DEPENDENCIES

    payload = [asdict(item) for item in PROCESSING_ENGINE_DEPENDENCIES]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def engine_id(paths: BackendPaths) -> str:
    return hashlib.sha256(str(paths.environment_path.resolve()).encode("utf-8")).hexdigest()[:20]


def current_plugin_build_id() -> str:
    """Hash the packaged runner and launch surfaces without importing QGIS."""
    package_root = Path(__file__).resolve().parents[2]
    inputs = (
        package_root / "backend_runner" / "run_processing_job.py",
        package_root / "backend_runner" / "polygon_job_coordinator.py",
        package_root / "core" / "adapter.py",
        package_root / "core" / "pipeline.py",
        package_root / "core" / "backend" / "execution.py",
        package_root / "core" / "polygon_batch.py",
    )
    try:
        return hashlib.sha256(b"".join(path.read_bytes() for path in inputs)).hexdigest()
    except OSError:
        return ""


def current_runner_hash() -> str:
    """Hash the packaged managed-job runner without importing scientific code."""
    runner = Path(__file__).resolve().parents[2] / "backend_runner" / "run_processing_job.py"
    try:
        return hashlib.sha256(runner.read_bytes()).hexdigest()
    except OSError:
        return ""


class ProcessingEngineVerifier:
    """Verify the same managed interpreter used for scientific execution."""

    def __init__(self, paths: BackendPaths, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None, plugin_parent: Path | None = None) -> None:
        self.paths = paths
        self.runner = runner or subprocess.run
        self.plugin_parent = plugin_parent or Path(__file__).resolve().parents[3]

    def quick(self) -> ProcessingEngineReport:
        """Read a complete current-build setup marker without running managed code."""
        if not self.paths.python_executable.exists():
            return ProcessingEngineReport(ProcessingEngineState.SETUP_REQUIRED, "Processing setup required.", str(self.paths.python_executable), {})
        path = processing_engine_manifest_path(self.paths)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ProcessingEngineReport(
                ProcessingEngineState.REPAIR_REQUIRED,
                "Processing Engine setup record is missing or unreadable.",
                str(self.paths.python_executable),
                {},
                ("setup_manifest",),
            )
        packaged_build = current_plugin_build_id()
        runner_hash = current_runner_hash()
        required_values = (
            "plugin_build_id", "runner_sha256", "runner_hash", "contract_hash", "dependency_manifest_hash",
            "product_capability_hash", "protocol_version", "python_executable",
            "verified_at", "setup_completed_at", "runtime_generation_id",
        )
        stale = (
            payload.get("contract_version") != PROCESSING_ENGINE_CONTRACT_VERSION
            or payload.get("environment_fingerprint") != environment_fingerprint(self.paths)
            or not packaged_build
            or payload.get("plugin_build_id") != packaged_build
            or payload.get("setup_plugin_build_id") != packaged_build
            or not runner_hash
            or payload.get("runner_sha256") != runner_hash
            or payload.get("runner_hash") != runner_hash
            or payload.get("dependency_manifest_hash") != dependency_manifest_hash()
            or payload.get("product_capability_hash") != product_capability_hash(tuple(PRODUCT_CAPABILITIES))
            or str(payload.get("protocol_version", "")) != "2"
            or _normalized_path(str(payload.get("python_executable", ""))) != _normalized_path(str(self.paths.python_executable))
            or payload.get("contract_hash") != contract_hash(payload)
            or any(not payload.get(field) for field in required_values)
            or payload.get("status") != ProcessingEngineState.READY.value
        )
        if stale:
            return ProcessingEngineReport(ProcessingEngineState.REPAIR_REQUIRED, "Processing Engine setup is not current for this plugin build.", str(self.paths.python_executable), payload, ("current_build_setup",))
        state = ProcessingEngineState(payload.get("status", ProcessingEngineState.CHECKING.value))
        return ProcessingEngineReport(state, _summary_for_state(state), str(self.paths.python_executable), payload, tuple(payload.get("failed_components", ())), True)

    def verify(self, *, persist: bool = True, require_setup_marker: bool = False, setup_completed: bool = False) -> ProcessingEngineReport:
        if not self.paths.python_executable.exists():
            return ProcessingEngineReport(ProcessingEngineState.SETUP_REQUIRED, "Processing setup required.", str(self.paths.python_executable), {})
        command = [str(self.paths.python_executable), "-m", "pyforestscan_qgis.backend_runner", "inspect_runtime_contract"]
        env = build_processing_engine_environment(self.paths.environment_path, self.paths.platform.value)
        try:
            completed = self.runner(command, check=False, capture_output=True, text=True, timeout=180, cwd=str(self.plugin_parent), env=env, **hidden_subprocess_kwargs())
            contract = json.loads(completed.stdout or "{}")
        except Exception as exc:  # noqa: BLE001 - state boundary must be actionable.
            return ProcessingEngineReport(ProcessingEngineState.FAILED, "Processing Engine check failed.", str(self.paths.python_executable), {}, (str(exc),))
        failures = tuple(str(item) for item in contract.get("failed_required_components", ()))
        actual = str(contract.get("python_executable", ""))
        if actual and Path(actual).resolve() != self.paths.python_executable.resolve():
            failures += ("runtime_identity",)
        protocol_ok = bool(contract.get("protocol_compatible", False))
        previous: dict[str, Any] = {}
        try:
            previous = json.loads(processing_engine_manifest_path(self.paths).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        packaged_build = current_plugin_build_id()
        setup_marker_current = bool(
            previous.get("setup_completed_at")
            and previous.get("setup_plugin_build_id") == packaged_build
        )
        if not protocol_ok:
            state = ProcessingEngineState.INCOMPATIBLE
        elif failures or completed.returncode != 0:
            state = ProcessingEngineState.REPAIR_REQUIRED
        elif require_setup_marker and not (setup_marker_current or setup_completed):
            failures += ("current_build_setup",)
            state = ProcessingEngineState.REPAIR_REQUIRED
        else:
            state = ProcessingEngineState.READY
        setup_completed_at = (
            datetime.now(timezone.utc).isoformat()
            if setup_completed and state is ProcessingEngineState.READY
            else str(previous.get("setup_completed_at", ""))
        )
        setup_plugin_build_id = (
            packaged_build
            if setup_completed and state is ProcessingEngineState.READY
            else str(previous.get("setup_plugin_build_id", ""))
        )
        runtime_generation_id = (
            uuid.uuid4().hex
            if setup_completed and state is ProcessingEngineState.READY
            else str(previous.get("runtime_generation_id", ""))
        )
        payload = {
            **contract,
            "contract_version": PROCESSING_ENGINE_CONTRACT_VERSION,
            "environment_fingerprint": environment_fingerprint(self.paths),
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "status": state.value,
            "failed_components": list(failures),
            "engine_id": engine_id(self.paths),
            "dependency_manifest_hash": dependency_manifest_hash(),
            "product_capability_hash": product_capability_hash(tuple(PRODUCT_CAPABILITIES)),
            "setup_completed_at": setup_completed_at,
            "setup_plugin_build_id": setup_plugin_build_id,
            "runner_hash": str(contract.get("runner_sha256", "")),
            "runtime_generation_id": runtime_generation_id,
        }
        payload["contract_hash"] = contract_hash(payload)
        if persist:
            path = processing_engine_manifest_path(self.paths)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                os.replace(temporary, path)
            except OSError as exc:
                payload["cache_write_error"] = str(exc)
                failures += ("manifest_persistence",)
                state = ProcessingEngineState.FAILED
                payload["status"] = state.value
                payload["failed_components"] = list(failures)
        return ProcessingEngineReport(state, _summary_for_state(state), str(self.paths.python_executable), payload, failures)

    def require_ready(self) -> ProcessingEngineReport:
        report = self.verify()
        if report.ready:
            return report
        code = {
            ProcessingEngineState.SETUP_REQUIRED: "ENGINE_SETUP_REQUIRED",
            ProcessingEngineState.REPAIR_REQUIRED: "ENGINE_REPAIR_REQUIRED",
            ProcessingEngineState.INCOMPATIBLE: "ENGINE_PROTOCOL_MISMATCH",
        }.get(report.state, "ENGINE_IMPORT_FAILED")
        raise ProcessingEngineError(code, _summary_for_state(report.state), ", ".join(report.failed_components))

    def assert_ready_for(self, products: tuple[str, ...]) -> ProcessingRuntimeToken:
        report = self.require_ready()
        unsupported = tuple(product for product in products if product not in PRODUCT_CAPABILITIES and product not in {"dataset_inspection", "ept_subset_extract"})
        if unsupported:
            raise ProcessingEngineError("ENGINE_PRODUCT_UNSUPPORTED", "Processing Engine does not support the selected product.", ", ".join(unsupported))
        return _token_from_contract(report.contract, products)

    def validate_token(self, token: ProcessingRuntimeToken, products: tuple[str, ...]) -> None:
        current = self.assert_ready_for(products)
        if current.executable != token.executable or current.environment_fingerprint != token.environment_fingerprint or current.contract_hash != token.contract_hash or current.protocol != token.protocol or current.product_capability_hash != token.product_capability_hash:
            raise ProcessingEngineError("ENGINE_RUNTIME_CHANGED", "Processing Engine needs rechecking before this job can start.")


class ProcessingEngineService:
    """Single state, setup, contract, token, and environment facade."""

    def __init__(self, paths: BackendPaths, setup_callback: Callable[..., Any] | None = None) -> None:
        self.paths = paths
        self.verifier = ProcessingEngineVerifier(paths)
        self.setup_callback = setup_callback
        self._state: ProcessingEngineStateModel | None = None
        self._listeners: list[Callable[[ProcessingEngineStateModel], None]] = []

    def state(self, *, quick: bool = True) -> ProcessingEngineStateModel:
        # Quick state is deliberately re-read: a singleton cache must not outlive a
        # plugin build, manifest replacement, or external engine repair.
        report = self.verifier.quick() if quick else self.verifier.verify(require_setup_marker=True)
        return self._publish(report)

    def assert_ready_for(self, products: tuple[str, ...]) -> ProcessingRuntimeToken:
        return self.runtime_token_for(products)

    def runtime_token_for(self, products: tuple[str, ...]) -> ProcessingRuntimeToken:
        """Return a token derived from the one published READY manifest."""
        state = self.state(quick=True)
        if not state.ready_for_processing:
            state = self.state(quick=False)
        if not state.ready_for_processing or state.runtime_token is None:
            raise ProcessingEngineError(state.failure_code or "ENGINE_REPAIR_REQUIRED", state.message)
        if state.environment_fingerprint != environment_fingerprint(self.paths):
            report = ProcessingEngineReport(
                ProcessingEngineState.REPAIR_REQUIRED,
                "Processing Engine changed after verification.",
                str(self.paths.python_executable),
                state.runtime_token.to_dict(),
                ("environment_fingerprint",),
            )
            self._publish(report)
            raise ProcessingEngineError("ENGINE_RUNTIME_CHANGED", report.summary)
        unsupported = tuple(product for product in products if product not in PRODUCT_CAPABILITIES and product not in {"dataset_inspection", "ept_subset_extract"})
        if unsupported:
            raise ProcessingEngineError("ENGINE_PRODUCT_UNSUPPORTED", "Processing Engine does not support the selected product.", ", ".join(unsupported))
        token = state.runtime_token
        return ProcessingRuntimeToken(
            engine_id=token.engine_id,
            executable=token.executable,
            environment_fingerprint=token.environment_fingerprint,
            contract_hash=token.contract_hash,
            protocol=token.protocol,
            verified_at=token.verified_at,
            product_capability_hash=product_capability_hash(products),
            backend_runner_hash=token.backend_runner_hash,
            plugin_build_id=token.plugin_build_id,
            dependency_manifest_hash=token.dependency_manifest_hash,
            runtime_generation_id=token.runtime_generation_id,
        )

    def validate_runtime_token_for_launch(
        self,
        token: ProcessingRuntimeToken | None,
        products: tuple[str, ...],
        snapshot_folder: Path | None = None,
    ) -> dict[str, dict[str, str]]:
        """Validate one frozen token without selecting or verifying another runtime."""
        if token is None:
            raise ProcessingEngineError(
                "ENGINE_RUNTIME_TOKEN_MISSING",
                "Processing runtime identity is missing from the request.",
                "Runtime token missing from polygon request.",
            )
        manifest_path = processing_engine_manifest_path(self.paths)
        if not Path(token.executable).exists():
            raise ProcessingEngineError(
                "ENGINE_EXECUTABLE_MISSING",
                "Processing Engine changed since this job was checked.",
                f"Expected executable does not exist: {token.executable}",
            )
        try:
            contract = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ProcessingEngineError("ENGINE_MANIFEST_MISSING", "Processing Engine changed since this job was checked.", str(manifest_path)) from exc
        except (OSError, ValueError) as exc:
            raise ProcessingEngineError("ENGINE_MANIFEST_INVALID", "Processing Engine changed since this job was checked.", f"{manifest_path}: {exc}") from exc
        observed = _token_from_contract(contract)
        expected = token.to_dict()
        actual = observed.to_dict()
        actual["product_capability_hash"] = product_capability_hash(products)
        actual["environment_fingerprint"] = environment_fingerprint(self.paths)
        comparison: dict[str, dict[str, str]] = {}
        for field in expected:
            expected_value = str(expected.get(field, ""))
            actual_value = str(actual.get(field, ""))
            comparison[field] = {
                "status": "MATCH" if expected_value and expected_value == actual_value else ("MISSING" if not expected_value or not actual_value else "MISMATCH"),
                "expected": expected_value,
                "observed": actual_value,
            }
        comparison["normalized_executable"] = {
            "status": "MATCH" if _normalized_path(token.executable) == _normalized_path(observed.executable) else "MISMATCH",
            "expected": _normalized_path(token.executable),
            "observed": _normalized_path(observed.executable),
        }
        comparison["manifest_path"] = {"status": "MATCH", "expected": str(processing_engine_manifest_path(self.paths)), "observed": str(processing_engine_manifest_path(self.paths))}
        failures = {field: values for field, values in comparison.items() if values["status"] != "MATCH"}
        if snapshot_folder is not None:
            _write_runtime_snapshot(
                Path(snapshot_folder) / "processing_engine_launch_snapshot.json",
                token,
                comparison,
                manifest_path=processing_engine_manifest_path(self.paths),
            )
            _write_runtime_comparison(Path(snapshot_folder) / "runtime_token_comparison.json", comparison)
        if failures:
            details = "; ".join(f"{field}: expected {values['expected']!r}, observed {values['observed']!r}" for field, values in failures.items())
            code = _launch_mismatch_code(tuple(failures))
            raise ProcessingEngineError(code, "Processing Engine changed since this job was checked.", details)
        return comparison

    def environment(self) -> dict[str, str]:
        return build_processing_engine_environment(self.paths.environment_path, self.paths.platform.value)

    def ensure_processing_engine_ready(self, progress_callback=None) -> ProcessingEngineStateModel:
        """Reconcile, verify, and mark setup complete for the current plugin build."""
        current = self.verifier.verify(persist=False, require_setup_marker=False)
        if current.ready:
            return self._publish(self.verifier.verify(require_setup_marker=True, setup_completed=True))
        if self.setup_callback is None:
            raise ProcessingEngineError("ENGINE_SETUP_REQUIRED", "Processing Engine setup is not available.")
        with ProcessingEngineSetupLock(self.paths):
            result = self.setup_callback(progress_callback=progress_callback)
            if getattr(result, "success", True) is False:
                raise ProcessingEngineError(
                    "ENGINE_SETUP_FAILED", result.message,
                    str(getattr(result, "log_path", "") or ""),
                )
            final = self.verifier.verify(require_setup_marker=True, setup_completed=True)
            state = self._publish(final)
            if not state.ready_for_processing:
                raise ProcessingEngineError(state.failure_code or "ENGINE_REPAIR_REQUIRED", state.message, ", ".join(final.failed_components))
            return state

    def setup_or_repair(self, progress_callback=None) -> ProcessingEngineStateModel:
        """Compatibility wrapper for the authoritative ensure transaction."""
        return self.ensure_processing_engine_ready(progress_callback=progress_callback)

    def subscribe(self, listener: Callable[[ProcessingEngineStateModel], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def recheck(self) -> ProcessingEngineStateModel:
        return self._publish(self.verifier.verify(require_setup_marker=True))

    def _publish(self, report: ProcessingEngineReport) -> ProcessingEngineStateModel:
        state = ProcessingEngineStateModel.from_report(report)
        self._state = state
        if state.ready_for_processing and state.runtime_token is not None:
            _write_runtime_snapshot(
                self.paths.backend_root / "processing_engine_snapshot.json",
                state.runtime_token,
                manifest_path=processing_engine_manifest_path(self.paths),
            )
        for listener in tuple(self._listeners):
            listener(state)
        return state


def _normalized_path(value: str) -> str:
    return os.path.normcase(os.path.abspath(value)) if value else ""


def _write_runtime_snapshot(
    path: Path,
    token: ProcessingRuntimeToken,
    comparison: dict[str, dict[str, str]] | None = None,
    manifest_path: Path | None = None,
) -> None:
    payload: dict[str, Any] = {
        "processing_runtime": token.to_dict(),
        "manifest_path": str(manifest_path or path.parent / "processing_engine.json"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    if comparison is not None:
        payload["comparison"] = comparison
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def _write_runtime_comparison(path: Path, comparison: dict[str, dict[str, str]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"fields": comparison}, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def _token_from_contract(contract: dict[str, Any], products: tuple[str, ...] | None = None) -> ProcessingRuntimeToken:
    return ProcessingRuntimeToken(
        engine_id=str(contract.get("engine_id", "")),
        executable=str(contract.get("python_executable", "")),
        environment_fingerprint=str(contract.get("environment_fingerprint", "")),
        contract_hash=contract_hash(contract),
        protocol=str(contract.get("protocol_version", "")),
        verified_at=str(contract.get("verified_at", "")),
        product_capability_hash=product_capability_hash(products or tuple(PRODUCT_CAPABILITIES)),
        backend_runner_hash=str(contract.get("runner_sha256", "")),
        plugin_build_id=str(contract.get("plugin_build_id", "")),
        dependency_manifest_hash=str(contract.get("dependency_manifest_hash", "")),
        runtime_generation_id=str(contract.get("runtime_generation_id", "")),
    )


def _launch_mismatch_code(fields: tuple[str, ...]) -> str:
    """Translate objective token differences into one precise launch error."""
    priorities = (
        ("plugin_build_id", "ENGINE_PLUGIN_BUILD_CHANGED"),
        ("backend_runner_hash", "ENGINE_RUNNER_CHANGED"),
        ("executable", "ENGINE_EXECUTABLE_CHANGED"),
        ("normalized_executable", "ENGINE_EXECUTABLE_CHANGED"),
        ("runtime_generation_id", "ENGINE_RUNTIME_TOKEN_STALE"),
        ("contract_hash", "ENGINE_CONTRACT_CHANGED"),
        ("dependency_manifest_hash", "ENGINE_DEPENDENCIES_CHANGED"),
        ("product_capability_hash", "ENGINE_PRODUCT_CAPABILITIES_CHANGED"),
        ("protocol", "ENGINE_PROTOCOL_CHANGED"),
        ("environment_fingerprint", "ENGINE_ENVIRONMENT_CHANGED"),
        ("engine_id", "ENGINE_ID_CHANGED"),
    )
    values = set(fields)
    return next((code for field, code in priorities if field in values), "ENGINE_RUNTIME_TOKEN_MISMATCH")


def _failure_code(state: ProcessingEngineState) -> str:
    return {
        ProcessingEngineState.SETUP_REQUIRED: "ENGINE_SETUP_REQUIRED",
        ProcessingEngineState.REPAIR_REQUIRED: "ENGINE_REPAIR_REQUIRED",
        ProcessingEngineState.INCOMPATIBLE: "ENGINE_UPDATE_REQUIRED",
        ProcessingEngineState.FAILED: "ENGINE_VERIFICATION_FAILED",
    }.get(state, "ENGINE_NOT_READY")


def _summary_for_state(state: ProcessingEngineState) -> str:
    return {
        ProcessingEngineState.UNINITIALIZED: "Processing Engine has not been checked.",
        ProcessingEngineState.READY: "Processing Engine is ready.",
        ProcessingEngineState.CHECKING: "Processing Engine needs verification.",
        ProcessingEngineState.SETUP_REQUIRED: "Processing setup required.",
        ProcessingEngineState.UPDATING: "Processing Engine is being prepared.",
        ProcessingEngineState.REPAIR_REQUIRED: "PyForestScan's Processing Engine needs repair.",
        ProcessingEngineState.INCOMPATIBLE: "Processing Engine needs an update.",
        ProcessingEngineState.FAILED: "Processing Engine check failed.",
    }[state]
