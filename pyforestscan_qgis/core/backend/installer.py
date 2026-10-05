"""Guarded PBM installer with transactional production architecture."""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .checksums import ChecksumResult, verify_checksum
from .config import planned_backend_config, save_backend_config, utc_now_iso
from .downloads import DownloadResult, Downloader, download_file
from .logging import write_backend_log_entry
from .manifest import platform_subdir
from .micromamba import MicromambaBootstrapPolicy, micromamba_bootstrap_policy
from .models import BackendOperationResult, BackendPlatform, BackendRegistry, BackendStatus, BackendVerificationResult
from .paths import BackendPaths, backend_architecture, resolve_backend_paths
from .process_env import backend_pip_install_command, build_clean_subprocess_env, clean_env_summary, conda_environment_path_entries, hidden_subprocess_kwargs, summarize_subprocess_output
from .registry import default_backend_registry
from .verification import verify_backend

BACKEND_INSTALL_ENABLE_ENV = "PYFORESTSCAN_QGIS_ENABLE_BACKEND_INSTALL"

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
Verifier = Callable[[BackendPaths], BackendVerificationResult]


@dataclass(frozen=True)
class BackendInstallAvailability:
    """User-facing availability for real backend installer execution."""

    enabled: bool
    supported: bool
    platform: BackendPlatform
    build_allows_install: bool
    developer_override: bool
    reason: str
    button_label: str
    requires_confirmation: bool = True


@dataclass(frozen=True)
class StagingPaths:
    """Installer staging paths below the user-local backend root."""

    root: Path
    micromamba_dir: Path
    micromamba_executable: Path
    environment_path: Path
    python_executable: Path


def backend_install_availability(
    environ: dict[str, str] | None = None,
    platform: BackendPlatform | None = None,
    machine: str | None = None,
) -> BackendInstallAvailability:
    """Return whether real installer execution is available for this build/platform."""
    env = environ if environ is not None else os.environ
    platform_value = platform or resolve_backend_paths().platform
    developer_override = env.get(BACKEND_INSTALL_ENABLE_ENV) == "1"
    try:
        from ... import __version__ as version_metadata

        build_allows = bool(getattr(version_metadata, "INTERNAL_BETA_BACKEND_INSTALL", False))
        beta_platforms = tuple(str(item).lower() for item in getattr(version_metadata, "INTERNAL_BETA_BACKEND_INSTALL_PLATFORMS", ()))
    except Exception:  # noqa: BLE001 - installer availability must remain safe if metadata is unavailable.
        build_allows = False
        beta_platforms = ()

    architecture = backend_architecture(platform_value, machine)
    supported = (
        platform_value in (BackendPlatform.WINDOWS, BackendPlatform.LINUX) and architecture == "x86_64"
    ) or (platform_value is BackendPlatform.MACOS and architecture == "arm64")
    if developer_override:
        return BackendInstallAvailability(
            enabled=platform_value is not BackendPlatform.UNKNOWN,
            supported=platform_value is not BackendPlatform.UNKNOWN,
            platform=platform_value,
            build_allows_install=build_allows,
            developer_override=True,
            reason="Developer override is enabled for controlled installer testing.",
            button_label="Install Backend",
        )
    if not build_allows:
        return BackendInstallAvailability(
            enabled=False,
            supported=supported,
            platform=platform_value,
            build_allows_install=False,
            developer_override=False,
            reason="This build does not enable backend installation.",
            button_label="Install Backend (Planned)",
        )
    if platform_value.value in beta_platforms and supported:
        return BackendInstallAvailability(
            enabled=True,
            supported=True,
            platform=platform_value,
            build_allows_install=True,
            developer_override=False,
            reason=("Apple Silicon experimental backend installation is enabled; native Mac validation is pending."
                    if platform_value is BackendPlatform.MACOS else
                    f"{platform_value.value.title()} internal beta backend installation is enabled."),
            button_label="Install Backend",
        )
    if platform_value in (BackendPlatform.LINUX, BackendPlatform.MACOS):
        reason = "Backend installation is planned/experimental on this platform or architecture until platform smoke testing is complete."
    else:
        reason = "Backend installation is unavailable for this platform."
    return BackendInstallAvailability(
        enabled=False,
        supported=False,
        platform=platform_value,
        build_allows_install=build_allows,
        developer_override=False,
        reason=reason,
        button_label="Install Backend (Planned)",
    )


