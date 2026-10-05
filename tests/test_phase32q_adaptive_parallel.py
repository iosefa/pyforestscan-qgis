import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pyforestscan_qgis.core.adaptive_concurrency import (
    AdaptiveConcurrencyController,
    robust_eta_seconds,
    weighted_progress,
)
from pyforestscan_qgis.core.owned_workers import GlobalResourceGovernor, OwnedWorkerRegistry
from pyforestscan_qgis.core.work_unit_scheduler import WorkFailureCircuitBreaker, WorkUnitResult
from pyforestscan_qgis.core.source_aware_processing import SpatialExtent, WorkUnit, WorkUnitType
from pyforestscan_qgis.core.work_unit_scheduler import CheckpointStore, PolygonProductWorkScheduler


GIB = 1024 ** 3


class AdaptiveConcurrencyTests(unittest.TestCase):
    def test_network_controller_starts_at_one_and_keeps_two_as_safe_baseline(self):
        controller = AdaptiveConcurrencyController(5, "network", GIB, lambda: 32 * GIB, cpu_count=16)
        self.assertEqual(controller.target, 1)
        self.assertEqual(controller.ceiling, 2)
        for _index in range(7):
            controller.observe(WorkUnitResult("unit", "Complete", metrics={"worker_peak_rss": GIB, "ept_read_and_point_decode_seconds": 1.0}))
        self.assertEqual(controller.target, 2)
        controller.observe(WorkUnitResult("unit", "Complete", metrics={"worker_peak_rss": GIB, "ept_read_and_point_decode_seconds": 1.0}))
        self.assertEqual(controller.target, 3)
        self.assertTrue(controller.network_probation_attempted)

    def test_controller_backs_off_after_failure(self):
        controller = AdaptiveConcurrencyController(5, "local", GIB, lambda: 32 * GIB, cpu_count=16)
        controller.target = 4
        controller.observe(WorkUnitResult("unit", "Failed", error_code="EXECUTION_FAILED"))
        self.assertEqual(controller.target, 3)
        self.assertEqual(controller.health, "RESOURCE_LIMITED")

    def test_memory_ceiling_reserves_headroom(self):
        controller = AdaptiveConcurrencyController(5, "local", 2 * GIB, lambda: 5 * GIB, cpu_count=16)
        self.assertEqual(controller.ceiling, 1)

    def test_weighted_progress_and_robust_eta(self):
        self.assertEqual(weighted_progress(50, 20, 100), 60)
        eta, confidence = robust_eta_seconds([8, 10, 12, 10], pending=7, active=2, concurrency=3)
        self.assertEqual(eta, 30)
        self.assertEqual(confidence, "LOW_CONFIDENCE")

    def test_native_crash_requires_repetition_before_circuit_breaker_stops(self):
        breaker = WorkFailureCircuitBreaker()
        first = breaker.record(WorkUnitResult("wu-1", "Failed", error_code="NATIVE_BACKEND_CRASH"))
        second = breaker.record(WorkUnitResult("wu-2", "Failed", error_code="NATIVE_BACKEND_CRASH"))
        self.assertFalse(first.stop)
        self.assertTrue(second.stop)

    def test_owned_registry_terminates_only_registered_processes(self):
        process = SimpleNamespace(pid=42, terminated=False)
        process.poll = lambda: None
        process.terminate = lambda: setattr(process, "terminated", True)
        registry = OwnedWorkerRegistry()
        registry.register("worker", process)
        with patch("pyforestscan_qgis.core.owned_workers.terminate_process_tree", side_effect=lambda child: child.terminate()):
            registry.terminate_all()
        self.assertTrue(process.terminated)
        self.assertEqual(registry.snapshots()[0].worker_id, "worker")

    def test_global_governor_enforces_hard_slots_and_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            governor = GlobalResourceGovernor(Path(folder), maximum=2)
            first = governor.acquire("first", timeout=0.1)
            second = governor.acquire("second", timeout=0.1)
            self.assertEqual(len(tuple(Path(folder).glob("slot-*.json"))), 2)
            first.release()
            replacement = governor.acquire("replacement", timeout=0.1)
            self.assertEqual(len(tuple(Path(folder).glob("slot-*.json"))), 2)
            replacement.release()
            second.release()

    def test_ept_worker_and_ui_contract_are_process_isolated_and_truthful(self):
        root = Path(__file__).parents[1]
        polygon = (root / "pyforestscan_qgis/core/polygon_batch.py").read_text(encoding="utf-8")
        pages = (root / "pyforestscan_qgis/ui/pages.py").read_text(encoding="utf-8")
        self.assertIn("subprocess.Popen(command", polygon)
        self.assertIn("GlobalResourceGovernor().acquire", polygon)
        self.assertIn('"OMP_NUM_THREADS": "1"', polygon)
        self.assertIn("Processing strategy: Automatic", pages)
        self.assertIn("regions complete", pages)
        self.assertNotIn("Concurrency: requested", pages)

    def test_qhull_internal_failure_is_repeatable_transient_code(self):
        from pyforestscan_qgis.core.work_unit_scheduler import _exception_code

        self.assertEqual(_exception_code(RuntimeError("QH6108 qhull internal error")), "QHULL_INTERNAL_ERROR")

    def test_transient_retry_is_requeued_after_adaptive_backoff(self):
        extent = SpatialExtent(0, 0, 10, 10)
        units = tuple(WorkUnit(f"wu-{index}", WorkUnitType.EPT_WINDOW, (Path("ept.json"),), extent, extent, 0, 10, 0, 10, index, GIB) for index in range(2))
        attempts = {}
        with tempfile.TemporaryDirectory() as folder:
            def execute(unit, attempt):
                attempts[unit.work_unit_id] = attempts.get(unit.work_unit_id, 0) + 1
                if unit.work_unit_id == "wu-0" and attempt == 1:
                    raise RuntimeError("QH6108 qhull internal error")
                output = Path(folder) / f"{unit.work_unit_id}.tif"
                output.write_bytes(b"ok")
                return WorkUnitResult(unit.work_unit_id, "Complete", output)

            controller = AdaptiveConcurrencyController(2, "network", GIB, lambda: 16 * GIB, cpu_count=8)
            results = PolygonProductWorkScheduler(
                units, execute, CheckpointStore(Path(folder) / "checkpoints", "sig"),
                concurrency=2, transient=lambda exc: "qhull internal error" in str(exc).lower(),
                adaptive_controller=controller,
            ).run()
        self.assertTrue(all(result.status == "Complete" for result in results))
        self.assertEqual(attempts["wu-0"], 2)
        self.assertGreaterEqual(controller.failed, 1)


if __name__ == "__main__":
    unittest.main()
