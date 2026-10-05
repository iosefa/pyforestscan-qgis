"""Service facade for the PyForestScan Backend Manager."""

from __future__ import annotations

import uuid
from pathlib import Path

from .checksums import ChecksumResult
from .config import load_backend_config, planned_backend_config
from .downloads import DownloadResult
from .execution import BackendExecutionAvailability, BackendExecutionService
from .install_plan import BackendInstallPlan, create_backend_install_plan, format_install_plan
from .installer import BackendInstallAvailability, BackendInstaller, backend_install_availability, backend_install_enabled
from .logging import backend_log_path, read_backend_log, write_backend_log_entry
from .manifest import BackendManifest, load_backend_manifest
from .models import BackendOperationResult, BackendRegistry, BackendState, BackendStatus, BackendVerificationResult
from .modules import BackendModuleRegistry, default_backend_module_registry
from .paths import BackendPaths, resolve_backend_paths
from .registry import default_backend_registry
from .repair import RepairPlan, format_repair_plan, plan_backend_repair
from .state import detect_backend_state
from .verification import format_verification_result, verify_backend
from .processing_engine import ProcessingEngineService, ProcessingEngineVerifier
from .version_manager import BackendVersionManager, VersionCompatibilityResult


_PROCESSING_ENGINE_SERVICES: dict[str, ProcessingEngineService] = {}