def backend_install_enabled(environ: dict[str, str] | None = None, platform: BackendPlatform | None = None, machine: str | None = None) -> bool:
    """Return whether real installer actions are enabled."""
    return backend_install_availability(environ=environ, platform=platform, machine=machine).enabled


def install_disabled_result(operation: str, platform: BackendPlatform | None = None, environ: dict[str, str] | None = None, machine: str | None = None) -> BackendOperationResult:
    """Return the standard refusal result when installer execution is unavailable."""
    availability = backend_install_availability(environ=environ, platform=platform, machine=machine)
    return BackendOperationResult(
        operation=operation,
        status=BackendStatus.NOT_INSTALLED,
        success=False,
        message=f"Backend installer is not available: {availability.reason}",
        modified_system=False,
    )


def staging_paths(paths: BackendPaths) -> StagingPaths:
    """Return the staging filesystem layout for one install attempt."""
    suffix = ".exe" if paths.platform is BackendPlatform.WINDOWS else ""
    env_python = "python.exe" if paths.platform is BackendPlatform.WINDOWS else "bin/python"
    return StagingPaths(
        root=paths.staging_dir,
        micromamba_dir=paths.staging_dir / "micromamba",
        micromamba_executable=paths.staging_dir / "micromamba" / f"micromamba{suffix}",
        environment_path=paths.staging_dir / "env",
        python_executable=paths.staging_dir / "env" / env_python,
    )


def staged_backend_paths(paths: BackendPaths) -> BackendPaths:
    """Return a BackendPaths object with exact staged executable/env paths."""
    staged = staging_paths(paths)
    return BackendPaths(
        platform=paths.platform,
        architecture=paths.architecture,
        backend_root=staged.root,
        micromamba_executable=staged.micromamba_executable,
        environment_path=staged.environment_path,
        python_executable=staged.python_executable,
        logs_dir=paths.logs_dir,
        config_file=staged.root / "backend.json",
        registry_file=staged.root / "registry.json",
        cache_dir=paths.cache_dir,
        downloads_dir=paths.downloads_dir,
        staging_dir=paths.staging_dir,
        scripts_dir=paths.scripts_dir,
        install_log=paths.install_log,
        download_log=paths.download_log,
        verify_log=paths.verify_log,
        repair_log=paths.repair_log,
        update_log=paths.update_log,
        remove_log=paths.remove_log,
    )


