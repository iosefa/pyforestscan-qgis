"""QGIS-free tests for Mission Control beta UX summary language."""

from __future__ import annotations

import unittest
from pathlib import Path

from pyforestscan_qgis.ui.ux_summary import (
    action_icon_intent,
    backend_summary_from_environment,
    button_role_for_label,
    design_spacing_tokens,
    design_status_labels,
    empty_state_message,
    environment_headline,
    expandable_section_labels,
    primary_action_label,
    home_environment_action_label,
    home_environment_readiness,
    qgis_fallback_summary,
    readiness_marker_label,
    readiness_marker_tokens,
    readiness_status_text,
    status_badge_label,
    status_badge_tone,
    status_display_word,
    technical_wording_is_advanced,
    routed_products_summary,
    technical_sections_default_collapsed,
    workflow_action_labels,
    guided_next_step,
    guided_workflow_indicator,
    guided_workflow_pages,
    guided_workflow_status_lines,
)

ROOT = Path(__file__).resolve().parents[1]


class MissionControlUxTests(unittest.TestCase):
    """Verify compact workflow labels without importing QGIS."""

    def test_home_action_labels_match_beta_workflow(self) -> None:
        self.assertEqual(workflow_action_labels(), ("Continue", "Check Environment", "Continue to Dataset"))

    def test_empty_states_are_concise_guidance(self) -> None:
        self.assertEqual(empty_state_message("advisor"), "Analyze a dataset to receive recommendations.")
        self.assertEqual(empty_state_message("results"), "No outputs yet.\nRun processing to generate scientific products.")
        self.assertEqual(empty_state_message("workspace"), "Open or create a workspace to begin.")
        self.assertEqual(empty_state_message("dataset"), "No dataset selected.\nSelect a LAS, LAZ, or COPC dataset to begin.")

    def test_primary_action_labels_are_standardized(self) -> None:
        self.assertEqual(primary_action_label("dataset"), "Analyze Dataset")
        self.assertEqual(primary_action_label("home"), "Continue")
        self.assertEqual(primary_action_label("planning"), "Continue to Processing")
        self.assertEqual(primary_action_label("advisor"), "Review Recommendations")
        self.assertEqual(primary_action_label("processing"), "Run Processing")
        self.assertEqual(primary_action_label("settings"), "Verify Backend")

    def test_design_system_status_badges_are_standardized(self) -> None:
        self.assertEqual(design_status_labels(), ("READY", "RUNNING", "WARNING", "FAILED", "NOT CONFIGURED", "DISABLED", "PLANNED"))
        self.assertEqual(status_badge_label("Ready"), "READY")
        self.assertEqual(status_badge_label("Backend Ready"), "READY")
        self.assertEqual(status_badge_label("Repair Required"), "WARNING")
        self.assertEqual(status_badge_label("not started"), "NOT CONFIGURED")
        self.assertEqual(status_badge_tone("READY"), "success")
        self.assertEqual(status_badge_tone("not_configured"), "neutral")
        self.assertEqual(status_badge_tone("FAILED"), "danger")

    def test_design_system_button_roles_are_standardized(self) -> None:
        self.assertEqual(button_role_for_label("Continue to Planning"), "primary")
        self.assertEqual(button_role_for_label("Open Batch"), "secondary")
        self.assertEqual(button_role_for_label("Run Processing"), "primary")
        self.assertEqual(button_role_for_label("Analyze Dataset"), "primary")
        self.assertEqual(button_role_for_label("Build Plan"), "primary")
        self.assertEqual(button_role_for_label("Open Output Folder"), "secondary")
        self.assertEqual(button_role_for_label("Refresh Environment"), "neutral")
        self.assertEqual(button_role_for_label("Refresh Dataset Page"), "neutral")
        self.assertEqual(button_role_for_label("Refresh Plan State"), "neutral")
        self.assertEqual(button_role_for_label("Refresh Processing State"), "neutral")
        self.assertEqual(button_role_for_label("Refresh Results"), "neutral")
        self.assertEqual(button_role_for_label("Clear Current Run"), "danger")


    def test_product_polish_icon_and_wording_helpers_are_standardized(self) -> None:
        self.assertEqual(action_icon_intent("Install Backend"), "install")
        self.assertEqual(action_icon_intent("Load Outputs"), "load")
        self.assertEqual(action_icon_intent("Verify Backend"), "verify")
        self.assertEqual(action_icon_intent("Open Output Folder"), "folder")
        self.assertEqual(status_display_word("PASS"), "Ready")
        self.assertEqual(status_display_word("FAIL"), "Failed")
        self.assertEqual(status_display_word("Repair Required"), "Needs review")
        self.assertTrue(technical_wording_is_advanced("Ready to process with PBM backend."))
        self.assertFalse(technical_wording_is_advanced("Manifest registry details are visible."))

    def test_design_system_spacing_and_expandable_labels(self) -> None:
        self.assertEqual(design_spacing_tokens(), {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24})
        self.assertEqual(expandable_section_labels(), ("Advanced", "Technical Details", "Troubleshooting"))

    def test_mission_control_uses_design_tokens_and_visual_hooks(self) -> None:
        source = (ROOT / "pyforestscan_qgis/ui/pages.py").read_text(encoding="utf-8")
        stylesheet = (ROOT / "pyforestscan_qgis/ui/mission_control.py").read_text(encoding="utf-8")

        self.assertIn("def _apply_button_role(button: QPushButton", source)
        self.assertIn("def _apply_action_icon(button: QPushButton)", source)
        self.assertIn("def _qgis_theme_icon(intent: str)", source)
        self.assertIn("QgsApplication.getThemeIcon", source)
        self.assertIn("def _set_status_badge(label: QLabel", source)
        self.assertIn('requested not in {"primary", "secondary", "neutral", "danger"}', source)
        self.assertIn("DESIGN_SPACING = design_spacing_tokens()", source)
        self.assertIn("PAGE_MARGINS = (SPACING_SM, SPACING_XS, SPACING_SM, SPACING_SM)", source)
        self.assertIn("self.content_layout.setContentsMargins(*PAGE_MARGINS)", source)
        self.assertIn("_size_list_to_content(self.file_list, row_height=72)", source)
        self.assertIn("self.backend_details.setMaximumHeight(140)", source)
        self.assertIn("continueWorkflowRequested = pyqtSignal()", source)
        self.assertIn("self.continue_button = QPushButton(\"Continue\")", source)
        self.assertIn("_apply_button_role(self.continue_button, \"primary\")", source)
        self.assertIn("_apply_button_role(self.clear_current_run_button, \"danger\")", source)
        self.assertIn("_set_status_badge(self.status_label, report.readiness.value", source)
        self.assertIn('_set_status_badge(self.status_label, "RUNNING"', source)
        self.assertIn("self.backend_primary_buttons = QHBoxLayout()", source)
        self.assertIn('QPushButton[buttonRole="primary"]', stylesheet)
        self.assertIn("QLabel#statusBadge", stylesheet)

    def test_guided_workflow_model_is_compact_and_contextual(self) -> None:
        self.assertEqual(guided_workflow_pages(), ("Home", "Workspace", "Dataset", "Planning", "Processing", "Results"))
        controller_source = (ROOT / "pyforestscan_qgis/ui/mission_control.py").read_text(encoding="utf-8")
        self.assertIn(
            'PAGE_NAMES = (\n        "Home",\n        "Workspace",\n        "Dataset",\n        "Planning",\n        "Processing",\n        "Batch",\n        "Results",\n        "Scientific Advisor",\n        "Environment",\n        "Settings",',
            controller_source,
        )
        self.assertEqual(
            guided_workflow_indicator(
                "Planning",
                environment_ready=True,
                workspace_ready=True,
                dataset_loaded=True,
                planning_ready=False,
                batch_complete=False,
                outputs_available=False,
            ),
            "✓ Home  ✓ Workspace  ✓ Dataset  ● Planning  ○ Processing  ○ Results",
        )
        self.assertEqual(
            guided_workflow_status_lines(
                backend_ready=True,
                dataset_loaded=True,
                planning_ready=True,
                batch_complete=False,
                outputs_available=False,
                processing_ready=True,
            ),
            ("Backend: READY", "Dataset: Loaded", "Planning: Configured", "Processing: Ready to run", "Results: None"),
        )
        self.assertEqual(
            guided_next_step(
                "Dataset",
                dataset_loaded=True,
                planning_ready=False,
                batch_complete=False,
                outputs_available=False,
            ),
            ("Build a product plan for this dataset.", "Continue to Planning", "Planning", True),
        )
        self.assertEqual(
            guided_next_step(
                "Results",
                dataset_loaded=True,
                planning_ready=True,
                batch_complete=True,
                outputs_available=True,
            ),
            ("Load outputs into QGIS for review.", "Load Outputs", "Results", False),
        )

    def test_default_continue_path_excludes_batch_and_advisor(self) -> None:
        home_missing_env = guided_next_step(
            "Home",
            environment_ready=False,
            dataset_loaded=False,
            planning_ready=False,
            outputs_available=False,
        )
        self.assertEqual(home_missing_env, ("Check environment readiness before processing.", "Check Environment", "Environment", True))
        home_ready = guided_next_step(
            "Home",
            environment_ready=True,
            dataset_loaded=True,
            planning_ready=True,
            outputs_available=False,
        )
        self.assertEqual(home_ready, ("Run processing for the selected products.", "Continue to Processing", "Processing", True))
        self.assertNotIn(home_ready[2], {"Batch", "Scientific Advisor"})
        self.assertEqual(
            guided_next_step("Planning", dataset_loaded=True, planning_ready=True, outputs_available=False),
            ("Run processing for the selected products.", "Continue to Processing", "Processing", True),
        )
        self.assertEqual(
            guided_next_step("Results", dataset_loaded=True, planning_ready=True, outputs_available=False),
            ("Run processing to generate scientific products.", "Open Processing", "Processing", True),
        )

    def test_home_readiness_copy_and_markers_keep_words(self) -> None:
        self.assertEqual(home_environment_readiness("READY"), "Ready to process with PBM backend.")
        self.assertEqual(home_environment_action_label("READY"), "Check Environment")
        self.assertEqual(home_environment_action_label("NOT READY"), "Set Up Backend")
        tokens = readiness_marker_tokens()
        self.assertEqual(tokens["ready"], ("●", "#3f7f52"))
        self.assertEqual(tokens["not_ready"], ("○", "#b45b52"))
        self.assertIn("●", readiness_marker_label("READY"))
        marked = readiness_status_text("READY", "Environment: Ready to process with PBM backend.")
        self.assertIn("Environment: Ready to process with PBM backend.", marked)

    def test_visual_polish_audit_exists(self) -> None:
        audit = (ROOT / "docs/development/VISUAL_POLISH_AUDIT.md").read_text(encoding="utf-8")

        self.assertIn("Phase 24F applied the PyForestScan Design System directly to Mission Control", audit)
        self.assertIn("Backend status badge", audit)
        self.assertIn("job history and run files/logs stay secondary or collapsed", audit)

    def test_backend_ready_summary_is_not_scary(self) -> None:
        self.assertEqual(backend_summary_from_environment("READY"), "Backend status: PBM ready for routed products")
        self.assertIn("routed products can run through PBM", environment_headline("READY"))

    def test_product_coverage_summaries_are_explicit(self) -> None:
        self.assertIn("Dataset Explorer", routed_products_summary())
        self.assertIn("Voxel Statistic", routed_products_summary())
        self.assertIn("Height Above Ground", qgis_fallback_summary())
        self.assertTrue(technical_sections_default_collapsed())

    def test_mission_control_pages_default_technical_sections_collapsed(self) -> None:
        source = (ROOT / "pyforestscan_qgis/ui/pages.py").read_text(encoding="utf-8")

        self.assertIn('QPushButton("Open Backend Settings")', source)
        self.assertIn('QGIS Python fallback environment', source)
        self.assertIn('Technical dependency details', source)
        self.assertIn('Advanced Batch Options', source)
        self.assertNotIn('_collapsible_section(self.content_layout, "Batch Footprint Estimate"', source)
        self.assertIn('Technical Report', source)
        self.assertIn('_collapsible_section(self.content_layout, "Details", checked=False)', source)
        self.assertIn('backend_detail_layout, "Technical Log", checked=False', source)
        self.assertIn('self.recommendations_card.setVisible(bool(report.suggested_next_actions))', source)
        self.assertIn('self.warnings_card.setVisible(bool(report.warnings))', source)
        self.assertIn('self.jobs_section.setVisible(False)', source)
        self.assertIn('self.reset_section = reset_group', source)
        self.assertIn('self.dataset_technical_text', source)
        self.assertNotIn('self.developer_mode_button', source)
        self.assertIn('self._set_backend_progress_visible(False)', source)


    def test_primary_backend_copy_hides_engineering_language(self) -> None:
        source = (ROOT / "pyforestscan_qgis/ui/pages.py").read_text(encoding="utf-8")

        primary_snippets = (
            'backend = self.add_section("Processing Engine")',
            "Processing Engine is configured for this PyForestScan version.",
            'self.install_backend_button = QPushButton("Set Up Processing Engine")',
            "processing_engine_setup_action(status)",
        )
        for snippet in primary_snippets:
            self.assertIn(snippet, source)
        self.assertIn('_collapsible_section(self.content_layout, "Details", checked=False)', source)

    def test_unified_interaction_model_static_hooks(self) -> None:
        controller = (ROOT / "pyforestscan_qgis/ui/mission_control.py").read_text(encoding="utf-8")
        pages = (ROOT / "pyforestscan_qgis/ui/pages.py").read_text(encoding="utf-8")

        self.assertIn("def _notify(self, message: str", controller)
        self.assertIn("messageBar", controller)
        self.assertIn("self.home_page.checkEnvironmentRequested.connect(self._open_environment_and_refresh)", controller)
        self.assertIn("self.settings_page.processingEngineStateChanged.connect(self._set_processing_engine_state)", controller)
        self.assertIn("self.results_page.outputsLoaded.connect(self._set_outputs_loaded_status)", controller)
        self.assertIn("self.dataset_page.datasetSelectionChanged.connect(self._set_dataset_pending)", controller)
        self.assertIn("self.environment_page.refresh()", controller)
        self.assertIn("with_dataset_pending", controller)
        self.assertIn("without_active_run", controller)
        self.assertIn("datasetSelectionChanged = pyqtSignal(str)", pages)
        self.assertIn("outputsLoaded = pyqtSignal(str, int, int)", pages)
        self.assertIn("backendStateChanged = pyqtSignal(str, str)", pages)
        self.assertIn("self.refresh_button.setEnabled(False)", pages)
        self.assertIn("self.analyze_button.setEnabled(False)", pages)
        self.assertIn("self.build_plan_button.setEnabled(False)", pages)
        self.assertIn("self.load_outputs_button.setEnabled(False)", pages)
        self.assertIn("self.discover_button.setEnabled(False)", pages)
        self.assertIn("self.preflight_button.setEnabled(False)", pages)
        self.assertIn("Stage: Preparing", pages)
        self.assertIn("return \"Complete\"", pages)


    def test_session_awareness_static_hooks_are_present(self) -> None:
        controller = (ROOT / "pyforestscan_qgis/ui/mission_control.py").read_text(encoding="utf-8")
        pages = (ROOT / "pyforestscan_qgis/ui/pages.py").read_text(encoding="utf-8")
        state = (ROOT / "pyforestscan_qgis/ui/state.py").read_text(encoding="utf-8")

        self.assertIn("class ProjectSummary", state)
        self.assertIn("class ProductSessionStatus", state)
        self.assertIn("def build_project_summary", state)
        self.assertIn("SESSION_PRODUCT_LABELS", state)
        self.assertIn("def _project_summary(self) -> ProjectSummary", controller)
        self.assertIn("self.home_page.set_project_summary(summary)", controller)
        self.assertIn("self.workspace_page.set_project_summary(summary)", controller)
        self.assertIn("self.processing_page.set_project_summary(summary)", controller)
        self.assertIn("self.results_page.set_project_summary(summary)", controller)
        self.assertIn("self.advisor_page.set_project_summary(summary)", controller)
        self.assertIn("self.loaded_result_paths.update", controller)
        self.assertIn("self.loaded_result_paths = set()", controller)
        self.assertIn("Products generated:", pages)
        self.assertIn("Already generated:", pages)
        self.assertIn("No generated products.", pages)
        self.assertIn("Generated: {generated}", pages)
        self.assertIn("Loaded: {loaded}", pages)
        self.assertIn("Ready to load: {available}", pages)
        self.assertIn("def loaded_output_paths", pages)

    def test_workflow_buttons_and_results_buttons_are_present(self) -> None:
        source = (ROOT / "pyforestscan_qgis/ui/pages.py").read_text(encoding="utf-8")

        self.assertIn("continueLastRequested = pyqtSignal()", source)
        self.assertIn("def set_continue_available", source)
        self.assertIn('QPushButton("Open Output Folder")', source)
        self.assertIn('QPushButton("Load into QGIS")', source)
        self.assertIn('QPushButton("Clear Current Run")', source)
        self.assertIn("Execution backend: PBM when READY", source)
        self.assertIn("Plugin ZIP: ready for QGIS Plugin Manager installs", source)
        self.assertIn("Backend installer: Windows and Linux x86_64; Apple Silicon experimental", source)
        self.assertIn("def set_workflow_indicator", source)
        self.assertIn("def set_next_step", source)
        self.assertNotIn('setObjectName("workflowStepIndicator")', source)
        self.assertIn('setObjectName("contextHelpBanner")', source)


    def test_refresh_controls_are_available_for_ui_recovery(self) -> None:
        source = (ROOT / "pyforestscan_qgis/ui/pages.py").read_text(encoding="utf-8")
        controller = (ROOT / "pyforestscan_qgis/ui/mission_control.py").read_text(encoding="utf-8")

        for label in ("Refresh Summary", "Refresh Dataset Page", "Refresh Plan State", "Refresh Processing State", "Refresh Results"):
            self.assertIn(label, source)
        self.assertIn("refreshSummaryRequested.connect(self._refresh_summary_from_home)", controller)
        self.assertIn("def refresh_dataset_page(self) -> None:", source)
        self.assertIn("def refresh_plan_state(self) -> None:", source)
        self.assertIn("def refresh_processing_state(self) -> None:", source)
        self.assertIn("def refresh_results(self) -> None:", source)
        self.assertIn("self.browse_dataset_button.setEnabled(True)", source)
        self.assertIn("finally:\n            self.analyze_button.setEnabled(True)\n            self.browse_dataset_button.setEnabled(True)", source)

if __name__ == "__main__":
    unittest.main()
