"""Shared LiDAR source metadata for polygon source selection."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

from .lidar_catalog_builder import inspect_lidar_header, iter_lidar_paths
from .lidar_catalog_models import CatalogBuildOptions, LidarCatalogRecord, stable_root_id
from .lidar_inventory import LidarSourceRecord
from .spatial_selection import Bounds2D

HEADER_READER_VERSION = "phase27r-header-v1"


@dataclass(frozen=True)
class LidarSourceMetadata:
    """One source-path metadata record used by direct selection and catalogs."""

    path: Path
    canonical_path: Path
    source_type: str
    exists: bool
    readable: bool
    file_size: int
    modified_time_ns: int
    xmin: float | None = None
    xmax: float | None = None
    ymin: float | None = None
    ymax: float | None = None
    zmin: float | None = None
    zmax: float | None = None
    embedded_crs: str | None = None
    repository_crs_override: str | None = None
    effective_crs: str | None = None
    effective_crs_source: str = ""
    point_count: int | None = None
    metadata_reader: str = HEADER_READER_VERSION
    metadata_signature: str = ""
    status: str = "indexed"
    errors: tuple[str, ...] = ()

    @property
    def bounds(self) -> Bounds2D | None:
        if self.xmin is None or self.ymin is None or self.xmax is None or self.ymax is None:
            return None
        return Bounds2D(float(self.xmin), float(self.ymin), float(self.xmax), float(self.ymax))

    @property
    def has_finite_bounds(self) -> bool:
        bounds = self.bounds
        if bounds is None:
            return False
        return all(math.isfinite(value) for value in (bounds.xmin, bounds.ymin, bounds.xmax, bounds.ymax)) and bounds.xmin < bounds.xmax and bounds.ymin < bounds.ymax

    def with_repository_crs_override(self, crs: str | None) -> "LidarSourceMetadata":
        override = (crs or "").strip() or None
        effective = self.embedded_crs or override
        return LidarSourceMetadata(
            path=self.path,
            canonical_path=self.canonical_path,
            source_type=self.source_type,
            exists=self.exists,
            readable=self.readable,
            file_size=self.file_size,
            modified_time_ns=self.modified_time_ns,
            xmin=self.xmin,
            xmax=self.xmax,
            ymin=self.ymin,
            ymax=self.ymax,
            zmin=self.zmin,
            zmax=self.zmax,
            embedded_crs=self.embedded_crs,
            repository_crs_override=override,
            effective_crs=effective,
            effective_crs_source="embedded_metadata" if self.embedded_crs else ("legacy_repository_override" if override else ""),
            point_count=self.point_count,
            metadata_reader=self.metadata_reader,
            metadata_signature=_metadata_signature(self.path, self.file_size, self.modified_time_ns, self.metadata_reader, self.bounds, effective, self.point_count),
            status=self.status,
            errors=self.errors,
        )

    def to_source_record(self) -> LidarSourceRecord:
        return LidarSourceRecord(self.path, self.source_type, self.file_size, self.modified_time_ns, bounds=self.bounds, crs=self.effective_crs, point_count=self.point_count, zmin=self.zmin, zmax=self.zmax)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "canonical_path": str(self.canonical_path),
            "source_type": self.source_type,
            "exists": self.exists,
            "readable": self.readable,
            "file_size": self.file_size,
            "modified_time_ns": self.modified_time_ns,
            "bounds": None if self.bounds is None else self.bounds.__dict__,
            "embedded_crs": self.embedded_crs,
            "repository_crs_override": self.repository_crs_override,
            "effective_crs": self.effective_crs,
            "effective_crs_source": self.effective_crs_source,
            "point_count": self.point_count,
            "metadata_reader": self.metadata_reader,
            "metadata_signature": self.metadata_signature,
            "status": self.status,
            "errors": list(self.errors),
        }

    @classmethod
    def from_catalog_record(cls, record: LidarCatalogRecord, *, repository_crs_override: str | None = None) -> "LidarSourceMetadata":
        stat_exists = record.source_path.exists()
        canonical = _canonical(record.source_path)
        effective = record.source_crs or ((repository_crs_override or "").strip() or None)
        return cls(
            path=record.source_path,
            canonical_path=canonical,
            source_type=record.source_type,
            exists=stat_exists,
            readable=record.inventory_status == "indexed" and record.bounds is not None,
            file_size=record.file_size,
            modified_time_ns=record.modified_time_ns,
            xmin=record.xmin,
            xmax=record.xmax,
            ymin=record.ymin,
            ymax=record.ymax,
            zmin=record.zmin,
            zmax=record.zmax,
            embedded_crs=record.source_crs,
            repository_crs_override=(repository_crs_override or "").strip() or None,
            effective_crs=effective,
            effective_crs_source="embedded_metadata" if record.source_crs else ("legacy_repository_override" if repository_crs_override else ""),
            point_count=record.point_count,
            metadata_reader="catalog",
            metadata_signature=record.header_signature or _metadata_signature(record.source_path, record.file_size, record.modified_time_ns, "catalog", record.bounds, effective, record.point_count),
            status=record.inventory_status,
            errors=(record.metadata_error,) if record.metadata_error else (),
        )


class HeaderMetadataService:
    """Read source headers without reading full point arrays."""

    def __init__(self, *, reader_version: str = HEADER_READER_VERSION) -> None:
        self.reader_version = reader_version

    def discover(
        self,
        repository_root: Path | str,
        *,
        repository_crs_override: str | None = None,
        recursive: bool = True,
        options: CatalogBuildOptions | None = None,
    ) -> tuple[LidarSourceMetadata, ...]:
        root = Path(repository_root).expanduser()
        normalized = root.resolve() if root.exists() else root.absolute()
        scan_options = options or CatalogBuildOptions(recursive=recursive, source_types=("las", "laz", "copc"))
        if options is None and not recursive:
            scan_options = CatalogBuildOptions(recursive=False, source_types=("las", "laz", "copc"))
        root_id = stable_root_id(normalized)
        records = (
            self.inspect_path(path, normalized, root_id, repository_crs_override=repository_crs_override)
            for path in iter_lidar_paths(normalized, options=scan_options)
        )
        # Filesystem traversal order differs across platforms. Stable source
        # order keeps execution plans and their signatures reproducible.
        return tuple(sorted(records, key=lambda item: (str(item.path).casefold(), str(item.path))))

    def inspect_path(
        self,
        path: Path,
        repository_root: Path,
        root_id: str,
        *,
        repository_crs_override: str | None = None,
    ) -> LidarSourceMetadata:
        path = Path(path)
        canonical = _canonical(path)
        exists = path.exists()
        try:
            stat = path.stat()
            file_size = int(stat.st_size)
            modified = int(stat.st_mtime_ns)
        except OSError as exc:
            return LidarSourceMetadata(path, canonical, _source_type(path), exists, False, 0, 0, metadata_reader=self.reader_version, status="error", errors=(str(exc),))
        try:
            record = inspect_lidar_header(path, repository_root, root_id)
        except Exception as exc:  # noqa: BLE001 - per-source metadata errors are diagnostics, not crashes.
            return LidarSourceMetadata(path, canonical, _source_type(path), exists, False, file_size, modified, metadata_reader=self.reader_version, status="error", errors=(str(exc),))
        effective = record.source_crs or ((repository_crs_override or "").strip() or None)
        bounds = record.bounds
        readable = record.inventory_status == "indexed" and bounds is not None
        errors: tuple[str, ...] = ()
        if bounds is None:
            errors = ("Header bounds are unavailable.",)
        elif not _finite_bounds(bounds):
            errors = ("Header bounds are not finite or ordered.",)
            readable = False
        return LidarSourceMetadata(
            path=path,
            canonical_path=canonical,
            source_type=record.source_type,
            exists=exists,
            readable=readable,
            file_size=record.file_size,
            modified_time_ns=record.modified_time_ns,
            xmin=record.xmin,
            xmax=record.xmax,
            ymin=record.ymin,
            ymax=record.ymax,
            zmin=record.zmin,
            zmax=record.zmax,
            embedded_crs=record.source_crs,
            repository_crs_override=(repository_crs_override or "").strip() or None,
            effective_crs=effective,
            effective_crs_source="embedded_metadata" if record.source_crs else ("legacy_repository_override" if repository_crs_override else ""),
            point_count=record.point_count,
            metadata_reader=self.reader_version,
            metadata_signature=_metadata_signature(path, record.file_size, record.modified_time_ns, self.reader_version, bounds, effective, record.point_count),
            status="indexed" if readable else "error",
            errors=errors,
        )


def _canonical(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _source_type(path: Path) -> str:
    text = str(path).lower()
    if text.endswith(".copc.laz"):
        return "copc"
    return path.suffix.lower().lstrip(".") or "unknown"


def _finite_bounds(bounds: Bounds2D) -> bool:
    return all(math.isfinite(value) for value in (bounds.xmin, bounds.ymin, bounds.xmax, bounds.ymax)) and bounds.xmin < bounds.xmax and bounds.ymin < bounds.ymax


def _metadata_signature(path: Path, size: int, modified: int, reader: str, bounds: Bounds2D | None, crs: str | None, points: int | None) -> str:
    payload = "|".join(
        [
            str(_canonical(path)).casefold(),
            str(size),
            str(modified),
            reader,
            "" if bounds is None else f"{bounds.xmin:.12g},{bounds.ymin:.12g},{bounds.xmax:.12g},{bounds.ymax:.12g}",
            crs or "",
            "" if points is None else str(points),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