class BackendInstaller:
    """Developer-guarded installer operations used by transactional PBM."""

    def __init__(
        self,
        paths: BackendPaths,
        environ: dict[str, str] | None = None,
        downloader: Downloader | None = None,
        runner: CommandRunner | None = None,
        verifier: Verifier | None = None,
        registry: BackendRegistry | None = None,
    ) -> None:
        self.paths = paths
        self.environ = environ if environ is not None else os.environ
        self.downloader = downloader
        self.runner = runner or self._default_runner
        self.verifier = verifier
        self.registry = registry or default_backend_registry()

    def install_availability(self) -> BackendInstallAvailability:
        """Return real installer availability for this installer instance."""
        return backend_install_availability(self.environ, self.paths.platform, self.paths.architecture or None)

    def enabled(self) -> bool:
        """Return whether real installer actions are enabled."""
        return self.install_availability().enabled

    def require_enabled(self, operation: str) -> BackendOperationResult | None:
        """Return a refusal result when installer execution is unavailable."""
        if self.enabled():
            return None
        return install_disabled_result(operation, self.paths.platform, self.environ, self.paths.architecture or None)

    def plan_install(self) -> BackendOperationResult:
        """Return installer readiness without modifying the filesystem."""
        availability = self.install_availability()
        if not availability.enabled:
            return install_disabled_result("plan_install", self.paths.platform, self.environ, self.paths.architecture or None)
        return BackendOperationResult(
            operation="plan_install",
            status=BackendStatus.NOT_INSTALLED,
            success=True,
            message=f"{availability.reason} Installer will write only to the user-local PBM backend directory.",
            modified_system=False,
        )

    def prepare_staging(self) -> BackendOperationResult:
        """Create clean staging, download, and log directories under the backend root."""
        disabled = self.require_enabled("prepare_staging")
        if disabled:
            return disabled
        self.rollback_failed_install()
        self.paths.backend_root.mkdir(parents=True, exist_ok=True)
        self.paths.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
        staged = staging_paths(self.paths)
        staged.micromamba_dir.mkdir(parents=True, exist_ok=True)
        return BackendOperationResult(
            operation="prepare_staging",
            status=BackendStatus.INSTALLING,
            success=True,
            message=f"Created staging layout at {staged.root}.",
            modified_system=True,
        )

    def download_micromamba(self, policy: MicromambaBootstrapPolicy | None = None) -> DownloadResult:
        """Download the Micromamba archive into the user-local download cache."""
        disabled = self.require_enabled("download_micromamba")
        if disabled:
            return DownloadResult(False, disabled.message, self.paths.downloads_dir, "", attempts=0)
        policy_value = policy or micromamba_bootstrap_policy(self.paths)
        return download_file(policy_value.source_url, policy_value.download_path, retries=policy_value.retry_count, downloader=self.downloader)

    def verify_micromamba_download(self, policy: MicromambaBootstrapPolicy | None = None) -> ChecksumResult:
        """Verify the downloaded Micromamba archive checksum."""
        policy_value = policy or micromamba_bootstrap_policy(self.paths)
        return verify_checksum(policy_value.download_path, policy_value.checksum_policy)

    def extract_micromamba(self, policy: MicromambaBootstrapPolicy | None = None) -> BackendOperationResult:
        """Extract Micromamba into staging and place the executable at the staged path."""
        disabled = self.require_enabled("extract_micromamba")
        if disabled:
            return disabled
        policy_value = policy or micromamba_bootstrap_policy(self.paths)
        staged = staging_paths(self.paths)
        try:
            with tarfile.open(policy_value.download_path, "r:*") as archive:
                _safe_extract_tar(archive, staged.root)
            candidate = _find_extracted_micromamba(staged.root, self.paths.platform)
            if candidate is None:
                return BackendOperationResult("extract_micromamba", BackendStatus.FAILED, False, "Micromamba executable was not found after extraction.", True)
            staged.micromamba_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, staged.micromamba_executable)
            staged.micromamba_executable.chmod(staged.micromamba_executable.stat().st_mode | 0o700)
        except (OSError, tarfile.TarError) as exc:
            return BackendOperationResult("extract_micromamba", BackendStatus.FAILED, False, f"Micromamba extraction failed: {exc}", True)
        return BackendOperationResult("extract_micromamba", BackendStatus.INSTALLING, True, f"Extracted Micromamba to {staged.micromamba_executable}.", True)

    def create_environment(self, spec_file: Path | None = None) -> BackendOperationResult:
        """Create the staged backend environment using Micromamba."""
        disabled = self.require_enabled("create_environment")
        if disabled:
            return disabled
        staged = staging_paths(self.paths)
        spec = spec_file or default_environment_spec_file(self.paths.platform)
        command = [str(staged.micromamba_executable), "create", "-y", "--no-rc",
                   "--platform", platform_subdir(self.paths.platform, self.paths.architecture or None),
                   "-p", str(staged.environment_path), "-f", str(spec)]
        try:
            completed = self._run_subprocess(command, command_kind="micromamba_create", prepend_paths=(staged.micromamba_executable.parent,))
        except Exception as exc:  # noqa: BLE001 - installer reports failures.
            return BackendOperationResult("create_environment", BackendStatus.FAILED, False, f"Environment creation failed: {exc}", True)
        if completed.returncode != 0:
            output = summarize_subprocess_output(completed.stderr, completed.stdout) or "No subprocess output."
            return BackendOperationResult("create_environment", BackendStatus.FAILED, False, f"Environment creation failed: {output}", True)
        return BackendOperationResult("create_environment", BackendStatus.INSTALLING, True, f"Created staged backend environment at {staged.environment_path}.", True)

    def install_python_packages(self, *, paths: BackendPaths | None = None) -> BackendOperationResult:
        """Install and check PyPI packages using the selected backend Python."""
        disabled = self.require_enabled("install_python_packages")
        if disabled:
            return disabled
        staged = paths or staged_backend_paths(self.paths)
        packages = self.pip_packages()
        if not packages:
            return BackendOperationResult("install_python_packages", BackendStatus.INSTALLING, True, "No PyPI-only backend packages are required.", False)
        command = backend_pip_install_command(staged.python_executable, packages)
        try:
            completed = self._run_subprocess(command, command_kind="backend_python_pip", prepend_paths=conda_environment_path_entries(staged.environment_path, self.paths.platform.value))
        except Exception as exc:  # noqa: BLE001 - installer reports failures.
            return BackendOperationResult("install_python_packages", BackendStatus.FAILED, False, f"Backend Python package install failed: {exc}", True)
        if completed.returncode != 0:
            output = summarize_subprocess_output(completed.stderr, completed.stdout) or "No subprocess output."
            return BackendOperationResult("install_python_packages", BackendStatus.FAILED, False, f"Backend Python package install failed: {output}", True)
        checked = self._run_subprocess(
            [str(staged.python_executable), "-m", "pip", "check"],
            command_kind="backend_python_pip_check",
            prepend_paths=conda_environment_path_entries(staged.environment_path, self.paths.platform.value),
        )
        if checked.returncode != 0:
            output = summarize_subprocess_output(checked.stderr, checked.stdout)
            return BackendOperationResult("install_python_packages", BackendStatus.FAILED, False, f"Backend dependency requirements are not satisfied: {output}", True)
        return BackendOperationResult("install_python_packages", BackendStatus.INSTALLING, True, f"Installed PyPI-only backend packages with {staged.python_executable}.", True)

    def pip_packages(self) -> list[str]:
        """Return registry-driven PyPI package specifiers for backend Python pip."""
        packages: list[str] = []
        for dependency in self.registry.required_dependencies():
            if "pypi" not in dependency.source.lower():
                continue
            version_spec = dependency.version_spec.replace(" ", "")
            packages.append(f"{dependency.name}{version_spec}" if version_spec else dependency.name)
        return packages

    def verify_environment(self) -> BackendVerificationResult:
        """Verify the staged backend environment before promotion."""
        staged_paths = staged_backend_paths(self.paths)
        self._log_verification_paths("STAGED_VERIFY", staged_paths, require_config=False)
        return self._verify_backend_paths(staged_paths, require_config=False)

    def verify_active_backend(self) -> BackendVerificationResult:
        """Verify the active backend after promotion and config write."""
        self._log_verification_paths("FINAL_VERIFY", self.paths, require_config=True)
        return self._verify_backend_paths(self.paths, require_config=True)

    def _verify_backend_paths(self, paths: BackendPaths, require_config: bool) -> BackendVerificationResult:
        if self.verifier is not None:
            return self.verifier(paths)
        return verify_backend(paths, self.registry, require_config=require_config, log_path=self.paths.install_log, log_stage="STAGED_VERIFY" if not require_config else "FINAL_VERIFY")

    def _log_verification_paths(self, stage: str, paths: BackendPaths, require_config: bool) -> None:
        write_backend_log_entry(
            self.paths.install_log,
            "install",
            "Verifying PBM backend paths.",
            stage=stage,
            details={
                "backend_root": str(paths.backend_root),
                "micromamba": str(paths.micromamba_executable),
                "environment": str(paths.environment_path),
                "python": str(paths.python_executable),
                "config": str(paths.config_file),
                "require_config": str(require_config).lower(),
            },
        )

    def promote_staging(self) -> BackendOperationResult:
        """Promote verified staged files into the active backend layout."""
        disabled = self.require_enabled("promote_staging")
        if disabled:
            return disabled
        staged = staging_paths(self.paths)
        if not staged.micromamba_executable.exists() or not staged.environment_path.exists():
            return BackendOperationResult("promote_staging", BackendStatus.FAILED, False, "Staging is incomplete; cannot promote backend.", False)
        backup_root = _promotion_backup_dir(self.paths)
        try:
            if backup_root.exists():
                shutil.rmtree(backup_root)
            backup_root.mkdir(parents=True, exist_ok=True)
            _backup_active_backend(self.paths, backup_root)
            self.paths.micromamba_executable.parent.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged.micromamba_dir), str(self.paths.micromamba_executable.parent))
            if self.paths.platform is BackendPlatform.WINDOWS:
                shutil.move(str(staged.environment_path), str(self.paths.environment_path))
            else:
                # Conda prefixes contain absolute paths in scripts and native
                # libraries. A filesystem move leaves references to staging.
                completed = self._run_subprocess(
                    [str(self.paths.micromamba_executable), "create", "-y", "--offline", "--no-rc",
                     "--platform", platform_subdir(self.paths.platform, self.paths.architecture or None),
                     "-p", str(self.paths.environment_path), "--clone", str(staged.environment_path)],
                    command_kind="micromamba_promote",
                )
                if completed.returncode != 0:
                    raise OSError(summarize_subprocess_output(completed.stderr, completed.stdout))
                # Micromamba clones Conda-managed files, not pip-installed
                # packages. Install those into the final prefix explicitly.
                packages = self.install_python_packages(paths=self.paths)
                if not packages.success:
                    raise OSError(packages.message)
        except OSError as exc:
            _restore_promotion_backup(self.paths, backup_root)
            return BackendOperationResult("promote_staging", BackendStatus.FAILED, False, f"Promotion failed and previous backend was restored when available: {exc}", True)
        return BackendOperationResult("promote_staging", BackendStatus.VERIFYING, True, "Promoted staged backend files into the active backend layout; previous backend backup will be removed after final verification.", True)

    def write_backend_config(self, status: BackendStatus = BackendStatus.READY) -> BackendOperationResult:
        """Write backend config after successful verification."""
        disabled = self.require_enabled("write_backend_config")
        if disabled:
            return disabled
        config = planned_backend_config(self.paths)
        config = type(config)(
            backend_version=config.backend_version,
            backend_root=config.backend_root,
            environment_path=config.environment_path,
            python_executable=config.python_executable,
            micromamba_executable=config.micromamba_executable,
            created_at=config.created_at,
            updated_at=utc_now_iso(),
            platform=config.platform,
            status=status,
            registry=config.registry,
        )
        save_backend_config(config, self.paths.config_file)
        return BackendOperationResult("write_backend_config", status, True, f"Wrote backend config at {self.paths.config_file}.", True)

    def rollback_failed_install(self) -> BackendOperationResult:
        """Remove staging files after a failed install attempt, restoring active backup if needed."""
        restored = False
        backup_root = _promotion_backup_dir(self.paths)
        if backup_root.exists():
            restored = _restore_promotion_backup(self.paths, backup_root)
        if self.paths.staging_dir.exists():
            shutil.rmtree(self.paths.staging_dir)
            message = f"Removed staging directory {self.paths.staging_dir}."
            if restored:
                message += " Restored previous active backend from promotion backup."
            return BackendOperationResult("rollback_failed_install", BackendStatus.REPAIR_REQUIRED, True, message, True)
        if restored:
            return BackendOperationResult("rollback_failed_install", BackendStatus.REPAIR_REQUIRED, True, "Restored previous active backend from promotion backup.", True)
        return BackendOperationResult("rollback_failed_install", BackendStatus.NOT_INSTALLED, True, "No staging directory was present.", False)

    def cleanup_successful_install(self) -> BackendOperationResult:
        """Remove staging and promotion backups after final verification passes."""
        if self.paths.staging_dir.exists():
            shutil.rmtree(self.paths.staging_dir)
            return BackendOperationResult("cleanup_successful_install", BackendStatus.READY, True, f"Removed staging directory {self.paths.staging_dir} after successful verification.", True)
        return BackendOperationResult("cleanup_successful_install", BackendStatus.READY, True, "No staging directory was present after successful verification.", False)

    def install_backend(self, policy: MicromambaBootstrapPolicy | None = None, spec_file: Path | None = None, progress_callback=None) -> BackendOperationResult:
        """Run the guarded transactional installer when explicitly enabled."""
        from .logging import write_backend_log_entry
        from .transaction import BackendInstallTransaction

        def log_stage(stage: str, severity: str, message: str) -> None:
            write_backend_log_entry(self.paths.install_log, "install", message, level=severity, stage=stage)

        transaction = BackendInstallTransaction(self, logger=log_stage, progress_callback=progress_callback)
        result = transaction.run(policy=policy, spec_file=spec_file)
        if not result.success:
            write_backend_log_entry(self.paths.install_log, "install", result.message, level="ERROR", stage=result.stage.value if result.stage else "FAILED")
        return BackendOperationResult(
            operation="install_backend",
            status=result.status,
            success=result.success,
            message=result.message,
            modified_system=result.modified_system,
            log_path=self.paths.install_log if self.paths.install_log.exists() else None,
        )

    def _run_subprocess(self, command: list[str], command_kind: str, prepend_paths: tuple[Path, ...] = ()) -> subprocess.CompletedProcess[str]:
        """Run an installer subprocess with a sanitized PBM environment."""
        env = build_clean_subprocess_env(
            self.environ, prepend_paths=prepend_paths,
            extra_env={
                "MAMBA_ROOT_PREFIX": str(self.paths.cache_dir / "mamba"),
                "MAMBA_DOWNLOAD_THREADS": "2",
                "MAMBA_EXTRACT_THREADS": "2",
            },
        )
        details = clean_env_summary(command_kind, command[0])
        write_backend_log_entry(self.paths.install_log, "install", "Running PBM installer subprocess with sanitized environment.", stage=command_kind.upper(), details=details)
        completed = self.runner(command, check=False, capture_output=True, text=True, timeout=1800, env=env, **hidden_subprocess_kwargs())
        if completed.returncode != 0:
            failure_details = dict(details)
            failure_details["returncode"] = str(completed.returncode)
            failure_details["stderr_preview"] = summarize_subprocess_output(completed.stderr, completed.stdout)
            write_backend_log_entry(self.paths.install_log, "install", "PBM installer subprocess failed.", level="ERROR", stage=command_kind.upper(), details=failure_details)
        return completed

    def _default_runner(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, **kwargs)