class BackendService:
    """Detect, verify, preview, repair-plan, and guard the user-local backend."""

    def __init__(self, paths: BackendPaths | None = None, registry: BackendRegistry | None = None, plugin_version: str = "0.1.0") -> None:
        """Create a backend service using resolved paths and registry data."""
        self.paths = paths or resolve_backend_paths()
        self.plugin_version = plugin_version
        try:
            self.manifest = load_backend_manifest()
        except Exception:  # noqa: BLE001 - Settings must remain usable with a bad packaged manifest.
            self.manifest = None
        self.registry = registry or (self.manifest.registry() if self.manifest is not None else default_backend_registry())

    def detect_backend(self) -> BackendState:
        """Detect backend installation state without modifying files."""
        return detect_backend_state(self.paths)

    def verify_backend(self) -> BackendVerificationResult:
        """Run placeholder-safe backend verification."""
        result = verify_backend(self.paths, self.registry)
        if self.paths.logs_dir.exists():
            write_backend_log_entry(self.paths.verify_log, "verify", result.summary, details={"status": result.status.value}, stage="VERIFY")
        return result

    def preview_install_plan(self) -> BackendInstallPlan:
        """Return the dry-run backend install plan without modifying files."""
        return create_backend_install_plan(self.paths, self.registry, self.manifest)

    def format_install_plan(self, plan: BackendInstallPlan | None = None) -> str:
        """Format the dry-run install plan for UI display."""
        return format_install_plan(plan or self.preview_install_plan())

    def backend_manifest(self) -> BackendManifest | None:
        """Return the loaded backend manifest, if valid."""
        return self.manifest

    def version_compatibility(self) -> VersionCompatibilityResult | None:
        """Return plugin/backend manifest compatibility, if a manifest is available."""
        if self.manifest is None:
            return None
        return BackendVersionManager(self.plugin_version).check_manifest(self.manifest)

    def module_registry(self) -> BackendModuleRegistry:
        """Return future backend module registry placeholders."""
        return default_backend_module_registry()

    def execution_service(self) -> BackendExecutionService:
        """Return the controlled PBM processing execution service."""
        return BackendExecutionService(self.paths, verifier=self.verify_backend, engine_service=self.processing_engine_service())

    def can_execute_processing(self) -> BackendExecutionAvailability:
        """Return whether PBM backend processing can run now."""
        return self.execution_service().can_execute_processing()

    def processing_engine_state(self, *, quick: bool = False):
        """Return the one user-facing Processing Engine readiness report."""
        return self.processing_engine_service().state(quick=quick)

    def processing_engine_service(self) -> ProcessingEngineService:
        """Return the single setup, state, contract, and runtime-token owner."""
        key = str(self.paths.backend_root.resolve())
        service = _PROCESSING_ENGINE_SERVICES.get(key)
        if service is None:
            service = ProcessingEngineService(self.paths, setup_callback=self.install_backend)
            _PROCESSING_ENGINE_SERVICES[key] = service
        elif service.setup_callback is None:
            service.setup_callback = self.install_backend
        return service

    def setup_processing_engine(self, progress_callback=None):
        """Idempotently install or repair the managed engine under one process lock."""
        return self.ensure_processing_engine_ready(progress_callback=progress_callback)

    def ensure_processing_engine_ready(self, progress_callback=None):
        """Run the one authoritative setup/reload transaction."""
        return self.processing_engine_service().ensure_processing_engine_ready(progress_callback=progress_callback)

    def verify_runner(self) -> BackendExecutionAvailability:
        """Verify the PBM backend runner module."""
        return self.execution_service().verify_runner()

    def write_job_spec(self, product: str, request):
        """Write a PBM backend job spec for a product request."""
        return self.execution_service().write_job_spec(product, request)

    def read_job_result(self, result_path: Path):
        """Read a PBM backend job result."""
        return self.execution_service().read_job_result(result_path)

    def run_processing_job(self, spec, spec_path: Path | None = None):
        """Run one PBM backend processing job spec."""
        return self.execution_service().run_processing_job(spec, spec_path)

    def submit_polygon_coordinator(self,payload_path:Path,job_dir:Path,runtime_token,products,*,generic:bool=False):
        """Launch the durable source-aware polygon coordinator."""
        return self.execution_service().submit_polygon_coordinator(payload_path,job_dir,runtime_token,products,generic=generic)

    def run_product(self, product: str, request, runtime_token=None, runtime_products=()):
        """Run one product through the PBM backend execution service."""
        return self.execution_service().run_product(
            product,
            request,
            runtime_token=runtime_token,
            runtime_products=runtime_products,
        )

    def run_dataset_inspection(self, input_path: Path, crs: str, options: dict[str, object], run_folder: Path):
        """Run Dataset Explorer inspection through PBM backend Python."""
        from pyforestscan_qgis.backend_runner.job_spec import BackendJobSpec

        job_id = f"pbm-dataset-inspection-{input_path.stem}-{uuid.uuid4().hex[:12]}"
        spec = BackendJobSpec(
            job_id=job_id,
            input_lidar_path=input_path,
            crs=crs,
            run_folder=run_folder,
            product="dataset_inspection",
            product_parameters=dict(options),
            output_paths={},
            result_path=run_folder / ".pbm_jobs" / f"{job_id}.result.json",
        )
        spec_path = spec.write()
        return self.execution_service().run_processing_job(spec, spec_path)


    def catalog_runner_command(self, spec_path: Path) -> list[str]:
        """Return the controlled PBM backend command for a LiDAR catalog job."""
        return self.execution_service().catalog_runner_command(spec_path)

    def preview_repair_plan(self) -> RepairPlan:
        """Return a non-mutating backend repair plan."""
        return plan_backend_repair(self.paths, self.manifest)

    def format_repair_plan(self, plan: RepairPlan | None = None) -> str:
        """Format repair diagnostics for UI display."""
        return format_repair_plan(plan or self.preview_repair_plan())

    def install_availability(self) -> BackendInstallAvailability:
        """Return user-facing installer availability for the current build/platform."""
        return backend_install_availability(platform=self.paths.platform, machine=self.paths.architecture or None)

    def backend_install_enabled(self) -> bool:
        """Return whether real backend installation is enabled."""
        return backend_install_enabled(platform=self.paths.platform, machine=self.paths.architecture or None)

    def installer(self) -> BackendInstaller:
        """Return a controlled installer bound to current paths."""
        return BackendInstaller(self.paths, registry=self.registry)

    def plan_install(self) -> BackendOperationResult:
        """Return developer installer readiness without modifying files."""
        return self.installer().plan_install()

    def download_micromamba(self) -> DownloadResult:
        """Download Micromamba through the controlled installer path."""
        return self.installer().download_micromamba()

    def verify_micromamba_download(self) -> ChecksumResult:
        """Verify the Micromamba download checksum."""
        return self.installer().verify_micromamba_download()

    def extract_micromamba(self) -> BackendOperationResult:
        """Extract Micromamba through the controlled installer path."""
        return self.installer().extract_micromamba()

    def create_environment(self) -> BackendOperationResult:
        """Create the managed backend environment through the controlled installer path."""
        return self.installer().create_environment()

    def verify_environment(self) -> BackendVerificationResult:
        """Verify the staged backend environment."""
        return self.installer().verify_environment()

    def write_backend_config(self) -> BackendOperationResult:
        """Write backend config through the controlled installer path."""
        return self.installer().write_backend_config()

    def rollback_failed_install(self) -> BackendOperationResult:
        """Rollback staging through the installer path."""
        return self.installer().rollback_failed_install()

    def install_backend(self, progress_callback=None) -> BackendOperationResult:
        """Run the transactional installer when the availability guard allows it."""
        return self.installer().install_backend(progress_callback=progress_callback)

    def repair_backend(self) -> BackendOperationResult:
        """Return repair planning while repair execution remains planned."""
        plan = self.preview_repair_plan()
        if self.paths.logs_dir.exists():
            write_backend_log_entry(self.paths.repair_log, "repair", f"Repair plan has {len(plan.issues)} issue(s).", stage="PLAN")
        return BackendOperationResult(
            operation="repair",
            status=plan.status,
            success=False,
            message=f"Backend repair execution is planned. {len(plan.issues)} issue(s) detected; use logs and retry installation on supported internal beta builds.",
            modified_system=False,
        )

    def update_backend(self) -> BackendOperationResult:
        """Return a planned-operation result; no update occurs in Phase 22D."""
        return self._planned_operation("update", self.detect_backend().status)

    def remove_backend(self) -> BackendOperationResult:
        """Return a planned-operation result; no removal occurs in Phase 22D."""
        return self._planned_operation("remove", self.detect_backend().status)

    def open_backend_folder_path(self) -> Path:
        """Return the user-local backend root path for UI integrations."""
        return self.paths.backend_root

    def get_logs(self) -> dict[str, tuple[str, ...]]:
        """Return recent backend log lines by operation."""
        return {
            "install": read_backend_log(self.paths.install_log),
            "download": read_backend_log(self.paths.download_log),
            "verify": read_backend_log(self.paths.verify_log),
            "repair": read_backend_log(self.paths.repair_log),
            "update": read_backend_log(self.paths.update_log),
            "remove": read_backend_log(self.paths.remove_log),
            "execute": read_backend_log(backend_log_path("execute", self.paths.logs_dir)),
        }

    def get_registry(self) -> BackendRegistry:
        """Return the dependency registry, preferring persisted config when valid."""
        try:
            config = load_backend_config(self.paths.config_file)
        except Exception:  # noqa: BLE001 - bad config should not break settings UI.
            return self.registry
        return config.registry if config is not None else self.registry

    def planned_config(self):
        """Return the planned config object without writing it to disk."""
        return planned_backend_config(self.paths)

    def locate_executable(self, executable_name: str) -> Path | None:
        """Locate a backend executable by name if it exists in the managed paths."""
        candidates = [self.paths.micromamba_executable]
        bin_dir = self.paths.environment_path / ("Scripts" if self.paths.platform.value == "windows" else "bin")
        candidates.append(bin_dir / executable_name)
        if self.paths.platform.value == "windows" and not executable_name.lower().endswith(".exe"):
            candidates.append(bin_dir / f"{executable_name}.exe")
        for candidate in candidates:
            if candidate.name.lower() == executable_name.lower() or candidate.stem.lower() == executable_name.lower():
                if candidate.exists():
                    return candidate
        return None

    def run_backend_python(self, args: tuple[str, ...] = ()) -> BackendOperationResult:
        """Return a safe placeholder for future backend Python execution."""
        return BackendOperationResult(
            operation="run_backend_python",
            status=self.detect_backend().status,
            success=False,
            message="Backend Python execution is planned for the PBM execution bridge; current scientific tools still use QGIS Python unless explicitly routed.",
            modified_system=False,
        )

    def run_pdal_pipeline(self, pipeline_path: Path | None = None) -> BackendOperationResult:
        """Return a safe placeholder for future backend PDAL execution."""
        detail = f" Pipeline: {pipeline_path}" if pipeline_path else ""
        return BackendOperationResult(
            operation="run_pdal_pipeline",
            status=self.detect_backend().status,
            success=False,
            message=f"Backend PDAL execution is planned for the PBM execution bridge; current scientific tools still use QGIS Python unless explicitly routed.{detail}",
            modified_system=False,
        )

    def format_verification_report(self, result: BackendVerificationResult | None = None) -> str:
        """Format verification details for UI or logs."""
        return format_verification_result(result or self.verify_backend())

    def _planned_operation(self, operation: str, status: BackendStatus) -> BackendOperationResult:
        return BackendOperationResult(
            operation=operation,
            status=status,
            success=False,
            message=f"Backend {operation} is planned. Phase 23C enables internal beta install on Windows, but update/remove execution remains disabled.",
            modified_system=False,
        )