def default_environment_spec_file(platform: BackendPlatform) -> Path:
    """Return the platform-specific environment spec file path."""
    source_root = Path(__file__).resolve().parents[3]
    package_root = Path(__file__).resolve().parents[2]
    for spec_dir in (source_root / "backend_specs", package_root / "backend_specs"):
        if spec_dir.exists():
            if platform is BackendPlatform.WINDOWS:
                return spec_dir / "environment.windows.yml"
            if platform is BackendPlatform.LINUX:
                return spec_dir / "environment.linux.yml"
            if platform is BackendPlatform.MACOS:
                return spec_dir / "environment.macos.yml"
            return spec_dir / "environment.yml"
    return source_root / "backend_specs" / "environment.yml"


def _promotion_backup_dir(paths: BackendPaths) -> Path:
    return paths.staging_dir / "promotion_backup"


def _backup_active_backend(paths: BackendPaths, backup_root: Path) -> None:
    micromamba_dir = paths.micromamba_executable.parent
    targets = (
        (micromamba_dir, backup_root / "micromamba", True),
        (paths.environment_path, backup_root / "env", True),
        (paths.config_file, backup_root / "backend.json", False),
    )
    for source, destination, is_dir in targets:
        if not source.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if is_dir:
            shutil.move(str(source), str(destination))
        else:
            shutil.copy2(source, destination)


def _restore_promotion_backup(paths: BackendPaths, backup_root: Path) -> bool:
    if not backup_root.exists():
        return False
    restored = False
    dir_targets = (
        (backup_root / "micromamba", paths.micromamba_executable.parent),
        (backup_root / "env", paths.environment_path),
    )
    for backup, target in dir_targets:
        if target.exists():
            shutil.rmtree(target)
        if backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(target))
            restored = True
    config_backup = backup_root / "backend.json"
    if paths.config_file.exists():
        paths.config_file.unlink()
    if config_backup.exists():
        paths.config_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(config_backup), str(paths.config_file))
        restored = True
    # A transaction may request rollback again after promote_staging restored
    # the previous environment. Do not leave an empty backup that would cause
    # that second rollback to delete the restored environment.
    shutil.rmtree(backup_root)
    return restored


def _safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    """Extract a tar archive after rejecting path traversal and unsafe links."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    for member in archive.getmembers():
        member_path = (destination / member.name).resolve()
        _require_within_directory(member_path, root)
        if member.issym() or member.islnk():
            link_target = (member_path.parent / member.linkname).resolve()
            _require_within_directory(link_target, root)
    archive.extractall(destination)


def _require_within_directory(candidate: Path, root: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise tarfile.TarError(f"Archive member escapes extraction directory: {candidate}") from exc


def _find_extracted_micromamba(root: Path, platform: BackendPlatform) -> Path | None:
    expected = "micromamba.exe" if platform is BackendPlatform.WINDOWS else "micromamba"
    for candidate in root.rglob(expected):
        if candidate.is_file():
            return candidate
    return None
