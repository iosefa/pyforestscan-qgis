"""Mission Control page widgets.

These widgets orchestrate existing adapter-backed workflows. They do not call
PyForestScan directly. CHM execution is routed through JobManager, Pipeline, and
the adapter boundary.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
import threading
import time
import traceback
from html import escape
from pathlib import Path
from typing import Callable

from qgis.PyQt.QtCore import QEvent, QObject, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QLayout,
    QListWidget,
    QMessageBox,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStyle,
    QSizePolicy,
    QSpinBox,
    QDoubleSpinBox,
    QInputDialog,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.adapter import PyForestScanAdapter
from ..core.backend import BackendService
from ..core.backend.system_summary import format_system_summary
from ..core.build_identity import PLUGIN_CORRUPT, PLUGIN_MIXED_INSTALL, plugin_root, verify_session_files_unchanged
from ..core.launch_attempt import LaunchAttempt, append_attempt_stage, create_launch_attempt, read_attempt_status
from ..core.qgis_compat import build_qgis_compatibility_report, format_qgis_compatibility_report
from ..core.qgis_processing_toolbox import QgisProcessingToolboxService
from .session_state import MissionControlSessionState, ScientificAdvisorSummary, build_scientific_advisor_summary, workflow_input_signature
from ..core.adaptive_lidar_indexing import (
    LidarIndexStrategy,
    choose_index_strategy,
    detect_repository_capabilities,
    format_repository_index_plan,
    register_existing_footprint_index,
    register_native_sources,
)
from ..core.batch import BatchItemResult, BatchProductSettings, BatchRequest, batch_run_context, discover_lidar_files
from ..core.batch_execution_contract import prepare_batch_execution
from ..core.batch_control_visibility import batch_control_visibility
from ..core.batch_options import BatchExecutionOptions, PolygonBatchOptions, requested_effective_concurrency
from ..core.batch_executor import PARALLEL_SAFE_MODE, SEQUENTIAL_MODE, BatchExecutor
from ..core.batch_preflight import BatchPreflightReport, run_batch_preflight
from ..core.batch_runner import BatchExecutionError
from ..core.dataset_report import (
    DatasetExplorerReport,
    build_dataset_explorer_report,
    format_count_for_display,
    format_crs_for_display,
    format_density_for_display,
    report_to_dict,
    write_csv_summary,
    write_html_report,
    write_json_report,
)
from ..core.dependency_check import CheckStatus, EnvironmentReport
from ..core.exceptions import AdapterError, ProcessingError
from ..core.ept_repository import incorrect_ept_catalog_detected, repair_ept_catalog
from ..core.ept_subset import build_ept_subset_request, compact_ept_subset_summary
from ..core.guided_polygon_workflow import PROCESSING_PROFILES, guided_review_summary, profile_by_key
from ..core.polygon_source import POLYGON_VECTOR_FILE_FILTER, PolygonSource, selected_feature_count_text
from ..core.polygon_normalization import normalize_polygon_source
from ..core.lidar_catalog_jobs import CatalogJobRunner, CatalogJobSpec, CatalogJobStatus, latest_catalog_job_state
from ..core.lidar_catalog_models import default_lidar_catalog_path, move_lidar_catalog_to_local_storage
from ..core.lidar_catalog_probe import quick_probe_lidar_repository, select_lidar_repository_path
from ..core.lidar_repository_discovery import discover_lidar_repository
from ..core.lidar_catalog_integrity import inspect_catalog_integrity, repair_catalog, source_view_rows, inspect_catalog_records
from ..core.repository_actions import repository_action_states, repository_setup_recommendation
from ..core.repository_coverage import build_repository_coverage_model
from ..core.repository_diagnostics import export_repository_diagnostic_report
from ..core.polygon_batch import PolygonBatchRequest, catalog_status_text, execute_polygon_batch, polygon_preflight_text, record_polygon_dispatch_validation, run_polygon_batch_preflight, write_polygon_batch_manifest
from ..core.prerun_forensics import PrerunForensics
from ..core.polygon_progress import PolygonProgressProjection
from ..core.prerun_profile import PrerunProfiler
from ..core.job_manager import JobExecutionError, JobManager
from ..core.knowledge import RecommendationReport
from ..core.jobs import JobRecord, JobStatus
from ..core.processing_footprint import ProcessingFootprint, footprint_from_plan_file
from ..core.processing_history import ProcessingHistoryEntry, format_recent_result
from ..core.product_plan import (
    PRODUCT_LABELS,
    ProductPlanError,
    ProductPlannerReport,
    ProductPlannerRequest,
    build_product_plan,
    write_plan_csv,
    write_plan_html,
    write_plan_json,
)

from ..compat.qt import install_enum_aliases

install_enum_aliases(QSizePolicy, "Policy", ("Expanding", "Fixed", "Ignored", "Maximum", "Minimum", "Preferred"))
install_enum_aliases(QFrame, "Shape", ("StyledPanel",))
install_enum_aliases(QFormLayout, "RowWrapPolicy", ("DontWrapRows", "WrapAllRows", "WrapLongRows"))
install_enum_aliases(QLayout, "SizeConstraint", ("SetNoConstraint",))
install_enum_aliases(QComboBox, "SizeAdjustPolicy", ("AdjustToMinimumContentsLengthWithIcon",))
install_enum_aliases(QMessageBox, "StandardButton", ("No", "Yes"))
install_enum_aliases(QStyle, "StandardPixmap", ("SP_ArrowForward", "SP_BrowserReload", "SP_DialogApplyButton", "SP_DialogCancelButton", "SP_DialogDiscardButton", "SP_DialogHelpButton", "SP_DialogOpenButton", "SP_DialogSaveButton", "SP_DirOpenIcon", "SP_DriveHDIcon", "SP_FileDialogContentsView", "SP_FileDialogDetailedView", "SP_FileDialogNewFolder", "SP_FileIcon", "SP_MediaPlay", "SP_MessageBoxInformation", "SP_MessageBoxWarning"))
install_enum_aliases(QEvent, "Type", ("Enter", "FocusIn", "FocusOut", "Leave", "Wheel"))
install_enum_aliases(Qt, "AlignmentFlag", ("AlignLeft", "AlignTop"))
install_enum_aliases(Qt, "ArrowType", ("DownArrow", "RightArrow"))
install_enum_aliases(Qt, "CheckState", ("Checked", "Unchecked"))
install_enum_aliases(Qt, "ItemFlag", ("ItemIsUserCheckable",))
install_enum_aliases(Qt, "ScrollBarPolicy", ("ScrollBarAlwaysOff",))
install_enum_aliases(Qt, "TextInteractionFlag", ("LinksAccessibleByMouse", "TextSelectableByMouse"))
install_enum_aliases(Qt, "ToolButtonStyle", ("ToolButtonTextBesideIcon",))
from ..core.product_registry import MISSION_CONTROL_PRODUCTS
from ..core.types import ProductType
from ..core.spatial_assignment import AssignmentScope, LinearUnit
from ..core.spatial_reference_resolver import default_spatial_assignment_store
from ..core.processing_spatial_context import default_source_local_policy_store, policy_with_fallback_crs
from ..core.processing_ui_state import ProcessingUiState, control_policy, reconcile_ui_state, terminal_state_from_result
from ..core.durable_errors import DurableErrorRecord, read_recent_error, write_recent_error
from ..core.completed_job_summary import CompletedJobSummary, completed_job_summary, format_completed_job_summary
from ..core.workspace import (
    RecentWorkspaceSummary,
    RunContext,
    Workspace,
    WorkspaceSession,
    format_timeline_events,
    workspace_primary_action,
    workspace_status_label,
    create_run_context,
)
from .advisor import PRODUCT_EXPLANATIONS, QGIS_TOOL_INSTRUCTIONS
from .help import info_badge, info_help_button
from .help_topics import scientific_group_help, semantic_action_help, semantic_help
from .output_loading import LoadableOutput, collect_loadable_outputs, compact_dataset_summary_lines, output_loading_summary
from .state import ProjectSummary
from .qgis_footprint import FootprintPreview, add_footprint_layer, preview_from_report, zoom_to_footprint
from .qgis_spatial_actions import add_repository_coverage_to_qgis, add_selected_lidar_to_qgis, combine_bounds, preview_spatial_alignment_in_qgis, preview_spatial_selection_in_qgis, remove_spatial_preview_layers, zoom_canvas_to_bounds
from .polygon_source_selector import normalize_qgis_layer_selection, normalize_vector_file_selection, polygon_layer_items, vector_file_layer_options
from .raster_styling import apply_generated_raster_renderer, layer_display_name
from .ux_summary import action_icon_intent, backend_summary_from_environment, button_role_for_label, design_spacing_tokens, empty_state_message, environment_headline, home_environment_action_label, home_environment_readiness, next_processing_action, primary_action_label, processing_area_summary, processing_engine_setup_action, qgis_fallback_summary, readiness_status_text, routed_products_summary, scientific_form_column_count, status_badge_label, status_badge_tone, status_display_word, workflow_action_labels

ActivityCallback = Callable[[str, str], None]

DESIGN_SPACING = design_spacing_tokens()
SPACING_XS = DESIGN_SPACING["xs"]
SPACING_SM = DESIGN_SPACING["sm"]
SPACING_MD = DESIGN_SPACING["md"]
SPACING_LG = DESIGN_SPACING["lg"]
SPACING_XL = DESIGN_SPACING["xl"]
PAGE_MARGINS = (SPACING_SM, SPACING_XS, SPACING_SM, SPACING_SM)
SECTION_MARGINS = (SPACING_XS, SPACING_XS, SPACING_XS, SPACING_XS)
SECTION_SPACING = SPACING_XS
ACTION_ROW_SPACING = SPACING_SM
PRIMARY_BUTTON_HEIGHT = 36
SECONDARY_BUTTON_HEIGHT = 30
PAGE_MARGIN = SPACING_MD
SECTION_GAP = SPACING_SM
ROW_GAP = SPACING_XS
CONTROL_GAP = SPACING_SM
HEADING_GAP = SPACING_XS
COMPACT_BUTTON_HEIGHT = SECONDARY_BUTTON_HEIGHT
FIELD_HEIGHT = SECONDARY_BUTTON_HEIGHT
COMPACT_LIST_HEIGHT = 76
TECHNICAL_DETAIL_HEIGHT = 72
COMPACT_VISIBLE_ROWS = 6


class ContextHelpBanner(QFrame):
    """Shared, theme-aware explanation surface for Mission Control controls."""

    DEFAULT_TEXT = "Hover over or focus a control for more information."

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("contextHelpBanner")
        self.setAccessibleName("Context help")
        self.setFrameShape(QFrame.StyledPanel)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setMaximumHeight(42)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING_SM, SPACING_XS, SPACING_SM, SPACING_XS)
        layout.setSpacing(SPACING_SM)
        self.label = QLabel(f"Help  |  {self.DEFAULT_TEXT}")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.label, 1)

    def set_help(self, text: str | None = None) -> None:
        self.label.setText(f"Help  |  {(text or self.DEFAULT_TEXT).strip()}")


class MissionPage(QWidget):
    """Base class for Mission Control pages with one full-page scroll region."""

    nextStepRequested = pyqtSignal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        """Create a page with a title and full-width scrollable content."""
        super().__init__(parent)
        self.title = title
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        heading = QLabel(title)
        heading.setObjectName("pageHeading")
        heading.setWordWrap(True)
        self.main_layout.addWidget(heading)
        self.next_step_section: QGroupBox | None = None
        self.next_step_label: QLabel | None = None
        self.next_step_button: QPushButton | None = None

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("pageScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.content_widget = QWidget()
        self.content_widget.setObjectName("pageContent")
        self.content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(*PAGE_MARGINS)
        self.content_layout.setSpacing(SECTION_GAP)
        self.scroll_area.setWidget(self.content_widget)
        self.main_layout.addWidget(self.scroll_area, 1)
        self.help_banner = ContextHelpBanner(self)
        self.main_layout.addWidget(self.help_banner)
        # Keep wheel gestures on numeric/choice controls as page scrolling;
        # controls only change from explicit clicks or keyboard input.  The
        # application-level filter also covers controls created dynamically
        # after the initial page construction.
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        QTimer.singleShot(0, self._install_context_help)

    def create_section(self, title: str, index: int | None = None) -> tuple[QGroupBox, QVBoxLayout]:
        """Create a durable section widget and its owned layout."""
        group = QGroupBox(title, self.content_widget)
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(*SECTION_MARGINS)
        layout.setSpacing(SECTION_SPACING)
        if index is None:
            self.content_layout.addWidget(group)
        else:
            self.content_layout.insertWidget(index, group)
        return group, layout

    def add_section(self, title: str) -> QVBoxLayout:
        """Add a titled full-width section and return its layout."""
        _group, layout = self.create_section(title)
        return layout

    def set_workflow_indicator(self, text: str | None) -> None:
        """Retain the compatibility hook after removing the wizard-like strip."""

    def _install_context_help(self) -> None:
        """Use existing accessible text and explicit help without adding icon clutter."""
        for widget in self.findChildren(QWidget):
            if widget is self.help_banner:
                continue
            if isinstance(widget, (QComboBox, QAbstractSpinBox)):
                widget.setProperty("ignoreWheelValueChange", True)
                widget.installEventFilter(self)
            text = str(widget.property("contextHelp") or widget.toolTip() or widget.accessibleName() or "").strip()
            if not text:
                text = _default_context_help(widget)
            if text:
                register_context_help(widget, text, self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        """Project mouse and keyboard context into one stable help banner."""
        if event.type() == QEvent.Wheel and isinstance(watched, (QComboBox, QAbstractSpinBox)):
            delta = getattr(event, "angleDelta", lambda: None)()
            vertical = int(delta.y()) if delta is not None else 0
            scrollbar = self.scroll_area.verticalScrollBar()
            scrollbar.setValue(scrollbar.value() - vertical)
            event.accept()
            return True
        if event.type() in {QEvent.Enter, QEvent.FocusIn}:
            text = str(watched.property("resolvedContextHelp") or "").strip()
            if text:
                self.help_banner.set_help(text)
        elif event.type() in {QEvent.Leave, QEvent.FocusOut} and not self.focusWidget():
            self.help_banner.set_help()
        return super().eventFilter(watched, event)

    def set_next_step(self, message: str, button_label: str, enabled: bool = True) -> None:
        """Show one concise next-step recommendation at the bottom of the page."""
        if self.next_step_section is None:
            layout = self.add_section("Next Step")
            self.next_step_section = layout.parentWidget()
            self.next_step_label = _body_label("")
            layout.addWidget(self.next_step_label)
            row = QHBoxLayout()
            row.setSpacing(ACTION_ROW_SPACING)
            self.next_step_button = QPushButton(button_label)
            self.next_step_button.setMinimumHeight(SECONDARY_BUTTON_HEIGHT)
            self.next_step_button.clicked.connect(self.nextStepRequested.emit)
            row.addWidget(self.next_step_button)
            row.addStretch(1)
            layout.addLayout(row)
        assert self.next_step_label is not None
        assert self.next_step_button is not None
        self.next_step_label.setText(f"Next: {message}")
        self.next_step_button.setText(button_label)
        self.next_step_button.setEnabled(enabled)
        _apply_button_role(self.next_step_button, "primary" if enabled else "neutral")
        self.next_step_section.setVisible(True)


class HomePage(MissionPage):
    """Mission Control workflow dashboard."""

    startSingleDatasetRequested = pyqtSignal()
    startBatchRequested = pyqtSignal()
    continueLastRequested = pyqtSignal()
    continueWorkflowRequested = pyqtSignal()
    checkEnvironmentRequested = pyqtSignal()
    refreshSummaryRequested = pyqtSignal()

    def __init__(self, plugin_version: str, parent: QWidget | None = None) -> None:
        """Create the home dashboard."""
        super().__init__("Home", parent)
        dashboard = self.add_section("Workflow Overview")
        self.backend_label = _body_label("Backend: unknown")
        self.environment_label = _body_label("Environment: Check readiness")
        self.dataset_label = _body_label("Dataset: Not selected")
        self.workflow_label = _body_label("Workflow: Not started")
        self.output_label = _body_label("Current output folder: None")
        self.products_generated_label = _body_label("Products generated: None")
        self.products_loaded_label = _body_label("Products loaded: None")
        self.last_run_label = _body_label("Last run: None")
        for label in (
            self.backend_label,
            self.environment_label,
            self.dataset_label,
            self.workflow_label,
            self.output_label,
            self.products_generated_label,
            self.products_loaded_label,
            self.last_run_label,
        ):
            dashboard.addWidget(label)

        actions = QHBoxLayout()
        self.continue_button = QPushButton("Continue")
        self.continue_button.setMinimumHeight(PRIMARY_BUTTON_HEIGHT)
        self.continue_button.clicked.connect(self.continueWorkflowRequested.emit)
        _apply_button_role(self.continue_button, "primary")
        self.check_environment_button = QPushButton("Check Environment")
        self.check_environment_button.setMinimumHeight(SECONDARY_BUTTON_HEIGHT)
        self.check_environment_button.clicked.connect(self.checkEnvironmentRequested.emit)
        _apply_button_role(self.check_environment_button, "neutral")
        self.refresh_summary_button = QPushButton("Refresh Summary")
        self.refresh_summary_button.setMinimumHeight(SECONDARY_BUTTON_HEIGHT)
        self.refresh_summary_button.clicked.connect(self.refreshSummaryRequested.emit)
        _apply_button_role(self.refresh_summary_button, "neutral")
        actions.addWidget(self.continue_button)
        actions.addWidget(self.check_environment_button)
        actions.addWidget(self.refresh_summary_button)
        actions.addStretch(1)
        dashboard.addLayout(actions)

        versions = _collapsible_section(self.content_layout, "Version Details", checked=False)
        version_group, version_layout = versions
        self.plugin_version_label = _details_label(f"Plugin version: {plugin_version}")
        self.pyforestscan_version_label = _details_label("PyForestScan version: Unknown")
        version_layout.addWidget(self.plugin_version_label)
        version_layout.addWidget(self.pyforestscan_version_label)
        _wire_collapsible_group(version_group)

        activity_group, activity = _collapsible_section(self.content_layout, "Recent Activity", checked=False)
        self.activity_list = QListWidget()
        activity.addWidget(self.activity_list)
        _wire_collapsible_group(activity_group)

    def set_versions(self, pyforestscan_version: str | None) -> None:
        """Update version labels."""
        self.pyforestscan_version_label.setText(f"PyForestScan version: {pyforestscan_version or 'Unknown'}")

    def set_summary(self, environment: str, dataset: str | None, project: str | None, batch_status: str = "Not started", recent_run: str | None = None, workflow_status: str | None = None, continue_label: str = "Continue", continue_enabled: bool = True) -> None:
        """Update dashboard labels."""
        current = Path(dataset).name if dataset else "Not selected"
        backend_text = backend_summary_from_environment(environment).replace("Backend status", "Backend")
        self.backend_label.setText(readiness_status_text(environment, backend_text))
        self.environment_label.setText(readiness_status_text(environment, f"Environment: {home_environment_readiness(environment)}"))
        self.dataset_label.setText(f"Dataset: {current}")
        self.workflow_label.setText(f"Workflow: {workflow_status or _next_home_action(environment, dataset, batch_status)}")
        self.output_label.setText(f"Current output folder: {recent_run or 'None'}")
        self.continue_button.setText(continue_label)
        self.continue_button.setEnabled(continue_enabled)
        self.check_environment_button.setText(home_environment_action_label(environment))

    def set_workspace(self, workspace: Workspace | None) -> None:
        """Display active workspace status on Home."""
        if workspace is None:
            return
        session = workspace.session
        self.dataset_label.setText(f"Dataset: {Path(session.last_selected_dataset).name if session.last_selected_dataset else workspace.name}")
        self.output_label.setText(f"Current output folder: {session.last_output_folder or workspace.output_root}")
        self.workflow_label.setText(f"Workflow: {workspace_primary_action(workspace)}")

    def set_project_summary(self, summary: ProjectSummary) -> None:
        """Display the shared current-session project summary."""
        self.dataset_label.setText(f"Dataset: {summary.dataset_name} ({summary.dataset_type})")
        self.output_label.setText(f"Current output folder: {summary.output_folder or 'None'}")
        self.products_generated_label.setText(summary.generated_summary())
        self.products_loaded_label.setText(summary.loaded_summary())
        self.last_run_label.setText(f"Last run: {summary.last_processing_time or 'None'}")

    def set_continue_available(self, available: bool) -> None:
        """Enable Continue Last Run when a current or recent workspace exists."""
        if not available and not self.continue_button.isEnabled():
            self.continue_button.setEnabled(False)

    def set_activities(self, activities: tuple[tuple[str, str], ...]) -> None:
        """Display recent activity."""
        self.activity_list.clear()
        for label, detail in activities[:8]:
            self.activity_list.addItem(f"{label}: {detail}" if detail else label)


class WorkspacePage(MissionPage):
    """Workspace welcome, resume, timeline, and notes page."""

    continueLastRequested = pyqtSignal()
    startNewRequested = pyqtSignal()
    workspaceSelected = pyqtSignal(object)
    removeRecentRequested = pyqtSignal(object)
    resetWorkspaceRequested = pyqtSignal()
    notesSaveRequested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the Workspace page."""
        super().__init__("Workspace", parent)
        self.recent_workspaces: tuple[RecentWorkspaceSummary, ...] = ()
        self.current_workspace: Workspace | None = None

        welcome = self.add_section("Welcome")
        self.workspace_status_card = QLabel(empty_state_message("workspace"))
        self.workspace_status_card.setObjectName("advisorMetric")
        self.workspace_status_card.setWordWrap(True)
        welcome.addWidget(self.workspace_status_card)
        action_row = QHBoxLayout()
        self.continue_button = QPushButton(primary_action_label("workspace"))
        self.continue_button.setMinimumHeight(PRIMARY_BUTTON_HEIGHT)
        self.continue_button.clicked.connect(self.continueLastRequested.emit)
        _apply_button_role(self.continue_button, "primary")
        self.start_new_button = QPushButton("Start New Workspace")
        self.start_new_button.setMinimumHeight(PRIMARY_BUTTON_HEIGHT)
        self.start_new_button.clicked.connect(self.startNewRequested.emit)
        _apply_button_role(self.start_new_button, "secondary")
        action_row.addWidget(self.continue_button)
        action_row.addWidget(self.start_new_button)
        action_row.addStretch(1)
        welcome.addLayout(action_row)

        recent = self.add_section("Recent Workspaces")
        self.recent_section = recent.parentWidget()
        self.recent_list = QListWidget()
        self.recent_list.itemDoubleClicked.connect(lambda _item: self.open_selected_workspace())
        recent.addWidget(self.recent_list)
        recent_row = QHBoxLayout()
        open_recent = QPushButton("Open Selected")
        open_recent.clicked.connect(self.open_selected_workspace)
        remove_recent = QPushButton("Remove Missing/Selected")
        remove_recent.clicked.connect(self.remove_selected_workspace)
        _apply_button_role(remove_recent, "danger")
        recent_row.addWidget(open_recent)
        recent_row.addWidget(remove_recent)
        recent_row.addStretch(1)
        recent.addLayout(recent_row)
        self.recent_section.setVisible(False)

        status = self.add_section("Workspace Status")
        self.status_section = status.parentWidget()
        self.current_step_label = _body_label("Current step: None")
        self.completion_label = _body_label("Completion: 0%")
        self.dataset_label = _body_label("Last dataset: None")
        self.output_label = _body_label("Last output folder: None")
        self.primary_action_label = _body_label("Primary next action: start or continue a workspace.")
        self.current_project_label = _body_label("Current project: Unknown")
        self.products_label = _body_label("Products: None")
        self.session_status_label = _body_label("Session: not started")
        for label in (
            self.current_step_label,
            self.completion_label,
            self.current_project_label,
            self.dataset_label,
            self.output_label,
            self.products_label,
            self.session_status_label,
            self.primary_action_label,
        ):
            status.addWidget(label)

        runs = self.add_section("Recent Runs")
        self.runs_section = runs.parentWidget()
        self.runs_list = QListWidget()
        runs.addWidget(self.runs_list)

        outputs = self.add_section("Key Output Links")
        self.outputs_section = outputs.parentWidget()
        self.output_links_list = QListWidget()
        outputs.addWidget(self.output_links_list)

        timeline = self.add_section("Timeline Summary")
        self.timeline_section = timeline.parentWidget()
        self.timeline_list = QListWidget()
        timeline.addWidget(self.timeline_list)

        notes_group, notes = _collapsible_section(self.content_layout, "Notes", checked=False)
        self.notes_section = notes_group
        self.notes_edit = QTextEdit()
        self.notes_edit.setMinimumHeight(TECHNICAL_DETAIL_HEIGHT)
        notes.addWidget(self.notes_edit)
        save_notes = QPushButton("Save Notes")
        save_notes.clicked.connect(lambda: self.notesSaveRequested.emit(self.notes_edit.toPlainText()))
        notes.addWidget(save_notes)
        _wire_collapsible_group(notes_group)

        reset_group, reset = _collapsible_section(self.content_layout, "Workspace Reset", checked=False)
        self.reset_section = reset_group
        reset.addWidget(_body_label("Clear the current workspace from Mission Control or reset its progress/history. Workspace files remain local."))
        self.reset_button = QPushButton("Clear / Reset Current Workspace")
        self.reset_button.clicked.connect(self.resetWorkspaceRequested.emit)
        _apply_button_role(self.reset_button, "danger")
        reset.addWidget(self.reset_button)
        _wire_collapsible_group(reset_group)
        for section in (self.status_section, self.runs_section, self.outputs_section, self.timeline_section, self.notes_section, self.reset_section):
            section.setVisible(False)

    def set_workspace(self, workspace: Workspace | None) -> None:
        """Display the current workspace."""
        self.current_workspace = workspace
        if workspace is None:
            self.workspace_status_card.setText(empty_state_message("workspace"))
            self.current_step_label.setText("Current step: None")
            self.completion_label.setText("Completion: 0%")
            self.dataset_label.setText("Last dataset: None")
            self.output_label.setText("Last output folder: None")
            self.primary_action_label.setText("Primary next action: start or continue a workspace.")
            self.current_project_label.setText("Current project: Unknown")
            self.products_label.setText("Products: None")
            self.session_status_label.setText("Session: not started")
            self.runs_list.clear()
            self.output_links_list.clear()
            self.timeline_list.clear()
            self.notes_edit.setPlainText("")
            for section in (self.status_section, self.runs_section, self.outputs_section, self.timeline_section, self.notes_section, self.reset_section):
                section.setVisible(False)
            return
        for section in (self.status_section, self.notes_section, self.reset_section):
            section.setVisible(True)
        session = workspace.session
        self.workspace_status_card.setText(workspace_status_label(workspace))
        self.current_step_label.setText(f"Current step: {workspace.state.current_step}")
        self.completion_label.setText(f"Completion: {workspace.state.completion_percentage}%")
        self.dataset_label.setText(f"Last dataset: {session.last_selected_dataset or 'None'}")
        self.output_label.setText(f"Last output folder: {session.last_output_folder or workspace.output_root}")
        self.primary_action_label.setText(f"Primary next action: {workspace_primary_action(workspace)}")
        self.notes_edit.setPlainText(workspace.notes.markdown)
        self._set_runs(workspace)
        self._set_outputs(workspace)
        self._set_timeline(workspace)

    def set_project_summary(self, summary: ProjectSummary) -> None:
        """Display compact current-session state independent of persisted history."""
        for section in (self.status_section,):
            section.setVisible(True)
        project = summary.workspace or "Current QGIS project"
        crs = summary.project_crs or "Unknown CRS"
        self.current_project_label.setText(f"Current project: {project}; CRS: {crs}")
        self.dataset_label.setText(f"Dataset: {summary.dataset_name} ({summary.dataset_type})")
        self.output_label.setText(f"Output folder: {summary.output_folder or 'None'}")
        self.products_label.setText(summary.generated_summary())
        self.session_status_label.setText(summary.compact_status())

    def set_recent_workspaces(self, recent_workspaces: tuple[RecentWorkspaceSummary, ...]) -> None:
        """Display recent workspace choices."""
        self.recent_workspaces = recent_workspaces[:10]
        self.recent_list.clear()
        for item in self.recent_workspaces:
            suffix = "" if item.exists else " [missing]"
            self.recent_list.addItem(f"{item.label}{suffix}\n{item.path}")
        has_recent = bool(self.recent_workspaces)
        self.recent_section.setVisible(has_recent)
        self.continue_button.setEnabled(has_recent)

    def open_selected_workspace(self) -> None:
        """Emit the selected recent workspace path."""
        row = self.recent_list.currentRow()
        if 0 <= row < len(self.recent_workspaces):
            self.workspaceSelected.emit(self.recent_workspaces[row].path)

    def remove_selected_workspace(self) -> None:
        """Request removal of the selected recent workspace path."""
        row = self.recent_list.currentRow()
        if 0 <= row < len(self.recent_workspaces):
            self.removeRecentRequested.emit(self.recent_workspaces[row].path)

    def _set_runs(self, workspace: Workspace) -> None:
        self.runs_list.clear()
        for run in workspace.history.runs[:10]:
            status = "success" if run.success else "failed"
            products = ", ".join(run.products) or "no products"
            self.runs_list.addItem(f"{status.upper()} - {products}\n{run.finished_at or run.started_at or run.run_id}")
        self.runs_section.setVisible(bool(workspace.history.runs))

    def _set_outputs(self, workspace: Workspace) -> None:
        self.output_links_list.clear()
        outputs = []
        for run in workspace.history.runs:
            outputs.extend(run.output_paths)
        for path in outputs[:10]:
            self.output_links_list.addItem(str(path))
        self.outputs_section.setVisible(bool(outputs))

    def _set_timeline(self, workspace: Workspace) -> None:
        self.timeline_list.clear()
        lines = format_timeline_events(workspace.timeline, limit=12)
        for line in lines:
            self.timeline_list.addItem(line)
        self.timeline_section.setVisible(bool(lines))


class EnvironmentPage(MissionPage):
    """Environment diagnostics page."""

    environmentChanged = pyqtSignal(str)
    backendSettingsRequested = pyqtSignal()

    def __init__(self, adapter: PyForestScanAdapter, parent: QWidget | None = None) -> None:
        """Create the environment page."""
        super().__init__("Environment", parent)
        self.adapter = adapter
        controls = self.add_section("Readiness")
        button_row = QHBoxLayout()
        self.refresh_button = QPushButton(primary_action_label("environment"))
        self.refresh_button.clicked.connect(self.refresh)
        _apply_button_role(self.refresh_button, "primary")
        self.open_backend_settings_button = QPushButton("Open Backend Settings")
        self.open_backend_settings_button.clicked.connect(self.backendSettingsRequested.emit)
        _apply_button_role(self.open_backend_settings_button, "secondary")
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.open_backend_settings_button)
        button_row.addStretch(1)
        controls.addLayout(button_row)
        self.status_label = QLabel()
        _set_status_badge(self.status_label, "NOT CONFIGURED", readiness_status_text("NOT CONFIGURED", "Status: Not set up - refresh to check readiness."))
        self.pbm_status_label = _body_label("PBM backend status: not checked")
        self.execution_label = _body_label("Execution backend: not checked")
        self.scope_label = _body_label(routed_products_summary())
        self.next_step_label = _body_label("Recommended next step: refresh environment.")
        for label in (self.status_label, self.pbm_status_label, self.execution_label, self.scope_label, self.next_step_label):
            controls.addWidget(label)

        fallback_group, fallback = _collapsible_section(self.content_layout, "QGIS Python fallback environment", checked=False)
        fallback.addWidget(_details_label("Optional when PBM backend is READY. Expand only for tools that still use QGIS Python or for troubleshooting."))
        self.fallback_checks_list = QListWidget()
        self.fallback_checks_list.setMaximumHeight(140)
        fallback.addWidget(self.fallback_checks_list)
        _wire_collapsible_group(fallback_group)

        technical_group, technical = _collapsible_section(self.content_layout, "Technical dependency details", checked=False)
        self.checks_list = QListWidget()
        self.checks_list.setMaximumHeight(180)
        technical.addWidget(self.checks_list)
        _wire_collapsible_group(technical_group)

    def refresh(self) -> None:
        """Run adapter-backed environment validation."""
        self.refresh_button.setEnabled(False)
        _set_status_badge(self.status_label, "RUNNING", readiness_status_text("RUNNING", "Status: Running - checking environment."))
        QApplication.processEvents()
        try:
            report = self.adapter.check_environment()
            self.set_report(report)
        finally:
            self.refresh_button.setEnabled(True)

    def set_processing_engine_state(self, engine: object) -> None:
        """Project engine readiness without running the full environment check."""
        status = str(getattr(getattr(engine, "status", None), "value", getattr(engine, "engine_status", "FAILED")))
        ready = bool(getattr(engine, "ready_for_processing", getattr(engine, "processing_available", False)))
        repair = bool(getattr(engine, "repair_needed", getattr(engine, "repair_required", False)))
        display = "Ready" if ready else ("Needs repair" if repair else ("Checking" if status == "CHECKING" else "Setup required"))
        _set_status_badge(self.status_label, status, f"Processing Engine: {display}")
        self.pbm_status_label.setText(f"Processing Engine: {display}")
        self.execution_label.setText("Execution backend: Processing Engine" if ready else "Execution backend: unavailable until setup completes")
        self.next_step_label.setText(
            "Recommended next step: continue processing."
            if ready
            else "Recommended next step: open Tools & Setup."
        )

    def set_report(self, report: EnvironmentReport) -> None:
        """Display an environment report."""
        _set_status_badge(self.status_label, report.readiness.value, readiness_status_text(report.readiness.value, environment_headline(report.readiness.value)))
        self.checks_list.clear()
        self.fallback_checks_list.clear()
        pbm_message = "PBM backend status: not checked"
        execution_message = "Execution backend: not checked"
        next_step = "Recommended next step: continue with Dataset Explorer or Batch when READY."
        fallback_names = {"pyforestscan", "pdal", "osgeo.gdal", "rasterio", "numpy"}
        for check in report.checks:
            icon = _status_icon(check.status.value)
            version = f" ({check.version})" if check.version else ""
            guidance = f"\nNext: {check.guidance}" if check.guidance else ""
            row = f"{icon} {check.name}{version}: {check.message}{guidance}"
            self.checks_list.addItem(row)
            if check.name == "PBM managed backend":
                pbm_message = f"PBM backend status: {check.message}"
            elif check.name == "Active execution backend":
                execution_message = f"Execution backend: {check.message}"
            elif check.name in fallback_names:
                self.fallback_checks_list.addItem(row)
        if report.readiness.value == "NOT READY":
            next_step = "Recommended next step: open Backend Settings and install or repair PBM."
        elif report.readiness.value == "READY WITH QGIS PYTHON":
            next_step = "Recommended next step: run guided workflows, or install PBM for no-manual-setup routed products."
        self.pbm_status_label.setText(pbm_message)
        self.execution_label.setText(execution_message)
        self.scope_label.setText(f"{routed_products_summary()}\n{qgis_fallback_summary()}")
        self.next_step_label.setText(next_step)
        self.environmentChanged.emit(report.readiness.value)


class DatasetPage(MissionPage):
    """Dataset inspection page with automatic run-folder creation."""

    datasetExplored = pyqtSignal(object, str, object)
    datasetSelectionChanged = pyqtSignal(str)

    def __init__(self, adapter: PyForestScanAdapter, iface: object | None = None, parent: QWidget | None = None) -> None:
        """Create the dataset page."""
        super().__init__("Dataset", parent)
        self.adapter = adapter
        self.iface = iface
        self.active_run: RunContext | None = None
        self.footprint_preview: FootprintPreview | None = None
        picker = self.add_section("Dataset")
        row = QHBoxLayout()
        self.dataset_path_edit = QLineEdit()
        self.dataset_path_edit.setPlaceholderText("Choose LAS, LAZ, COPC, or ept.json")
        self.browse_dataset_button = QPushButton("Select Dataset")
        self.dataset_path_edit.setProperty("contextHelp", "Choose the LAS, LAZ, COPC, or EPT dataset to inspect and process. Reading metadata does not modify the source.")
        self.browse_dataset_button.clicked.connect(self.browse_dataset)
        row.addWidget(self.dataset_path_edit)
        row.addWidget(self.browse_dataset_button)
        picker.addLayout(row)

        output_row = QHBoxLayout()
        self.output_folder_edit = QLineEdit()
        self.output_folder_edit.setPlaceholderText("Choose output folder")
        output_browse = QPushButton("Browse")
        self.output_folder_edit.setProperty("contextHelp", "Folder where Dataset Explorer reports and subsequent run outputs will be written. Choosing it does not move or modify source LiDAR.")
        output_browse.clicked.connect(self.browse_output_folder)
        output_row.addWidget(self.output_folder_edit)
        output_row.addWidget(output_browse)
        picker.addLayout(output_row)

        dataset_actions = QHBoxLayout()
        dataset_actions.setSpacing(ACTION_ROW_SPACING)
        self.analyze_button = QPushButton(primary_action_label("dataset"))
        self.analyze_button.clicked.connect(self.run_explorer)
        _apply_button_role(self.analyze_button, "primary")
        self.dataset_refresh_button = QPushButton("Refresh Dataset Page")
        self.dataset_refresh_button.clicked.connect(self.refresh_dataset_page)
        _apply_button_role(self.dataset_refresh_button, "neutral")
        dataset_actions.addWidget(self.analyze_button)
        dataset_actions.addWidget(self.dataset_refresh_button)
        dataset_actions.addStretch(1)
        picker.addLayout(dataset_actions)

        summary = self.add_section("Dataset Summary")
        self.summary_section = summary.parentWidget()
        self.summary_text = _body_label(empty_state_message("dataset"))
        summary.addWidget(self.summary_text)
        self.summary_section.setVisible(False)

        metadata_group, metadata = _collapsible_section(self.content_layout, "Technical Metadata", checked=False)
        self.dataset_technical_text = _details_label("Dataset technical metadata appears after analysis.")
        metadata.addWidget(self.dataset_technical_text)
        _wire_collapsible_group(metadata_group)

        ept_group, ept_layout = _collapsible_section(self.content_layout, "EPT Subset", checked=False)
        ept_layout.addWidget(_body_label("Extract a bounded EPT subset to LAS/LAZ, then use it as the current dataset."))
        ept_form = QFormLayout()
        ept_form.setVerticalSpacing(SECTION_SPACING)
        self.ept_srs_edit = QLineEdit("EPSG:4326")
        self.ept_bounds_edit = QLineEdit()
        self.ept_bounds_edit.setPlaceholderText("xmin,xmax,ymin,ymax[,zmin,zmax]")
        self.ept_thin_radius_spin = QDoubleSpinBox()
        self.ept_thin_radius_spin.setDecimals(3)
        self.ept_thin_radius_spin.setMinimum(0.0)
        self.ept_thin_radius_spin.setSpecialValueText("None")
        self.ept_hag_combo = QComboBox()
        self.ept_hag_combo.addItems(("None", "Delaunay HAG", "DTM-backed HAG"))
        self.ept_dtm_edit = QLineEdit()
        self.ept_dtm_edit.setPlaceholderText("Required only for DTM-backed HAG")
        dtm_row = QHBoxLayout()
        dtm_browse = QPushButton("Browse")
        dtm_browse.clicked.connect(self.browse_ept_dtm)
        dtm_row.addWidget(self.ept_dtm_edit, 1)
        dtm_row.addWidget(dtm_browse, 0)
        self.ept_crop_check = QCheckBox("Crop with polygon")
        self.ept_poly_edit = QLineEdit()
        self.ept_poly_edit.setPlaceholderText("Polygon WKT or polygon file path")
        self.ept_reproject_check = QCheckBox("Reproject while reading")
        self.ept_output_edit = QLineEdit()
        self.ept_output_edit.setPlaceholderText("outputs/ept_subset.laz")
        output_path_row = QHBoxLayout()
        output_browse = QPushButton("Browse")
        output_browse.clicked.connect(self.browse_ept_output)
        output_path_row.addWidget(self.ept_output_edit, 1)
        output_path_row.addWidget(output_browse, 0)
        ept_form.addRow("SRS / CRS", self.ept_srs_edit)
        ept_form.addRow("Bounds", self.ept_bounds_edit)
        ept_form.addRow("Thin radius", self.ept_thin_radius_spin)
        ept_form.addRow("HAG method", self.ept_hag_combo)
        ept_form.addRow("DTM", dtm_row)
        ept_form.addRow("Crop", self.ept_crop_check)
        ept_form.addRow("Polygon", self.ept_poly_edit)
        ept_form.addRow("Reproject", self.ept_reproject_check)
        ept_form.addRow("Output LAS/LAZ", output_path_row)
        ept_layout.addLayout(ept_form)
        ept_actions = QHBoxLayout()
        ept_actions.setSpacing(ACTION_ROW_SPACING)
        self.ept_extract_button = QPushButton("Extract Subset")
        self.ept_extract_button.clicked.connect(self.extract_ept_subset)
        _apply_button_role(self.ept_extract_button, "primary")
        self.ept_use_output_button = QPushButton("Use Extracted Subset as Dataset")
        self.ept_use_output_button.clicked.connect(self.use_extracted_subset_as_dataset)
        self.ept_use_output_button.setEnabled(False)
        _apply_button_role(self.ept_use_output_button, "secondary")
        ept_actions.addWidget(self.ept_extract_button)
        ept_actions.addWidget(self.ept_use_output_button)
        ept_actions.addStretch(1)
        ept_layout.addLayout(ept_actions)
        self.ept_message_label = _details_label("Select an ept.json source to enable subset extraction.")
        ept_layout.addWidget(self.ept_message_label)
        self.ept_subset_output_path: Path | None = None
        _wire_collapsible_group(ept_group)

        spatial = self.add_section("Spatial Preview")
        self.spatial_section = spatial.parentWidget()
        self.footprint_text = QTextEdit()
        self.footprint_text.setReadOnly(True)
        spatial.addWidget(self.footprint_text)
        self.spatial_section.setVisible(False)
        spatial_buttons = QHBoxLayout()
        self.add_footprint_button = QPushButton("Add Footprint Layer")
        self.add_footprint_button.clicked.connect(self.add_footprint_layer)
        _apply_button_role(self.add_footprint_button, "secondary")
        self.add_footprint_button.setEnabled(False)
        self.zoom_footprint_button = QPushButton("Zoom to Footprint")
        self.zoom_footprint_button.clicked.connect(self.zoom_to_footprint)
        _apply_button_role(self.zoom_footprint_button, "secondary")
        self.zoom_footprint_button.setEnabled(False)
        self.open_report_button = QPushButton("Open Report")
        self.open_report_button.clicked.connect(self.open_report)
        _apply_button_role(self.open_report_button, "secondary")
        self.open_report_button.setEnabled(False)
        spatial_buttons.addWidget(self.add_footprint_button)
        spatial_buttons.addWidget(self.zoom_footprint_button)
        spatial_buttons.addWidget(self.open_report_button)
        spatial.addLayout(spatial_buttons)

    def set_default_output_folder(self, folder: Path | None) -> None:
        """Use the configured output folder when the page has no explicit folder."""
        if folder is not None and not self.output_folder_edit.text().strip():
            self.output_folder_edit.setText(str(folder))

    def browse_dataset(self) -> None:
        """Open a file picker for supported point-cloud datasets."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose lidar dataset",
            "",
            "Point cloud datasets (*.las *.laz *.copc *.copc.laz *ept.json);;All files (*.*)",
        )
        if path:
            previous = self.dataset_path_edit.text().strip()
            self.dataset_path_edit.setText(path)
            self.browse_dataset_button.setText("Change Dataset")
            _apply_button_role(self.browse_dataset_button, "secondary")
            self.analyze_button.setText("Analyze Dataset")
            if path != previous:
                self._reset_dataset_outputs()
                self.datasetSelectionChanged.emit(path)

    def browse_output_folder(self) -> None:
        """Choose the root output folder for Mission Control runs."""
        path = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if path:
            self.output_folder_edit.setText(path)

    def browse_ept_dtm(self) -> None:
        """Choose a DTM raster for DTM-backed EPT HAG."""
        path, _ = QFileDialog.getOpenFileName(self, "Choose DTM GeoTIFF", "", "GeoTIFF files (*.tif *.tiff);;All files (*.*)")
        if path:
            self.ept_dtm_edit.setText(path)

    def browse_ept_output(self) -> None:
        """Choose the LAS/LAZ output path for an extracted EPT subset."""
        path, _ = QFileDialog.getSaveFileName(self, "Choose EPT subset output", str(self._default_ept_output_path()), "LAS/LAZ files (*.las *.laz);;All files (*.*)")
        if path:
            self.ept_output_edit.setText(path)

    def refresh_dataset_page(self) -> None:
        """Recover Dataset page button states without deleting outputs or backend state."""
        has_dataset = bool(self.dataset_path_edit.text().strip())
        self.browse_dataset_button.setEnabled(True)
        self.browse_dataset_button.setText("Change Dataset" if has_dataset else "Select Dataset")
        _apply_button_role(self.browse_dataset_button, "secondary" if has_dataset else "neutral")
        self.analyze_button.setEnabled(True)
        self.ept_extract_button.setEnabled(True)
        self.ept_use_output_button.setEnabled(self.ept_subset_output_path is not None)
        self._set_ept_message("Dataset page refreshed. EPT subset extraction is available for ept.json sources.")
        if not self.summary_text.text().strip():
            self._set_dataset_message(empty_state_message("dataset"))

    def extract_ept_subset(self) -> None:
        """Extract the selected EPT subset and offer it as the active dataset."""
        source = self.dataset_path_edit.text().strip()
        if not source:
            self._set_ept_message("Select an ept.json source before extracting a subset.")
            return
        output = self.ept_output_edit.text().strip() or str(self._default_ept_output_path())
        self.ept_output_edit.setText(output)
        method = self.ept_hag_combo.currentText().lower()
        thin_radius = self.ept_thin_radius_spin.value() if self.ept_thin_radius_spin.value() > 0 else None
        try:
            request = build_ept_subset_request(
                input_path=source,
                crs=self.ept_srs_edit.text(),
                output_path=output,
                bounds_text=self.ept_bounds_edit.text(),
                thin_radius=thin_radius,
                hag=method.startswith("delaunay"),
                hag_dtm=method.startswith("dtm"),
                dtm_path=self.ept_dtm_edit.text().strip() or None,
                crop_poly=self.ept_crop_check.isChecked(),
                poly=self.ept_poly_edit.text(),
                reproject=self.ept_reproject_check.isChecked(),
            )
        except ProcessingError as exc:
            self._set_ept_message(str(exc))
            return
        self.ept_extract_button.setEnabled(False)
        self._set_ept_message("Extracting EPT subset...")
        QApplication.processEvents()
        try:
            result = self.adapter.extract_lidar_subset(request)
        except (AdapterError, ProcessingError) as exc:
            self._set_ept_message(f"EPT subset extraction failed.\n{exc}")
            return
        finally:
            self.ept_extract_button.setEnabled(True)
        self.ept_subset_output_path = result.output_path
        self.ept_use_output_button.setEnabled(True)
        self._set_ept_message(compact_ept_subset_summary(result) + "\nNext: use the extracted subset as the current dataset or load it in QGIS.")

    def use_extracted_subset_as_dataset(self) -> None:
        """Switch the Dataset page to the extracted LAS/LAZ subset."""
        if self.ept_subset_output_path is None:
            self._set_ept_message("Extract an EPT subset before using it as the dataset.")
            return
        self.dataset_path_edit.setText(str(self.ept_subset_output_path))
        self.browse_dataset_button.setText("Change Dataset")
        _apply_button_role(self.browse_dataset_button, "secondary")
        self.analyze_button.setText("Analyze Dataset")
        self._reset_dataset_outputs()
        self.datasetSelectionChanged.emit(str(self.ept_subset_output_path))
        self._set_dataset_message("Extracted subset selected. Click Analyze Dataset to inspect it.")

    def _default_ept_output_path(self) -> Path:
        if self.active_run is not None:
            return self.active_run.outputs_dir / "ept_subset.laz"
        output_root = self.output_folder_edit.text().strip()
        if output_root:
            return Path(output_root) / "ept_subset.laz"
        source = self.dataset_path_edit.text().strip()
        return Path(source).parent / "ept_subset.laz" if source else Path("ept_subset.laz")

    def _set_ept_message(self, message: str) -> None:
        self.ept_message_label.setText(message)

    def run_explorer(self) -> None:
        """Run adapter-backed dataset inspection and write internal reports."""
        path = self.dataset_path_edit.text().strip()
        output_root = self.output_folder_edit.text().strip()
        if not path:
            self._set_dataset_message(empty_state_message("dataset"))
            return
        if not output_root:
            self._set_dataset_message("No output folder selected.\nChoose where Mission Control should write reports and products.")
            return
        context = create_run_context(path, output_root).ensure_directories()
        try:
            self.analyze_button.setEnabled(False)
            self.browse_dataset_button.setEnabled(False)
            self._set_dataset_message("Preparing dataset inspection...")
            QApplication.processEvents()
            inspection = self.adapter.inspect_dataset(path)
            report = build_dataset_explorer_report(inspection)
            write_json_report(report, context.dataset_report_json)
            write_html_report(report, context.dataset_report_html)
            write_csv_summary(report, context.dataset_summary_csv)
        except AdapterError as exc:
            self._set_dataset_message(f"Dataset inspection failed.\n{exc}")
            return
        except OSError as exc:
            self._set_dataset_message(f"Dataset reports could not be written.\n{exc}")
            return
        finally:
            self.analyze_button.setEnabled(True)
            self.browse_dataset_button.setEnabled(True)
        self.active_run = context
        self.set_report(report, context)
        self.set_footprint_preview(report, path, context)
        self.datasetExplored.emit(report, path, context)

    def set_report(self, report: DatasetExplorerReport, context: RunContext | None = None) -> None:
        """Display a Dataset Explorer report summary."""
        self.summary_text.setText("\n".join(compact_dataset_summary_lines(report)))
        technical_lines = [
            f"Density: {format_density_for_display(report.estimated_density)}",
            f"Point format: {report.point_format or 'Unknown'}",
            f"Metadata source: {report.metadata_source}",
        ]
        if report.dimensions:
            technical_lines.append(f"Dimensions: {', '.join(report.dimensions)}")
        if report.warnings:
            technical_lines.extend(("Warnings:", *[f"- {warning.code}: {warning.message}" for warning in report.warnings], ""))
        if report.products:
            technical_lines.extend(("Available products:", *[f"- {item.label}: {item.status}" for item in report.products], ""))
        if context:
            technical_lines.extend((f"Run folder: {context.run_folder}", f"Dataset Report: {context.dataset_report_html}"))
        self.dataset_technical_text.setText("\n".join(technical_lines).strip() or "No technical metadata warnings.")
        self.summary_section.setVisible(True)

    def _set_dataset_message(self, message: str) -> None:
        """Show a compact Dataset page empty or warning state."""
        self.summary_text.setText(message)
        self.summary_section.setVisible(True)

    def _reset_dataset_outputs(self) -> None:
        """Clear analysis-dependent UI when the selected dataset changes."""
        self.active_run = None
        self.footprint_preview = None
        self.summary_text.setText("Dataset selected. Click Analyze Dataset to inspect it.")
        self.summary_section.setVisible(True)
        self.dataset_technical_text.setText("Dataset technical metadata appears after analysis.")
        self.footprint_text.clear()
        self.spatial_section.setVisible(False)
        self.add_footprint_button.setEnabled(False)
        self.zoom_footprint_button.setEnabled(False)
        self.open_report_button.setEnabled(False)
        self.ept_use_output_button.setEnabled(self.ept_subset_output_path is not None)

    def set_footprint_preview(self, report: DatasetExplorerReport, dataset_path: str, context: RunContext | None = None) -> None:
        """Display a footprint preview built from Dataset Explorer bounds."""
        self.footprint_preview = preview_from_report(report, dataset_path)
        self.open_report_button.setEnabled(context is not None)
        if self.footprint_preview is None:
            self.footprint_text.setPlainText("Footprint unavailable: Dataset Explorer did not report usable XY bounds.")
            self.add_footprint_button.setEnabled(False)
            self.zoom_footprint_button.setEnabled(False)
            self.spatial_section.setVisible(True)
            return
        preview = self.footprint_preview
        crs = preview.crs or "Unknown"
        lines = [
            "Footprint status: READY",
            f"CRS: {crs}",
            f"Coordinate extent: {preview.extent_text}",
            f"Approximate area: {preview.area:g} square map units",
            f"Center point: {preview.center_text}",
        ]
        if preview.warnings:
            lines.extend(f"Warning: {message}" for message in preview.warnings)
        self.footprint_text.setPlainText("\n".join(lines))
        self.spatial_section.setVisible(True)
        self.add_footprint_button.setEnabled(True)
        self.zoom_footprint_button.setEnabled(bool(preview.crs))

    def add_footprint_layer(self) -> None:
        """Add the current footprint preview to the QGIS project."""
        if self.footprint_preview is None:
            self.footprint_text.setPlainText("Run Dataset Explorer before adding a footprint layer.")
            return
        result = add_footprint_layer(self.footprint_preview, self.iface)
        self._append_footprint_message(result.message)

    def zoom_to_footprint(self) -> None:
        """Zoom the QGIS map canvas to the current footprint preview."""
        if self.footprint_preview is None:
            self.footprint_text.setPlainText("Run Dataset Explorer before zooming to a footprint.")
            return
        result = zoom_to_footprint(self.footprint_preview, self.iface)
        self._append_footprint_message(result.message)

    def open_report(self) -> None:
        """Open the Dataset Explorer HTML report for the active run."""
        if self.active_run is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.active_run.dataset_report_html)))

    def _append_footprint_message(self, message: str) -> None:
        current = self.footprint_text.toPlainText().strip()
        self.footprint_text.setPlainText(f"{current}\n\n{message}" if current else message)


class ScientificAdvisorPage(MissionPage):
    """Scientific Advisor page driven by the deterministic Knowledge Engine."""

    def __init__(self, iface: object | None = None, parent: QWidget | None = None) -> None:
        """Create the advisor page."""
        super().__init__("Scientific Advisor", parent)
        self.iface = iface
        self.run_context: RunContext | None = None
        self.completed_products: tuple[str, ...] = ()

        self.content_widget.setObjectName("advisorBody")
        self.advisor_layout = self.content_layout
        self.advisor_layout.setContentsMargins(*PAGE_MARGINS)
        self.advisor_layout.setSpacing(SPACING_LG)

        executive = self._add_card("Executive Summary")
        self.executive_summary_label = _body_label(empty_state_message("advisor"))
        self.session_context_label = _details_label("Session: no active dataset or products yet.")
        executive.addWidget(self.executive_summary_label)
        executive.addWidget(self.session_context_label)

        overview = self._add_card("Dataset Health")
        self.overview_card = overview.parentWidget()
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(SECTION_SPACING)
        self.score_label = _advisor_metric_card("Dataset score", "Run Dataset Explorer to evaluate.")
        self.confidence_label = _advisor_metric_card("Confidence / readiness", "Unknown")
        metrics_row.addWidget(self.score_label)
        metrics_row.addWidget(self.confidence_label)
        overview.addLayout(metrics_row)

        recommendations = self._add_card("Key Recommendations")
        self.recommendations_card = recommendations.parentWidget()
        self.recommendation_list = _readable_list()
        recommendations.addWidget(self.recommendation_list)

        warnings = self._add_card("Warnings")
        self.warnings_card = warnings.parentWidget()
        self.warning_list = _readable_list()
        self.warning_list.setObjectName("advisorWarningList")
        warnings.addWidget(self.warning_list)

        products = self._add_card("Recommended Products")
        self.products_card = products.parentWidget()
        self.product_list = _readable_list()
        products.addWidget(self.product_list)

        parameters = self._add_card("Recommended Parameters")
        self.parameters_card = parameters.parentWidget()
        self.parameter_list = _readable_list()
        parameters.addWidget(self.parameter_list)

        qgis_tools = self._add_card("Recommended Next Actions")
        self.qgis_tools_card = qgis_tools.parentWidget()
        self.qgis_tools_summary = _body_label("After processing, inspect generated layers in QGIS Layer Styling and Histogram before sharing results.")
        qgis_tools.addWidget(self.qgis_tools_summary)
        self.open_output_folder_button = QPushButton("Open Output Folder")
        self.open_output_folder_button.setMinimumHeight(SECONDARY_BUTTON_HEIGHT)
        self.open_output_folder_button.clicked.connect(self.open_output_folder)
        _apply_button_role(self.open_output_folder_button, "secondary")
        qgis_tools.addWidget(self.open_output_folder_button)
        tools_group, tools_layout = _collapsible_section(self.advisor_layout, "QGIS Tool Instructions", checked=False)
        self.qgis_tools_details = _details_label(_tool_instruction_text())
        tools_layout.addWidget(self.qgis_tools_details)
        _wire_collapsible_group(tools_group)

        notes_group, notes = _collapsible_section(self.advisor_layout, "Scientific Notes", checked=False)
        self.notes_summary = _body_label("Recommendations will appear after Dataset Explorer runs. Threshold-based guidance is configurable and must be calibrated for production interpretation.")
        notes.addWidget(self.notes_summary)
        self.notes_details = _details_label("")
        notes.addWidget(self.notes_details)
        _wire_collapsible_group(notes_group)

        cards_group, cards = _collapsible_section(self.advisor_layout, "Product Explanations", checked=False)
        product_grid = QVBoxLayout()
        product_grid.setSpacing(SECTION_SPACING)
        for explanation in PRODUCT_EXPLANATIONS:
            product_grid.addWidget(_product_explanation_card(explanation))
        cards.addLayout(product_grid)
        _wire_collapsible_group(cards_group)

        next_steps = self._add_card("Next Steps")
        self.next_steps_card = next_steps.parentWidget()
        self.next_steps_label = _body_label("Run Dataset Explorer to generate top-priority next steps.")
        next_steps.addWidget(self.next_steps_label)
        for card in (self.overview_card, self.recommendations_card, self.warnings_card, self.products_card, self.parameters_card, self.qgis_tools_card, self.next_steps_card):
            card.setVisible(False)
        self.advisor_layout.addStretch(1)

    def _add_card(self, title: str) -> QVBoxLayout:
        """Add a spacious Advisor card section and return its layout."""
        frame = QFrame()
        frame.setObjectName("advisorCard")
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(SPACING_LG, SPACING_MD, SPACING_LG, SPACING_LG)
        layout.setSpacing(SECTION_SPACING)
        heading = QLabel(title)
        heading.setObjectName("advisorSectionHeading")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.advisor_layout.addWidget(frame)
        return layout

    def refresh_from_session(self, state: MissionControlSessionState) -> None:
        """Invalidate old guidance immediately and debounce a lightweight rebuild."""
        self._pending_session_state = state
        self.executive_summary_label.setText("Guidance is updating...")
        for card in (self.overview_card, self.recommendations_card, self.warnings_card,
                     self.products_card, self.parameters_card, self.qgis_tools_card, self.next_steps_card):
            card.setVisible(False)
        if not hasattr(self, "_session_refresh_timer"):
            self._session_refresh_timer = QTimer(self)
            self._session_refresh_timer.setSingleShot(True)
            self._session_refresh_timer.timeout.connect(self._render_pending_session)
        self._session_refresh_timer.start(150)

    def _render_pending_session(self) -> None:
        summary = build_scientific_advisor_summary(self._pending_session_state)
        self.set_session_summary(summary)

    def set_session_summary(self, summary: ScientificAdvisorSummary) -> None:
        """Render only guidance derived from the current source signature."""
        self.current_session_summary = summary
        self.executive_summary_label.setText(summary.executive_summary)
        self.session_context_label.setText("Guidance reflects the current Batch selections.")
        for widget, values in ((self.recommendation_list, summary.key_recommendations),
                               (self.warning_list, summary.warnings),
                               (self.product_list, summary.recommended_products),
                               (self.parameter_list, summary.parameter_recommendations)):
            widget.clear()
            for value in values:
                _add_advisor_item(widget, str(value))
        self.recommendations_card.setVisible(bool(summary.key_recommendations))
        self.warnings_card.setVisible(bool(summary.warnings))
        self.products_card.setVisible(bool(summary.recommended_products))
        self.parameters_card.setVisible(bool(summary.parameter_recommendations))

    def set_run_context(self, context: RunContext | None) -> None:
        """Store active run context for output-folder actions."""
        self.run_context = context

    def set_project_summary(self, summary: ProjectSummary) -> None:
        """Display compact session context for Advisor recommendations."""
        self.session_context_label.setText(
            f"Session: {summary.dataset_name}; {summary.generated_summary()}; {summary.loaded_summary()}"
        )

    def reset_for_new_dataset(self) -> None:
        """Clear recommendation content when a different dataset is selected."""
        self.run_context = None
        self.completed_products = ()
        self.executive_summary_label.setText("Dataset selected. Analyze it to receive recommendations.")
        self.session_context_label.setText("Session: dataset changed; recommendations are pending.")
        self.notes_summary.setText("Recommendations will appear after Dataset Explorer runs.")
        self.notes_details.setText("")
        self.next_steps_label.setText("Run Dataset Explorer to generate top-priority next steps.")
        for card in (self.overview_card, self.recommendations_card, self.warnings_card, self.products_card, self.parameters_card, self.qgis_tools_card, self.next_steps_card):
            card.setVisible(False)

    def set_recommendation_report(self, report: RecommendationReport) -> None:
        """Display a Knowledge Engine recommendation report."""
        has_guidance = bool(report.suggested_next_actions or report.warnings or report.recommended_products or report.recommended_parameters)
        if not has_guidance:
            self.executive_summary_label.setText(
                "Dataset analyzed. No recommendations were produced for this dataset.\n"
                "Next: continue to Planning and choose products manually."
            )
            for card in (self.overview_card, self.recommendations_card, self.warnings_card, self.products_card, self.parameters_card, self.qgis_tools_card, self.next_steps_card):
                card.setVisible(False)
            return
        self.overview_card.setVisible(True)
        self.score_label.setText(f"<b>Dataset score</b><br>{report.dataset_score}/100")
        self.confidence_label.setText(f"<b>Confidence / readiness</b><br>{_stars(report.confidence_stars)} ({report.confidence_stars}/5)")
        best_product = report.recommended_products[0].label if report.recommended_products else "No product recommendation yet"
        key_warning = report.warnings[0].reason if report.warnings else "No blocking warning from the Knowledge Engine"
        next_action = report.suggested_next_actions[0].suggested_action if report.suggested_next_actions else "Build a Product Plan when the dataset report is ready"
        self.executive_summary_label.setText(
            f"Dataset readiness: {report.dataset_score}/100 with {_stars(report.confidence_stars)} confidence.\n"
            f"Best product to consider: {best_product}.\n"
            f"Key warning: {key_warning}.\n"
            f"Next: {next_action}"
        )

        self.recommendation_list.clear()
        for item in report.suggested_next_actions[:4]:
            _add_advisor_item(self.recommendation_list, f"{item.reason}\nNext: {item.suggested_action}")
        self.recommendations_card.setVisible(bool(report.suggested_next_actions))

        self.warning_list.clear()
        for item in report.warnings[:5]:
            _add_advisor_item(self.warning_list, f"{item.severity.value.upper()} - {item.reason}\nAction: {item.suggested_action}", 68)
        if len(report.warnings) > 5:
            _add_advisor_item(self.warning_list, f"{len(report.warnings) - 5} additional actionable warning(s) are included in Scientific Notes.")
        self.warnings_card.setVisible(bool(report.warnings))

        self.product_list.clear()
        for product in report.recommended_products[:6]:
            _add_advisor_item(self.product_list, f"{product.label}: {product.status}\n{product.reason}")
        self.products_card.setVisible(bool(report.recommended_products))

        self.parameter_list.clear()
        for parameter in report.recommended_parameters[:6]:
            calibration = " Calibration required." if parameter.calibration_required else ""
            _add_advisor_item(self.parameter_list, f"{parameter.product} {parameter.name}: {parameter.value} {parameter.unit}\n{parameter.reason}{calibration}", 68)
        self.parameters_card.setVisible(bool(report.recommended_parameters))
        self.qgis_tools_card.setVisible(bool(report.recommended_products))
        self.next_steps_card.setVisible(True)

        note_count = len(report.scientific_notes)
        threshold_count = len([threshold for threshold in report.thresholds if threshold.calibration_required])
        self.notes_summary.setText(
            f"{note_count} scientific note(s) and {threshold_count} calibration-sensitive threshold(s) are available. "
            "Use the details below for transparent rationale before interpreting products."
        )
        scientific_notes = [f"* {item.code}: {item.reason} {item.scientific_note or ''}" for item in report.scientific_notes]
        threshold_notes = [f"* {threshold.name}: {threshold.value if threshold.value is not None else 'unset'} {threshold.unit}. {threshold.rationale}" for threshold in report.thresholds if threshold.calibration_required]
        self.notes_details.setText(
            "<b>More details</b><br>"
            + _html_lines(("Scientific notes:", *scientific_notes, "", "Configurable thresholds:", *threshold_notes))
        )
        self._update_next_steps(report)

    def set_completed_products(self, products: tuple[str, ...]) -> None:
        """Update next-step context after processing completes."""
        self.completed_products = products
        if products:
            completed = ", ".join(_product_label(product) for product in products)
            self.next_steps_label.setText(
                f"Completed products: {completed}\n\n"
                "Next: inspect loaded layers with Layer Styling and Histogram, compare extents/CRS, and open the final run summary."
            )

    def open_processing_toolbox(self) -> None:
        """Open QGIS Processing Toolbox when the iface exposes a stable hook."""
        method = getattr(self.iface, "openProcessingToolbox", None) if self.iface is not None else None
        if callable(method):
            method()
            return
        self.qgis_tools_details.setText(_html_lines((_tool_instruction_text(), "", "Processing Toolbox: open it from Processing > Toolbox in QGIS.")))

    def open_layer_styling(self) -> None:
        """Open selected-layer properties when available, otherwise show instructions."""
        layer = _selected_layer(self.iface)
        method = getattr(self.iface, "showLayerProperties", None) if self.iface is not None else None
        if layer is not None and callable(method):
            method(layer)
            return
        self.qgis_tools_details.setText(_html_lines((_tool_instruction_text(), "", "Layer Styling: select a raster layer, then open Layer Styling or Layer Properties > Symbology.")))

    def zoom_to_selected_layer(self) -> None:
        """Zoom the main QGIS canvas to the selected layer when available."""
        layer = _selected_layer(self.iface)
        canvas = self.iface.mapCanvas() if self.iface is not None and hasattr(self.iface, "mapCanvas") else None
        if layer is not None and canvas is not None and hasattr(layer, "extent"):
            canvas.setExtent(layer.extent())
            canvas.refresh()
            return
        self.qgis_tools_details.setText(_html_lines((_tool_instruction_text(), "", "Zoom: select an output layer in the Layers panel, then use Zoom to Layer in QGIS.")))

    def open_output_folder(self) -> None:
        """Open the active run output folder."""
        if self.run_context is None:
            self.next_steps_label.setText("Run Dataset Explorer before opening an output folder.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.run_context.outputs_dir)))

    def _update_next_steps(self, report: RecommendationReport) -> None:
        if report.warnings:
            first = report.warnings[0]
            text = f"Start here: {first.suggested_action}"
        else:
            text = "Start here: build a product plan with the recommended products and settings."
        self.next_steps_label.setText(text + "\n\nUse QGIS review tools after processing before sharing outputs.")


class PlanningPage(MissionPage):
    """Product planning page using the active run context."""

    planningChanged = pyqtSignal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the planning page."""
        super().__init__("Planning", parent)
        self.dataset_report: DatasetExplorerReport | None = None
        self.run_context: RunContext | None = None
        self.latest_plan: ProductPlannerReport | None = None

        dataset = self.add_section("Dataset")
        self.dataset_context_label = _body_label("Run Dataset Explorer to load an active dataset report for planning.")
        dataset.addWidget(self.dataset_context_label)

        output_group, output = _collapsible_section(self.content_layout, "Advanced Output Folder", checked=False)
        self.output_folder_edit = QLineEdit()
        self.output_folder_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        folder_button = QPushButton("Browse")
        folder_button.setMinimumHeight(SECONDARY_BUTTON_HEIGHT)
        folder_button.clicked.connect(self.browse_folder)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(ACTION_ROW_SPACING)
        folder_row.addWidget(self.output_folder_edit, 1)
        folder_row.addWidget(folder_button, 0)
        output.addWidget(_body_label("Mission Control normally uses the active run folder. Override it only when you need a different output location."))
        output.addLayout(folder_row)
        _wire_collapsible_group(output_group)

        products = self.add_section("Product Selection")
        self.product_checks: dict[ProductType, QCheckBox] = {}
        product_grid = QGridLayout()
        product_grid.setHorizontalSpacing(SPACING_XL)
        product_grid.setVerticalSpacing(SPACING_SM)
        for index, (product, label) in enumerate(PRODUCT_LABELS.items()):
            check = QCheckBox(label)
            check.setProperty("contextHelp", semantic_help(f"product.{product.value}"))
            check.setMinimumHeight(SECONDARY_BUTTON_HEIGHT)
            if product is ProductType.CHM:
                check.setChecked(True)
            self.product_checks[product] = check
            product_grid.addWidget(check, index // 2, index % 2)
        products.addLayout(product_grid)
        product_button_row = QHBoxLayout()
        product_button_row.setSpacing(ACTION_ROW_SPACING)
        select_all = QPushButton("Select All Products")
        select_all.setMinimumHeight(SECONDARY_BUTTON_HEIGHT)
        select_all.clicked.connect(lambda: self._set_all_products(True))
        _apply_button_role(select_all, "neutral")
        clear_all = QPushButton("Clear Products")
        clear_all.setMinimumHeight(SECONDARY_BUTTON_HEIGHT)
        clear_all.clicked.connect(lambda: self._set_all_products(False))
        _apply_button_role(clear_all, "neutral")
        product_button_row.addWidget(select_all)
        product_button_row.addWidget(clear_all)
        product_button_row.addStretch(1)
        products.addLayout(product_button_row)

        shared = self.add_section("Shared Parameters")
        shared_form = QFormLayout()
        shared_form.setLabelAlignment(Qt.AlignLeft)
        shared_form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        shared_form.setHorizontalSpacing(SPACING_LG)
        shared_form.setVerticalSpacing(SECTION_SPACING)
        self.resolution_spin = QDoubleSpinBox()
        self.resolution_spin.setDecimals(3)
        self.resolution_spin.setMinimum(0.01)
        self.resolution_spin.setValue(1.0)
        self.height_bin_spin = QDoubleSpinBox()
        self.height_bin_spin.setDecimals(3)
        self.height_bin_spin.setMinimum(0.0)
        self.height_bin_spin.setSpecialValueText("Not specified")
        self.height_bin_spin.setValue(1.0)
        shared_form.addRow("Grid resolution", self.resolution_spin)
        self.resolution_spin.setProperty("contextHelp", semantic_help("parameter.grid_resolution"))
        shared_form.addRow("Height bin size", self.height_bin_spin)
        self.height_bin_spin.setProperty("contextHelp", semantic_help("parameter.voxel_height"))
        shared.addLayout(shared_form)

        product_params_group, product_params = _collapsible_section(self.content_layout, "Advanced Product Settings", checked=False)
        product_params.addWidget(_details_label("Expand only when you need to change product-specific filenames, CHM interpolation options, or canopy-cover threshold. Recommended/shared settings above are enough for the default workflow."))
        params_grid = QGridLayout()
        params_grid.setHorizontalSpacing(SPACING_XL)
        params_grid.setVerticalSpacing(SECTION_SPACING)
        chm_box = QGroupBox("CHM")
        chm_layout = QFormLayout(chm_box)
        chm_layout.setVerticalSpacing(SPACING_SM)
        self.chm_interpolation_combo = QComboBox()
        self.chm_interpolation_combo.addItems(("linear", "nearest", "cubic"))
        self.chm_valid_region_check = QCheckBox("Interpolate valid region")
        self.chm_clean_edges_check = QCheckBox("Clean edges")
        self.chm_output_filename_edit = QLineEdit("chm.tif")
        chm_layout.addRow("Interpolation", self.chm_interpolation_combo)
        chm_layout.addRow("Valid region", self.chm_valid_region_check)
        chm_layout.addRow("Edges", self.chm_clean_edges_check)
        chm_layout.addRow("Output filename", self.chm_output_filename_edit)

        canopy_box = QGroupBox("Canopy Cover")
        canopy_layout = QFormLayout(canopy_box)
        canopy_layout.setVerticalSpacing(SPACING_SM)
        self.canopy_cover_threshold_spin = QDoubleSpinBox()
        self.canopy_cover_threshold_spin.setDecimals(3)
        self.canopy_cover_threshold_spin.setMinimum(0.0)
        self.canopy_cover_threshold_spin.setValue(2.0)
        self.canopy_cover_output_filename_edit = QLineEdit("canopy_cover.tif")
        canopy_layout.addRow("Height threshold", self.canopy_cover_threshold_spin)
        canopy_layout.addRow("Output filename", self.canopy_cover_output_filename_edit)

        raster_box = QGroupBox("PAD / PAI / FHD / Rumple")
        raster_layout = QFormLayout(raster_box)
        raster_layout.setVerticalSpacing(SPACING_SM)
        self.pad_output_filename_edit = QLineEdit("pad.tif")
        self.pai_output_filename_edit = QLineEdit("pai.tif")
        self.fhd_output_filename_edit = QLineEdit("fhd.tif")
        self.rumple_output_filename_edit = QLineEdit("rumple.tif")
        raster_layout.addRow("PAD output", self.pad_output_filename_edit)
        raster_layout.addRow("PAI output", self.pai_output_filename_edit)
        raster_layout.addRow("FHD output", self.fhd_output_filename_edit)
        raster_layout.addRow("Rumple output", self.rumple_output_filename_edit)

        for box in (chm_box, canopy_box, raster_box):
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        params_grid.addWidget(chm_box, 0, 0)
        params_grid.addWidget(canopy_box, 0, 1)
        params_grid.addWidget(raster_box, 1, 0, 1, 2)
        params_grid.setColumnStretch(0, 1)
        params_grid.setColumnStretch(1, 1)
        product_params.addLayout(params_grid)
        _wire_collapsible_group(product_params_group)

        summary = self.add_section("Plan Summary")
        plan_actions = QHBoxLayout()
        plan_actions.setSpacing(ACTION_ROW_SPACING)
        self.build_plan_button = QPushButton("Build Plan")
        self.build_plan_button.setMinimumHeight(PRIMARY_BUTTON_HEIGHT)
        self.build_plan_button.clicked.connect(self.build_plan)
        _apply_button_role(self.build_plan_button, "primary")
        self.build_plan_button.setEnabled(False)
        self.refresh_plan_button = QPushButton("Refresh Plan State")
        self.refresh_plan_button.clicked.connect(self.refresh_plan_state)
        _apply_button_role(self.refresh_plan_button, "neutral")
        plan_actions.addWidget(self.build_plan_button)
        plan_actions.addWidget(self.refresh_plan_button)
        plan_actions.addStretch(1)
        summary.addLayout(plan_actions)
        self.plan_text = QTextEdit()
        self.plan_text.setReadOnly(True)
        self.plan_text.setMinimumHeight(COMPACT_LIST_HEIGHT)
        self.plan_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.plan_text.setPlainText(empty_state_message("planning"))
        summary.addWidget(self.plan_text)

    def _set_all_products(self, checked: bool) -> None:
        """Set all product checkboxes to a common state."""
        for check in self.product_checks.values():
            check.setChecked(checked)

    def refresh_plan_state(self) -> None:
        """Recover Planning page controls from the current dataset/run state."""
        ready = self.dataset_report is not None
        self.build_plan_button.setEnabled(ready)
        if ready:
            self.plan_text.setPlainText("Plan state refreshed. Choose products and build or rebuild the plan.")
        else:
            self.plan_text.setPlainText("Plan state refreshed. Run Dataset Explorer before building a product plan.")

    def set_dataset_report(self, report: DatasetExplorerReport, context: RunContext | None = None) -> None:
        """Store latest Dataset Explorer report and run context for planning."""
        self.dataset_report = report
        self.run_context = context
        dataset_name = context.lidar_path.name if context is not None else "loaded dataset"
        run_folder = str(context.run_folder) if context is not None else "not assigned"
        self.dataset_context_label.setText(
            f"Active dataset: {dataset_name}\nRun folder: {run_folder}\nChoose products and parameters, then build the Product Planner report."
        )
        if context is not None:
            self.output_folder_edit.setText(str(context.outputs_dir))
        self.plan_text.setPlainText("Dataset loaded. Choose products and build a plan.")
        self.build_plan_button.setEnabled(True)

    def reset_for_new_dataset(self, dataset_name: str = "selected dataset") -> None:
        """Clear plan state when the selected dataset changes."""
        self.dataset_report = None
        self.run_context = None
        self.latest_plan = None
        self.dataset_context_label.setText(f"Dataset selected: {dataset_name}\nAnalyze the dataset before choosing products.")
        self.output_folder_edit.clear()
        self.plan_text.setPlainText("Analyze the selected dataset before building a product plan.")
        self.build_plan_button.setEnabled(False)

    def apply_recommendation_report(self, report: RecommendationReport) -> None:
        """Adopt practical Advisor parameter recommendations into planning controls."""
        adopted: list[str] = []
        for parameter in report.recommended_parameters:
            if parameter.product == "chm" and parameter.name == "grid_resolution":
                try:
                    value = float(parameter.value)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    self.resolution_spin.setValue(value)
                    adopted.append(f"CHM grid resolution set to {value:g} {parameter.unit}")
        if adopted:
            current = self.plan_text.toPlainText().strip()
            note = "Advisor recommendations adopted:\n" + "\n".join(f"- {item}" for item in adopted)
            self.plan_text.setPlainText(f"{current}\n\n{note}" if current else note)

    def browse_folder(self) -> None:
        """Choose a future output folder."""
        path = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if path:
            self.output_folder_edit.setText(path)

    def build_plan(self) -> None:
        """Build an in-memory product plan without writing outputs."""
        if self.dataset_report is None:
            self.plan_text.setPlainText("Run Dataset Explorer before building a product plan.")
            return
        self.build_plan_button.setEnabled(False)
        self.plan_text.setPlainText("Preparing product plan...")
        QApplication.processEvents()
        selected = tuple(product for product, check in self.product_checks.items() if check.isChecked())
        output_folder = self.run_context.outputs_dir if self.run_context is not None else Path(self.output_folder_edit.text().strip() or "planned_outputs")
        height_bin_size = self.height_bin_spin.value() if self.height_bin_spin.value() > 0 else None
        request = ProductPlannerRequest(
            explorer_report_path=self.run_context.dataset_report_json if self.run_context is not None else Path("mission_control_dataset_report.json"),
            requested_products=selected,
            output_folder=output_folder,
            grid_resolution=self.resolution_spin.value(),
            height_bin_size=height_bin_size,
            chm_interpolation=self.chm_interpolation_combo.currentText(),
            chm_interpolate_valid_region=self.chm_valid_region_check.isChecked(),
            chm_clean_edges=self.chm_clean_edges_check.isChecked(),
            chm_output_filename=self.chm_output_filename_edit.text().strip() or "chm.tif",
            pad_output_filename=self.pad_output_filename_edit.text().strip() or "pad.tif",
            pai_output_filename=self.pai_output_filename_edit.text().strip() or "pai.tif",
            fhd_output_filename=self.fhd_output_filename_edit.text().strip() or "fhd.tif",
            rumple_output_filename=self.rumple_output_filename_edit.text().strip() or "rumple.tif",
            canopy_cover_height_threshold=self.canopy_cover_threshold_spin.value(),
            canopy_cover_output_filename=self.canopy_cover_output_filename_edit.text().strip() or "canopy_cover.tif",
            title="Mission Control Product Plan",
        )
        try:
            plan = build_product_plan(report_to_dict(self.dataset_report), request)
        except ProductPlanError as exc:
            self.plan_text.setPlainText(f"Product plan failed: {exc}")
            self.build_plan_button.setEnabled(True)
            self.planningChanged.emit("Needs review", None)
            return
        try:
            if self.run_context is not None:
                write_plan_json(plan, self.run_context.product_plan_json)
                write_plan_csv(plan, self.run_context.product_plan_csv)
                write_plan_html(plan, self.run_context.product_plan_html)
        except OSError as exc:
            self.plan_text.setPlainText(f"Product plan reports could not be written: {exc}")
            self.build_plan_button.setEnabled(True)
            self.planningChanged.emit("Needs review", None)
            return
        self.latest_plan = plan
        ready = sum(1 for product in plan.products if product.plan_status == "Ready")
        review = sum(1 for product in plan.products if product.plan_status == "Needs review")
        blocked = sum(1 for product in plan.products if product.plan_status == "Blocked")
        plan_path = f"Product Plan: {self.run_context.product_plan_html}" if self.run_context is not None else "Product Plan: in memory"
        lines = [
            f"Ready: {ready}",
            f"Needs review: {review}",
            f"Blocked: {blocked}",
            f"Estimated cells: {plan.estimated_cells if plan.estimated_cells is not None else 'Unknown'}",
            f"CHM interpolation: {plan.chm_interpolation}",
            f"CHM valid region interpolation: {plan.chm_interpolate_valid_region}",
            f"CHM clean edges: {plan.chm_clean_edges}",
            f"CHM output: {plan.output_folder / plan.chm_output_filename}",
            f"PAD output: {plan.output_folder / plan.pad_output_filename}",
            f"PAI output: {plan.output_folder / plan.pai_output_filename}",
            f"FHD output: {plan.output_folder / plan.fhd_output_filename}",
            f"Rumple output: {plan.output_folder / plan.rumple_output_filename}",
            f"Canopy cover threshold: {plan.canopy_cover_height_threshold}",
            f"Canopy cover output: {plan.output_folder / plan.canopy_cover_output_filename}",
            plan_path,
            "",
        ]
        lines.extend(f"- {item.label}: {item.plan_status}" for item in plan.products)
        self.plan_text.setPlainText("\n".join(lines))
        self.build_plan_button.setEnabled(True)
        self.planningChanged.emit("Ready" if blocked == 0 else "Needs review", plan)


class ProcessingPage(MissionPage):
    """Pipeline execution page using the active product plan."""

    jobUpdated = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the processing page."""
        super().__init__("Processing", parent)
        self.job_manager = JobManager(event_sink=self._on_job_update)
        self.current_job_id: str | None = None
        self.run_context: RunContext | None = None
        self.current_footprint: ProcessingFootprint | None = None

        overview = self.add_section("Ready To Run")
        self.selected_products_label = _body_label("Selected products: build a Product Plan first.")
        self.current_output_label = _body_label("Outputs: choose a dataset and output folder, then build a Product Plan.")
        self.footprint_label = _body_label("Processing footprint: build a Product Plan to see expected outputs, raster size, bands, and storage.")
        self.already_generated_label = _body_label("Already generated: None")
        self.status_label = QLabel()
        _set_status_badge(self.status_label, "NOT CONFIGURED", "Status: Not set up - build a Product Plan first.")
        overview.addWidget(self.selected_products_label)
        overview.addWidget(self.already_generated_label)
        overview.addWidget(self.current_output_label)
        overview.addWidget(self.footprint_label)
        overview.addWidget(self.status_label)

        self.execution_backend_label = _body_label("Execution backend: PBM when READY; QGIS Python fallback only when PBM is unavailable.")
        overview.addWidget(self.execution_backend_label)

        self.job_title_edit = QLineEdit("Mission Control Product Job")
        self.job_title_edit.setPlaceholderText("Optional run label")
        overview.addWidget(self.job_title_edit)
        self.job_title_edit.setProperty("contextHelp", "Optional descriptive name stored with this run's logs and provenance. It does not change scientific calculations or output contents.")

        button_row = QHBoxLayout()
        button_row.setSpacing(ACTION_ROW_SPACING)
        self.start_button = QPushButton(primary_action_label("processing"))
        self.start_button.setMinimumHeight(PRIMARY_BUTTON_HEIGHT)
        self.start_button.clicked.connect(self.start_job)
        _apply_button_role(self.start_button, "primary")
        self.validate_request_button = QPushButton("Validate Processing Request")
        self.validate_request_button.setMinimumHeight(SECONDARY_BUTTON_HEIGHT)
        self.validate_request_button.clicked.connect(self.validate_processing_request)
        _apply_button_role(self.validate_request_button, "secondary")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setMinimumHeight(PRIMARY_BUTTON_HEIGHT)
        self.cancel_button.clicked.connect(self.cancel_current_job)
        _apply_button_role(self.cancel_button, "danger")
        self.cancel_button.setEnabled(False)
        self.refresh_processing_button = QPushButton("Refresh Processing State")
        self.refresh_processing_button.clicked.connect(self.refresh_processing_state)
        _apply_button_role(self.refresh_processing_button, "neutral")
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.validate_request_button)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.refresh_processing_button)
        button_row.addStretch(1)
        overview.addLayout(button_row)

        progress = self.add_section("Current Progress")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress.addWidget(self.progress_bar)
        self.processing_stage_label = _body_label("Stage: Not started")
        progress.addWidget(self.processing_stage_label)
        progress.addWidget(_body_label("Keep QGIS open until processing completes."))

        technical_group, technical = _collapsible_section(self.content_layout, "Technical Details", checked=False)
        technical.addWidget(_details_label("Run files, plan paths, processing stages, and logs are shown here for troubleshooting."))
        self.current_plan_label = QLabel("Product plan file: none")
        self.current_plan_label.setWordWrap(True)
        technical.addWidget(self.current_plan_label)
        plan_row = QHBoxLayout()
        self.product_plan_edit = QLineEdit()
        self.product_plan_edit.setPlaceholderText("Optional Product Planner JSON override")
        plan_browse = QPushButton("Browse")
        plan_browse.clicked.connect(self.browse_product_plan)
        plan_row.addWidget(self.product_plan_edit, 1)
        plan_row.addWidget(plan_browse, 0)
        technical.addLayout(plan_row)
        output_row = QHBoxLayout()
        self.job_output_folder_edit = QLineEdit()
        self.job_output_folder_edit.setPlaceholderText("Optional job log folder override")
        output_browse = QPushButton("Browse")
        output_browse.clicked.connect(self.browse_output_folder)
        output_row.addWidget(self.job_output_folder_edit, 1)
        output_row.addWidget(output_browse, 0)
        technical.addLayout(output_row)
        self.pipeline_list = QListWidget()
        self.pipeline_list.setMinimumHeight(TECHNICAL_DETAIL_HEIGHT)
        technical.addWidget(self.pipeline_list)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(TECHNICAL_DETAIL_HEIGHT)
        technical.addWidget(self.log_text)
        _wire_collapsible_group(technical_group)

    def validate_processing_request(self) -> None:
        """Explain the PBM-side validation gate before running products."""
        if self.run_context is None or not self.run_context.product_plan_json.exists():
            _set_status_badge(self.status_label, "NOT CONFIGURED", "Status: Not set up - build a Product Plan before validation.")
            self.log_text.setPlainText((self.log_text.toPlainText().strip() + "\n" if self.log_text.toPlainText().strip() else "") + "Validate Processing Request: build a Product Plan first.")
            return
        _set_status_badge(self.status_label, "READY", "Status: Request validation will run in PBM before product execution.")
        self.processing_stage_label.setText("Stage: Request validation ready")
        self.log_text.setPlainText((self.log_text.toPlainText().strip() + "\n" if self.log_text.toPlainText().strip() else "") + "Validate Processing Request: PBM will check API compatibility, EPT metadata, bounds syntax, polygon file, CRS, and output writability before reading point data.")

    def refresh_processing_state(self) -> None:
        """Recover Processing controls from the current run context and job state."""
        self._refresh_plan_summary()
        job = self.job_manager.get_job(self.current_job_id) if self.current_job_id else None
        running = job is not None and job.status in {JobStatus.PENDING, JobStatus.VALIDATING, JobStatus.RUNNING, JobStatus.CANCELLING}
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(bool(running))
        if not running and self.processing_stage_label.text().strip() == "Stage: Not started":
            self.processing_stage_label.setText("Stage: Ready when a Product Plan is available")
        self.log_text.setPlainText((self.log_text.toPlainText().strip() + "\n" if self.log_text.toPlainText().strip() else "") + "Processing state refreshed.")

    def set_run_context(self, context: RunContext | None) -> None:
        """Use the active Mission Control run context."""
        self.run_context = context
        if context is None:
            self.current_plan_label.setText("Product plan file: none")
            self.selected_products_label.setText("Selected products: build a Product Plan first.")
            self.current_output_label.setText("Outputs: choose a dataset and output folder, then build a Product Plan.")
            self.footprint_label.setText("Processing footprint: build a Product Plan to see expected outputs, raster size, bands, and storage.")
            self.processing_stage_label.setText("Stage: Not started")
            return
        self.product_plan_edit.setText(str(context.product_plan_json))
        self.job_output_folder_edit.setText(str(context.logs_dir))
        self.current_plan_label.setText(f"Product plan file: {context.product_plan_json}")
        self.current_output_label.setText(f"Outputs: {context.outputs_dir}")
        self._refresh_plan_summary()

    def _refresh_plan_summary(self) -> None:
        """Refresh selected products and footprint summary from the active Product Plan."""
        plan_path = Path(self.product_plan_edit.text().strip()) if self.product_plan_edit.text().strip() else None
        if plan_path is None or not plan_path.exists():
            self.selected_products_label.setText("Selected products: build a Product Plan first.")
            self.footprint_label.setText("Processing footprint: build a Product Plan to see expected outputs, raster size, bands, and storage.")
            self.processing_stage_label.setText("Stage: Not started")
            return
        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            products = [item for item in payload.get("products", []) if isinstance(item, dict) and item.get("requested", True)]
            labels = [str(item.get("label") or item.get("product")) for item in products]
            footprint = footprint_from_plan_file(plan_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self.selected_products_label.setText("Selected products: Product Plan could not be read.")
            self.footprint_label.setText(f"Processing footprint: unavailable ({exc})")
            return
        self.current_footprint = footprint
        self.selected_products_label.setText("Selected products: " + (", ".join(labels) if labels else "none"))
        self.footprint_label.setText(_processing_footprint_text(footprint))

    def set_project_summary(self, summary: ProjectSummary) -> None:
        """Display generated-product awareness before users rerun processing."""
        if summary.generated_products:
            labels = ", ".join(item.label for item in summary.generated_products)
            self.already_generated_label.setText(f"Already generated: {labels}")
        else:
            self.already_generated_label.setText("Already generated: None")

    def browse_product_plan(self) -> None:
        """Choose a Product Planner JSON report for advanced troubleshooting."""
        path, _ = QFileDialog.getOpenFileName(self, "Choose Product Planner JSON", "", "JSON reports (*.json);;All files (*.*)")
        if path:
            self.product_plan_edit.setText(path)
            self._refresh_plan_summary()

    def browse_output_folder(self) -> None:
        """Choose a job summary output folder for advanced troubleshooting."""
        path = QFileDialog.getExistingDirectory(self, "Choose job output folder")
        if path:
            self.job_output_folder_edit.setText(path)
            self.current_output_label.setText(f"Outputs: {path}")

    def start_job(self) -> None:
        """Start a processing job from the active Product Planner report."""
        plan_path = self.product_plan_edit.text().strip()
        output_folder = self.job_output_folder_edit.text().strip()
        summary_path = self.run_context.job_summary_json if self.run_context is not None else None
        if not plan_path:
            _set_status_badge(self.status_label, "WARNING", "Status: Needs review - build a product plan before starting.")
            self.log_text.setPlainText("Build a product plan before starting a processing job.")
            return
        if not Path(plan_path).exists():
            _set_status_badge(self.status_label, "WARNING", "Status: Needs review - build a product plan before starting.")
            self.log_text.setPlainText("Build a product plan before starting a processing job.")
            return
        if not output_folder:
            _set_status_badge(self.status_label, "WARNING", "Status: Needs review - choose an output folder before starting.")
            self.log_text.setPlainText("Choose an output folder for the job summary JSON.")
            return
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.processing_stage_label.setText("Stage: Preparing")
        self.progress_bar.setValue(5)
        self.log_text.clear()
        execution_backend = self.job_manager.execution_backend().replace("_", " ")
        self.execution_backend_label.setText(f"Execution backend: {execution_backend}")
        self.log_text.setPlainText(f"Execution backend: {execution_backend}.\n")
        try:
            job = self.job_manager.run_pipeline(
                Path(plan_path),
                Path(output_folder),
                self.job_title_edit.text().strip() or "Mission Control Product Job",
                summary_path=summary_path,
            )
        except JobExecutionError as exc:
            _set_status_badge(self.status_label, "FAILED", f"Status: Failed - processing job could not start: {exc}")
            self.log_text.setPlainText(f"Processing job could not start: {exc}")
            self.processing_stage_label.setText("Stage: Failed")
            self.start_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            return
        self.current_job_id = job.job_id
        self._on_job_update(job)
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(job.status in {JobStatus.PENDING, JobStatus.VALIDATING, JobStatus.RUNNING, JobStatus.CANCELLING})

    def cancel_current_job(self) -> None:
        """Request cancellation for the current job when it is still active."""
        if self.current_job_id is None:
            return
        job = self.job_manager.request_cancel(self.current_job_id)
        if job is not None:
            self._on_job_update(job)

    def _on_job_update(self, job: JobRecord) -> None:
        """Bridge core job progress into Qt widgets."""
        self.current_job_id = job.job_id
        _set_status_badge(self.status_label, job.status.value, f"Status: {status_display_word(job.status.value)} - {job.status.value}")
        self.progress_bar.setValue(int(job.progress.percent))
        self.processing_stage_label.setText(f"Stage: {_processing_lifecycle_stage(job)}")
        self.execution_backend_label.setText(f"Execution backend: {self.job_manager.execution_backend().replace('_', ' ')}")
        self.log_text.setPlainText("\n".join(f"{entry.level}: {entry.message}" for entry in job.logs))
        self.cancel_button.setEnabled(job.status in {JobStatus.PENDING, JobStatus.VALIDATING, JobStatus.RUNNING, JobStatus.CANCELLING})
        self._set_pipeline_results(job)
        self.jobUpdated.emit(job)

    def _set_pipeline_results(self, job: JobRecord) -> None:
        """Display pipeline stages for the current job."""
        self.pipeline_list.clear()
        for pipeline in job.pipeline_results:
            self.pipeline_list.addItem(f"{pipeline.label}")
            for step in pipeline.steps:
                self.pipeline_list.addItem(f"  {_pipeline_status_icon(step.status.value)} {step.label}: {step.message}")


class _BatchExecutionWorker(QObject):
    """Qt worker that runs BatchExecutor away from the UI thread."""

    itemReady = pyqtSignal(object)
    jobReady = pyqtSignal(object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, request: BatchRequest, control_callback: Callable[[], str | None]) -> None:
        """Store immutable batch request and cancellation callback."""
        super().__init__()
        self.request = request
        self.control_callback = control_callback

    def run(self) -> None:
        """Execute the batch and emit a final result or error message."""
        try:
            from ..core.backend import BackendService
            token = self.request.runtime_token
            BackendService().processing_engine_service().validate_runtime_token_for_launch(
                token,
                tuple(product.value for product in self.request.settings.products),
                self.request.batch_folder,
            )
            result = BatchExecutor(adapter_factory=lambda: PyForestScanAdapter(execution_mode="pbm_backend")).run(
                self.request,
                item_callback=self.itemReady.emit,
                job_callback=self.jobReady.emit,
                control_callback=self.control_callback,
            )
        except BatchExecutionError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - worker must report unexpected failures to UI.
            self.failed.emit(f"Unexpected batch failure: {exc}")
            return
        self.completed.emit(result)


class _PolygonPreflightWorker(QObject):
    """Run pure polygon prerun planning and manifest I/O away from QGIS GUI objects."""

    progress = pyqtSignal(str)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, request: PolygonBatchRequest, cancel_callback: Callable[[], bool]) -> None:
        super().__init__()
        self.request = request
        self.cancel_callback = cancel_callback

    def run(self) -> None:
        profiler = PrerunProfiler()
        diagnostics_root = self.request.batch_folder or (Path(self.request.output_folder) / ".pyforestscan_diagnostics")
        forensic = PrerunForensics(diagnostics_root)
        try:
            forensic.set_stage("INPUT_VALIDATION", "Capturing current polygon and product selection")
            self.progress.emit("Reading repository metadata")
            with profiler.measure("polygon_preflight"):
                forensic.set_stage("RESOLVE_REPOSITORY", "Resolving LiDAR repository")
                report = run_polygon_batch_preflight(
                    self.request,
                    cancel_callback=self.cancel_callback,
                    progress_callback=lambda stage, current, total: (forensic.set_stage("PRERUN", str(stage)), self.progress.emit(f"{stage} ({current:,}/{total:,})" if total else str(stage))),
                )
            if self.cancel_callback():
                raise RuntimeError("Polygon Prerun cancelled.")
            self.progress.emit("Building processing grid")
            with profiler.measure("source_aware_planning_and_manifest_serialization"):
                manifest = write_polygon_batch_manifest(
                    report,
                    cancel_callback=self.cancel_callback,
                    progress_callback=lambda stage, current, total: self.progress.emit(f"{stage} ({current:,}/{total:,})" if total else str(stage)),
                )
            profiler.write(Path(report.batch_folder) / "prerun_profile.json", stage="READY", extra={"manifest_bytes": manifest.stat().st_size})
        except Exception as exc:  # noqa: BLE001 - worker boundary returns diagnostics to QGIS.
            forensic.failure(exc, failed_stage=forensic.stage, failed_substage=forensic.substage, context={"selected_products": [str(p.value) for p in self.request.products], "repository_selection": str(self.request.lidar_folder), "input_mode": "polygon"})
            forensic.close("CANCELLED" if "cancel" in str(exc).lower() else "FAILED")
            diagnostic = traceback.format_exc()
            try:
                folder = Path(self.request.batch_folder)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "prerun_failure.txt").write_text(diagnostic, encoding="utf-8")
                profiler.write(folder / "prerun_profile.json", stage="PRERUN_FAILED", extra={"error": str(exc)})
            except OSError:
                pass
            self.failed.emit(f"{exc}\n\nTechnical traceback saved with the Prerun artifacts when possible.")
            return
        forensic.close("COMPLETED")
        self.progress.emit("Finalizing plan")
        self.completed.emit(report)


class _CatalogBuildWorker(QObject):
    """Worker-thread wrapper for durable LiDAR catalog jobs."""

    progress = pyqtSignal(object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, spec: CatalogJobSpec, pause_callback: Callable[[], bool]) -> None:
        super().__init__()
        self.spec = spec
        self.pause_callback = pause_callback

    def run(self) -> None:
        try:
            result = CatalogJobRunner(
                self.spec,
                progress_callback=lambda progress: self.progress.emit(progress),
                pause_callback=self.pause_callback,
            ).run()
        except Exception as exc:  # noqa: BLE001 - worker must report UI-safe message.
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class _PolygonBatchExecutionWorker(QObject):
    """Qt worker that clips polygon sources and runs the normal BatchExecutor."""

    itemReady = pyqtSignal(object)
    jobReady = pyqtSignal(object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    progressUpdated = pyqtSignal(object)

    def __init__(self, report: object, control_callback: Callable[[], str | None], launch_attempt: LaunchAttempt | None = None) -> None:
        super().__init__()
        self.report = report
        self.control_callback = control_callback
        self.launch_attempt = launch_attempt

    def run(self) -> None:
        try:
            append_attempt_stage(self.launch_attempt, "WORKER_STARTED", operation="Background polygon orchestration owns the request.")
            result = execute_polygon_batch(
                self.report,
                adapter=PyForestScanAdapter(execution_mode="pbm_backend"),
                item_callback=self.itemReady.emit,
                job_callback=self.jobReady.emit,
                control_callback=self.control_callback,
                attempt_folder=None if self.launch_attempt is None else self.launch_attempt.folder,
                stage_callback=lambda stage, details: append_attempt_stage(self.launch_attempt, stage, **details),
                progress_callback=self.progressUpdated.emit,
            )
        except Exception as exc:  # noqa: BLE001 - worker must report failures to UI.
            terminal_stage = "CANCELLED" if "cancelled" in str(exc).lower() else "FAILED"
            append_attempt_stage(self.launch_attempt, terminal_stage, reason=str(exc))
            self.failed.emit(f"Unexpected polygon batch failure: {exc}")
            return
        outcome = str(getattr(result, "scientific_outcome", "FAILED"))
        if outcome not in {"SUCCEEDED", "PARTIAL_SUCCESS", "FAILED", "CANCELLED"}:
            append_attempt_stage(self.launch_attempt, "FAILED", reason=f"INTERNAL_TERMINAL_STATE_MISMATCH: unsupported scientific outcome {outcome}.")
            self.failed.emit(f"INTERNAL_TERMINAL_STATE_MISMATCH: unsupported scientific outcome {outcome}.")
            return
        append_attempt_stage(self.launch_attempt, "TERMINAL_RESULT_VALIDATED", scientific_outcome=outcome)
        append_attempt_stage(self.launch_attempt, "FINALIZING", scientific_outcome=outcome)
        terminal_stage = {"SUCCEEDED": "SCIENTIFIC_SUCCEEDED", "PARTIAL_SUCCESS": "PARTIAL_SUCCESS", "FAILED": "SCIENTIFIC_FAILED", "CANCELLED": "CANCELLED"}[outcome]
        append_attempt_stage(self.launch_attempt, terminal_stage, scientific_outcome=outcome)
        self.completed.emit(result)


class _BackendInstallWorker(QObject):
    """Qt worker that runs PBM installation away from the main QGIS UI thread."""

    progressUpdated = pyqtSignal(object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, service: BackendService) -> None:
        """Store the backend service used for the install transaction."""
        super().__init__()
        self.service = service

    def run(self) -> None:
        """Run backend installation and emit progress/result signals."""
        try:
            result = self.service.ensure_processing_engine_ready(progress_callback=self.progressUpdated.emit)
        except Exception as exc:  # noqa: BLE001 - worker must never crash QGIS UI.
            self.failed.emit(f"Unexpected backend installation failure: {exc}")
            return
        self.completed.emit(result)


class AdvancedToolboxPage(MissionPage):
    """Compact status and controls for the native QGIS Processing Toolbox."""

    def __init__(self, iface: object | None = None, parent: QWidget | None = None) -> None:
        super().__init__("Advanced Toolbox", parent)
        self.service = QgisProcessingToolboxService(iface)
        section, layout = self.create_section("Advanced Toolbox")
        layout.addWidget(_body_label("Expert PyForestScan algorithms are available through QGIS Processing."))
        self.status_label = _body_label("Checking provider status...")
        self.groups_label = _details_label("")
        layout.addWidget(self.status_label)
        layout.addWidget(self.groups_label)
        row = QHBoxLayout()
        self.open_button = QPushButton("Open Processing Toolbox")
        self.refresh_button = QPushButton("Refresh Tools")
        self.documentation_button = QPushButton("View Tool Documentation")
        self.open_button.clicked.connect(self.open_toolbox)
        self.refresh_button.clicked.connect(self.refresh_tools)
        self.documentation_button.clicked.connect(self.open_documentation)
        _apply_button_role(self.open_button, "primary")
        _apply_button_role(self.refresh_button, "secondary")
        _apply_button_role(self.documentation_button, "neutral")
        row.addWidget(self.open_button); row.addWidget(self.refresh_button); row.addWidget(self.documentation_button); row.addStretch(1)
        layout.addLayout(row)
        self.feedback_label = _details_label("")
        layout.addWidget(self.feedback_label)
        self.refresh_from_session(None)

    def refresh_from_session(self, _state: object = None) -> None:
        status = self.service.provider_status()
        availability = "Available" if status.available else "Not available"
        self.status_label.setText(f"PyForestScan Processing Provider: {availability}\nAlgorithms: {status.algorithm_count}")
        self.groups_label.setText("Groups: " + (", ".join(status.groups) if status.groups else "Input / I/O, Preprocessing / Filters, Terrain, Metrics, Diagnostics"))

    def open_toolbox(self) -> object:
        result = self.service.open_toolbox()
        self.feedback_label.setText(result.user_message)
        self.refresh_from_session()
        return result

    def open_documentation(self) -> None:
        docs = plugin_root().parent / "docs" / "development" / "ADVANCED_PROCESSING_TOOLBOX.md"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(docs)))
        self.feedback_label.setText("Opened Advanced Toolbox documentation.")

    def refresh_tools(self) -> object:
        from ..processing_provider import PyForestScanProvider

        status = self.service.refresh_provider(PyForestScanProvider)
        self.feedback_label.setText(status.message)
        self.refresh_from_session()
        return status


class BatchPage(MissionPage):
    """Folder-to-products batch workflow with standard and polygon-area modes."""

    jobUpdated = pyqtSignal(object)
    jobUpdatedForJob = pyqtSignal(object, object)
    batchCompleted = pyqtSignal(object)
    batchCompletedForJob = pyqtSignal(object, object)
    logicalJobStarted = pyqtSignal(object)
    loadCurrentOutputsRequested = pyqtSignal()
    openCurrentOutputFolderRequested = pyqtSignal()
    loadResultOutputsRequested = pyqtSignal(object)
    resultNavigationFailed = pyqtSignal(str)
    clearCurrentResultRequested = pyqtSignal()
    sessionStateChanged = pyqtSignal(object)
    processingEngineSetupRequested = pyqtSignal()

    def __init__(self, adapter: PyForestScanAdapter, iface: object | None = None, parent: QWidget | None = None) -> None:
        """Create the Batch page."""
        super().__init__("Process", parent)
        self.content_layout.setContentsMargins(0, SPACING_XS, 0, SPACING_MD)
        self.content_layout.setSizeConstraint(QLayout.SetNoConstraint)
        self.adapter = adapter
        self._job_token_factory = None
        self._current_job_token = None
        self.iface = iface
        self._adopted_polygon_selection = None
        self._engine_ready = False
        self.discovered_paths: list[Path] = []
        self.latest_result: object | None = None
        self.batch_items: list[object] = []
        self.cancel_requested = False
        self.pause_requested = False
        self.failed_paths: list[Path] = []
        self.active_workers = 0
        self.batch_thread: QThread | None = None
        self.batch_worker: _BatchExecutionWorker | None = None
        self.catalog_thread: QThread | None = None
        self.catalog_worker: _CatalogBuildWorker | None = None
        self.catalog_pause_requested = False
        self.preflight_thread: QThread | None = None
        self.preflight_worker: _PolygonPreflightWorker | None = None
        self._prerun_terminal = False
        self.preflight_cancel_event = threading.Event()
        self.preflight_report: object | None = None
        self.current_index_plan: object | None = None
        self.processing_ui_state = ProcessingUiState.IDLE
        self._last_durable_state = ""
        self._recent_error_path: Path | None = None
        self._completed_job_summary: CompletedJobSummary | None = None
        self._active_processing_profile = "Automatic (Recommended)"
        self._active_launch_attempt: LaunchAttempt | None = None
        self._last_launch_heartbeat_ms = 0
        self._last_session_state: MissionControlSessionState | None = None
        self._recent_results: tuple[ProcessingHistoryEntry, ...] = ()
        self._current_result_paths: tuple[Path, ...] = ()
        self._current_output_folder: Path | None = None
        self._current_report_path: Path | None = None
        self._processing_started_at: float | None = None
        self._processing_current_file = "Waiting for a dataset"
        self._processing_current_product = "Waiting for a product"

        self.mode_section, mode_layout = self.create_section("Processing Mode")
        self.batch_mode_combo = QComboBox()
        self.batch_mode_combo.addItem("Folder", "standard")
        self.batch_mode_combo.addItem("Polygon Area", "polygon")
        self.batch_mode_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.batch_mode_combo.setMinimumContentsLength(12)
        self.batch_mode_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.batch_mode_combo.setToolTip("Choose LiDAR Folder Selection or Polygon Selection.")
        self.batch_mode_combo.currentIndexChanged.connect(self._update_batch_mode_visibility)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(ACTION_ROW_SPACING)
        mode_row.addWidget(QLabel("Mode"))
        mode_row.addWidget(self.batch_mode_combo, 1)
        mode_layout.addLayout(mode_row)
        self.batch_mode_combo.setAccessibleName("Processing mode")
        self.batch_mode_combo.setToolTip("Choose folder processing or processing limited to a polygon.")
        self.batch_mode_summary_label = _details_label("Process LiDAR files found in a selected folder.")
        mode_layout.addWidget(self.batch_mode_summary_label)
        self.smart_status_label = _body_label("Needs attention: select LiDAR data, processing area, product, and output.")
        mode_layout.addWidget(self.smart_status_label)
        self.batch_mode_summary_label.setVisible(False)
        self.smart_status_label.setVisible(False)

        self.repository_section, repository_layout = self.create_section("LiDAR Data")
        self.standard_batch_section = self.repository_section
        folder_row = QHBoxLayout()
        self.input_folder_edit = QLineEdit()
        self.input_folder_edit.setPlaceholderText("Choose a folder containing LAS, LAZ, COPC, or EPT datasets")
        self.input_folder_edit.setProperty("contextHelp", "Choose the folder or EPT root Mission Control will inspect for supported LiDAR sources. Discovery reads source metadata but does not modify source files.")
        input_browse = QPushButton("Browse")
        input_browse.clicked.connect(self.browse_input_folder)
        folder_row.addWidget(self.input_folder_edit, 1)
        folder_row.addWidget(input_browse, 0)
        repository_layout.addLayout(folder_row)
        self.recursive_check = QCheckBox("Search subfolders")
        self.recursive_check.setProperty("contextHelp", semantic_help("process.folder.search_subfolders"))
        repository_layout.addWidget(self.recursive_check)
        self.folder_clip_bounds_check = QCheckBox("Clip processing to rectangular bounds")
        self.folder_clip_bounds_check.setToolTip(
            "Apply one XY window to every selected source. Values use the source/project coordinate system."
        )
        repository_layout.addWidget(self.folder_clip_bounds_check)
        folder_bounds_row = QHBoxLayout()
        self.folder_clip_bounds_edit = QLineEdit()
        self.folder_clip_bounds_edit.setPlaceholderText("xmin,xmax,ymin,ymax")
        self.folder_clip_bounds_edit.setEnabled(False)
        self.folder_clip_bounds_check.toggled.connect(self.folder_clip_bounds_edit.setEnabled)
        self.use_canvas_bounds_button = QPushButton("Use Current Map Extent")
        self.use_canvas_bounds_button.setEnabled(False)
        self.folder_clip_bounds_check.toggled.connect(self.use_canvas_bounds_button.setEnabled)
        self.use_canvas_bounds_button.clicked.connect(self.use_current_map_extent_for_folder_bounds)
        _apply_button_role(self.use_canvas_bounds_button, "secondary")
        folder_bounds_row.addWidget(self.folder_clip_bounds_edit, 1)
        folder_bounds_row.addWidget(self.use_canvas_bounds_button)
        repository_layout.addLayout(folder_bounds_row)
        self.spatial_assignment_frame = QFrame()
        assignment_layout = QVBoxLayout(self.spatial_assignment_frame)
        assignment_layout.setContentsMargins(0, SECTION_SPACING, 0, SECTION_SPACING)
        self.spatial_assignment_title = _body_label("Spatial reference needed")
        assignment_layout.addWidget(self.spatial_assignment_title)
        self.spatial_assignment_help = _details_label("Choose only the missing spatial information. Source coordinates are not modified.")
        assignment_layout.addWidget(self.spatial_assignment_help)
        assignment_row = QHBoxLayout()
        self.source_units_combo = QComboBox()
        self.source_units_combo.addItem("Meters", LinearUnit.METERS.value)
        self.source_units_combo.addItem("International feet", LinearUnit.INTERNATIONAL_FEET.value)
        self.source_units_combo.addItem("US survey feet", LinearUnit.US_SURVEY_FEET.value)
        self.assignment_scope_combo = QComboBox()
        self.assignment_scope_combo.addItem("This file", AssignmentScope.FILE.value)
        self.assignment_scope_combo.addItem("This repository", AssignmentScope.REPOSITORY.value)
        self.confirm_source_units_button = QPushButton("Continue")
        self.confirm_source_units_button.clicked.connect(self.assign_selected_source_units)
        _apply_button_role(self.confirm_source_units_button, "primary")
        self.choose_source_crs_button = QPushButton("Choose Coordinate System")
        self.choose_source_crs_button.clicked.connect(self.assign_selected_source_crs)
        _apply_button_role(self.choose_source_crs_button, "secondary")
        self.use_project_crs_button = QPushButton("Use Project CRS")
        self.use_project_crs_button.clicked.connect(self.assign_project_crs_to_selected_source)
        _apply_button_role(self.use_project_crs_button, "secondary")
        assignment_row.addWidget(self.source_units_combo)
        assignment_row.addWidget(self.assignment_scope_combo)
        assignment_row.addWidget(self.confirm_source_units_button)
        assignment_row.addWidget(self.choose_source_crs_button)
        assignment_row.addWidget(self.use_project_crs_button)
        assignment_row.addStretch(1)
        assignment_layout.addLayout(assignment_row)
        self.spatial_assignment_frame.setVisible(False)
        repository_layout.addWidget(self.spatial_assignment_frame)
        discover_row = QHBoxLayout()
        self.discover_button = QPushButton("Discover Files")
        self.discover_button.setProperty("contextHelp", semantic_help("process.folder.discover"))
        self.discover_button.clicked.connect(self.discover_files)
        _apply_button_role(self.discover_button, "primary")
        select_all = QPushButton("Select All")
        select_all.setProperty("contextHelp", semantic_help("process.folder.select_all"))
        select_all.clicked.connect(lambda: self._set_all_files(True))
        _apply_button_role(select_all, "neutral")
        clear_all = QPushButton("Clear")
        clear_all.setProperty("contextHelp", semantic_help("process.folder.clear"))
        clear_all.clicked.connect(lambda: self._set_all_files(False))
        _apply_button_role(clear_all, "neutral")
        discover_row.addWidget(self.discover_button)
        discover_row.addWidget(select_all)
        discover_row.addWidget(clear_all)
        discover_row.addStretch(1)
        repository_layout.addLayout(discover_row)
        self.file_list = QListWidget()
        self.file_list.setVisible(False)
        self.file_empty_label = _details_label("No files discovered. Choose a folder to begin.")
        repository_layout.addWidget(self.file_empty_label)
        repository_layout.addWidget(self.file_list)

        self.polygon_section, polygon_layout = self.create_section("Processing Area")
        self.polygon_batch_section = self.polygon_section
        polygon_form = QFormLayout()
        polygon_form.setVerticalSpacing(SECTION_SPACING)
        polygon_form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.polygon_lidar_folder_edit = QLineEdit()
        self.polygon_lidar_folder_edit.setPlaceholderText("LiDAR repository containing LAS, LAZ, COPC, or local ept.json sources")
        self.polygon_lidar_folder_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.polygon_lidar_folder_edit.setProperty("contextHelp", "Choose the LiDAR source. Repository recognition, metadata preparation, and spatial indexing are automatic when needed.")
        polygon_folder_row = QHBoxLayout()
        polygon_folder_browse = QPushButton("Browse")
        polygon_folder_browse.clicked.connect(self.browse_polygon_lidar_folder)
        self.polygon_lidar_folder_edit.editingFinished.connect(self.use_polygon_repository_path)
        polygon_folder_row.addWidget(self.polygon_lidar_folder_edit, 1)
        polygon_folder_row.addWidget(polygon_folder_browse, 0)
        self.polygon_source_combo = QComboBox()
        self.polygon_source_combo.addItem("QGIS polygon layer", "qgis")
        self.polygon_source_combo.addItem("Vector file", "file")
        self.polygon_source_combo.addItem("Technical WKT", "wkt")
        self.polygon_source_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.polygon_source_combo.setMinimumContentsLength(12)
        self.polygon_source_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.polygon_source_combo.currentIndexChanged.connect(self._update_polygon_source_visibility)
        self.polygon_source_combo.setProperty("contextHelp", "Choose a loaded QGIS polygon layer or a vector file that defines the processing area.")
        lidar_repository_row = QHBoxLayout()
        lidar_repository_row.addLayout(polygon_folder_row, 1)
        polygon_form.addRow("LiDAR Data", lidar_repository_row)
        polygon_form.addRow("Polygon source", self.polygon_source_combo)
        polygon_layout.addLayout(polygon_form)
        self.polygon_catalog_status_label = _details_label("Status: choose LiDAR data.")
        polygon_layout.addWidget(self.polygon_catalog_status_label)
        self.polygon_summary_label = _body_label(processing_area_summary())
        self.polygon_summary_label.setAccessibleName("Processing area summary")
        self.polygon_summary_label.setProperty("contextHelp", "The Processing Engine automatically aligns coordinate systems, prepares bounded inputs, subdivides large areas, and clips final outputs.")
        polygon_layout.addWidget(self.polygon_summary_label)
        strategy_form = QFormLayout()
        strategy_form.setVerticalSpacing(SECTION_SPACING)
        self.polygon_index_strategy_combo = QComboBox()
        self.polygon_index_strategy_combo.addItem("Automatic Setup (Recommended)", LidarIndexStrategy.AUTOMATIC.value)
        self.polygon_index_strategy_combo.addItem("Use an Existing Footprint Index", LidarIndexStrategy.EXISTING_SPATIAL_INDEX.value)
        self.polygon_index_strategy_combo.addItem("Use Built-in Spatial Access", LidarIndexStrategy.NATIVE_HIERARCHICAL_SOURCE.value)
        self.polygon_index_strategy_combo.addItem("Use Tile Names", LidarIndexStrategy.FILENAME_GRID.value)
        self.polygon_index_strategy_combo.addItem("Use Folder Regions", LidarIndexStrategy.PARTITIONED_LAZY.value)
        self.polygon_index_strategy_combo.addItem("Scan File Headers", LidarIndexStrategy.FULL_HEADER_CATALOG.value)
        strategy_row = QHBoxLayout()
        strategy_row.addWidget(self.polygon_index_strategy_combo, 1)
        strategy_row.addWidget(info_badge("batch.repository_setup_method", parent=self), 0)
        strategy_form.addRow("Repository setup method", strategy_row)
        existing_index_row = QHBoxLayout()
        self.polygon_existing_index_edit = QLineEdit()
        self.polygon_existing_index_edit.setPlaceholderText("Optional footprint index (registered into this repository catalog): GeoJSON, CSV, GPKG, SHP, FGB, or PDAL tile index")
        self.polygon_existing_index_button = QPushButton("Choose Index")
        self.polygon_existing_index_button.clicked.connect(self.choose_polygon_existing_index)
        _apply_button_role(self.polygon_existing_index_button, "neutral")
        existing_index_row.addWidget(self.polygon_existing_index_edit, 1)
        existing_index_row.addWidget(self.polygon_existing_index_button, 0)
        strategy_form.addRow("Existing index", existing_index_row)
        self.polygon_selection_mode_combo = QComboBox()
        self.polygon_selection_mode_combo.addItem("Automatic - Recommended", "automatic")
        self.polygon_selection_mode_combo.addItem("Direct Header Metadata", "direct_header_scan")
        self.polygon_selection_mode_combo.addItem("Verified Catalog", "verified_catalog")
        strategy_form.addRow("Selection mode", self.polygon_selection_mode_combo)
        self.polygon_direct_fallback_check = QCheckBox("Fallback to Direct Header Scan when catalog selection is inconclusive")
        # Large repositories should remain catalog-backed by default.  A full
        # header scan is an explicit recovery action, not part of prerun.
        self.polygon_direct_fallback_check.setChecked(False)
        strategy_form.addRow("", self.polygon_direct_fallback_check)
        strategy_actions = QHBoxLayout()
        strategy_actions.setSpacing(ACTION_ROW_SPACING)
        self.detect_index_strategy_button = QPushButton("Preview Setup Method")
        self.detect_index_strategy_button.clicked.connect(self.detect_polygon_index_strategy)
        _apply_button_role(self.detect_index_strategy_button, "secondary")
        self.build_relevant_index_button = QPushButton("Prepare Repository")
        self.build_relevant_index_button.clicked.connect(self.build_relevant_polygon_index)
        _apply_button_role(self.build_relevant_index_button, "primary")
        self.update_catalog_button = QPushButton("Update Index")
        self.update_catalog_button.clicked.connect(self.update_polygon_catalog)
        _apply_button_role(self.update_catalog_button, "secondary")
        strategy_actions.addWidget(self.build_relevant_index_button)
        strategy_actions.addStretch(1)
        polygon_layout.addLayout(strategy_actions)
        self.build_relevant_index_button.setVisible(False)
        self.advanced_repository_section, advanced_repository = _collapsible_section(polygon_layout, "Repository Tools", checked=False)
        advanced_repository.addWidget(_details_label("Automatic setup is recommended. Expand only to diagnose, repair, resume, or override repository preparation."))
        advanced_repository.addLayout(strategy_form)
        advanced_repository.addWidget(self.detect_index_strategy_button)
        self.polygon_index_plan_text = QTextEdit()
        self.polygon_index_plan_text.setReadOnly(True)
        self.polygon_index_plan_text.setMinimumHeight(72)
        self.polygon_index_plan_text.setMaximumHeight(140)
        self.polygon_index_plan_text.setPlainText("Preview Setup Method checks the repository lightly and recommends a preparation method without scanning every file.")
        advanced_repository.addWidget(self.polygon_index_plan_text)
        catalog_actions = QHBoxLayout()
        catalog_actions.setSpacing(ACTION_ROW_SPACING)
        self.inspect_repository_button = QPushButton("Inspect Repository")
        self.inspect_repository_button.clicked.connect(self.inspect_polygon_repository)
        _apply_button_role(self.inspect_repository_button, "secondary")
        self.build_catalog_button = QPushButton("Scan File Headers")
        self.build_catalog_button.setToolTip("Build Catalog")
        self.build_catalog_button.clicked.connect(self.build_polygon_catalog)
        _apply_button_role(self.build_catalog_button, "secondary")
        self.resume_catalog_button = QPushButton("Resume Catalog Build")
        self.resume_catalog_button.clicked.connect(self.resume_polygon_catalog)
        _apply_button_role(self.resume_catalog_button, "secondary")
        self.pause_catalog_button = QPushButton("Pause After Current Chunk")
        self.pause_catalog_button.clicked.connect(self.pause_polygon_catalog)
        self.pause_catalog_button.setEnabled(False)
        _apply_button_role(self.pause_catalog_button, "secondary")
        self.repair_catalog_button = QPushButton("Repair Catalog")
        self.repair_catalog_button.clicked.connect(self.repair_polygon_catalog)
        _apply_button_role(self.repair_catalog_button, "secondary")
        self.assign_repository_crs_button = QPushButton("Assign Coordinate System")
        self.assign_repository_crs_button.clicked.connect(self.assign_polygon_repository_crs)
        _apply_button_role(self.assign_repository_crs_button, "secondary")
        self.add_coverage_button = QPushButton("Add Coverage to Map")
        self.add_coverage_button.clicked.connect(self.add_polygon_repository_coverage)
        _apply_button_role(self.add_coverage_button, "secondary")
        self.view_sources_button = QPushButton("View Sources")
        self.view_sources_button.clicked.connect(self.view_polygon_repository_sources)
        _apply_button_role(self.view_sources_button, "neutral")
        self.export_repository_diagnostic_button = QPushButton("Export Diagnostic Report")
        self.export_repository_diagnostic_button.clicked.connect(self.export_polygon_repository_diagnostic)
        _apply_button_role(self.export_repository_diagnostic_button, "neutral")
        self.repair_ept_catalog_button = QPushButton("Repair EPT Catalog")
        self.repair_ept_catalog_button.clicked.connect(self.repair_polygon_ept_catalog)
        self.repair_ept_catalog_button.setVisible(False)
        _apply_button_role(self.repair_ept_catalog_button, "secondary")
        self.move_catalog_local_button = QPushButton("Move Catalog Local")
        self.move_catalog_local_button.clicked.connect(self.move_polygon_catalog_local)
        self.move_catalog_local_button.setVisible(False)
        _apply_button_role(self.move_catalog_local_button, "secondary")
        self.open_catalog_folder_button = QPushButton("Open Catalog Folder")
        self.open_catalog_folder_button.clicked.connect(self.open_polygon_catalog_folder)
        _apply_button_role(self.open_catalog_folder_button, "neutral")
        catalog_actions.addWidget(self.inspect_repository_button)
        catalog_actions.addWidget(self.update_catalog_button)
        catalog_actions.addWidget(self.build_catalog_button)
        catalog_actions.addWidget(self.resume_catalog_button)
        catalog_actions.addWidget(self.pause_catalog_button)
        catalog_actions.addWidget(self.repair_catalog_button)
        catalog_actions.addWidget(self.assign_repository_crs_button)
        catalog_actions.addWidget(self.add_coverage_button)
        catalog_actions.addWidget(self.view_sources_button)
        catalog_actions.addWidget(self.export_repository_diagnostic_button)
        catalog_actions.addWidget(self.repair_ept_catalog_button)
        catalog_actions.addWidget(self.move_catalog_local_button)
        catalog_actions.addWidget(self.open_catalog_folder_button)
        catalog_actions.addStretch(1)
        advanced_repository.addLayout(catalog_actions)
        _wire_collapsible_group(self.advanced_repository_section)
        self.advanced_repository_section.setVisible(False)

        self.polygon_qgis_source_frame = QFrame()
        qgis_source_layout = QVBoxLayout(self.polygon_qgis_source_frame)
        qgis_source_layout.setContentsMargins(0, 0, 0, 0)
        qgis_source_layout.setSpacing(SECTION_SPACING)
        qgis_layer_row = QGridLayout()
        self.qgis_layer_row = qgis_layer_row
        self.polygon_layer_combo = QComboBox()
        self.polygon_layer_combo.setProperty("contextHelp", semantic_help("process.polygon.layer"))
        self.polygon_layer_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.polygon_layer_combo.setMinimumContentsLength(12)
        self.polygon_layer_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.polygon_layer_combo.currentIndexChanged.connect(self._on_polygon_layer_changed)
        self.polygon_refresh_layers_button = QPushButton("Refresh")
        self.polygon_refresh_layers_button.setProperty("contextHelp", semantic_help("process.polygon.refresh"))
        self.polygon_refresh_layers_button.clicked.connect(self.refresh_polygon_layers)
        self.polygon_refresh_layers_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        _apply_button_role(self.polygon_refresh_layers_button, "neutral")
        qgis_layer_row.addWidget(self.polygon_layer_combo, 0, 0)
        polygon_area_actions = QGridLayout()
        self.polygon_area_actions = polygon_area_actions
        polygon_area_actions.setSpacing(ACTION_ROW_SPACING)
        polygon_area_actions.addWidget(self.polygon_refresh_layers_button, 0, 0)
        self.use_selected_features_button = QPushButton("Use Selected Features")
        self.use_selected_features_button.clicked.connect(self.use_selected_polygon_features)
        self.use_selected_features_button.setProperty("contextHelp", semantic_help("process.polygon.use_selection"))
        _apply_button_role(self.use_selected_features_button, "secondary")
        self.use_selected_features_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        polygon_area_actions.addWidget(self.use_selected_features_button, 0, 1)
        self.use_entire_layer_button = QPushButton("Use Entire Layer")
        self.use_entire_layer_button.clicked.connect(self.use_entire_polygon_layer)
        self.use_entire_layer_button.setProperty("contextHelp", "Adopt every usable polygon feature in the chosen layer as the processing area. Source features are not modified.")
        _apply_button_role(self.use_entire_layer_button, "secondary")
        self.use_entire_layer_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        polygon_area_actions.addWidget(self.use_entire_layer_button, 0, 2)
        self.polygon_layer_mode_combo = QComboBox()
        self.polygon_layer_mode_combo.addItem("Selected features", "selected")
        self.polygon_layer_mode_combo.addItem("Entire layer", "full")
        self.polygon_layer_mode_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.polygon_layer_mode_combo.setMinimumContentsLength(12)
        self.polygon_layer_mode_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.polygon_dissolve_check = QCheckBox("Dissolve selection")
        self.polygon_dissolve_check.setChecked(True)
        self.polygon_dissolve_check.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        qgis_layer_row.addWidget(self.polygon_layer_mode_combo, 0, 1)
        qgis_layer_row.setColumnStretch(0, 1)
        qgis_source_layout.addLayout(qgis_layer_row)
        self.polygon_dissolve_check.setVisible(False)
        self.polygon_layer_status_label = _details_label("Refresh Polygon Layers to choose a loaded polygon layer.")
        qgis_source_layout.addWidget(self.polygon_layer_status_label)
        self.polygon_layer_status_label.setVisible(False)
        polygon_layout.addWidget(self.polygon_qgis_source_frame)

        self.polygon_vector_source_frame = QFrame()
        vector_source_layout = QVBoxLayout(self.polygon_vector_source_frame)
        vector_source_layout.setContentsMargins(0, 0, 0, 0)
        vector_source_layout.setSpacing(SECTION_SPACING)
        vector_file_row = QHBoxLayout()
        self.polygon_vector_file_edit = QLineEdit()
        self.polygon_vector_file_edit.setPlaceholderText("GeoPackage, Shapefile, GeoJSON, FlatGeobuf, or KML")
        self.polygon_vector_browse_button = QPushButton("Choose Vector File")
        self.polygon_vector_browse_button.clicked.connect(self.browse_polygon_vector_file)
        _apply_button_role(self.polygon_vector_browse_button, "secondary")
        vector_file_row.addWidget(self.polygon_vector_file_edit, 1)
        vector_file_row.addWidget(self.polygon_vector_browse_button, 0)
        vector_source_layout.addLayout(vector_file_row)
        self.polygon_vector_layer_combo = QComboBox()
        self.polygon_vector_layer_combo.setVisible(False)
        vector_source_layout.addWidget(self.polygon_vector_layer_combo)
        self.polygon_vector_status_label = _details_label("Guided default: all polygon features are dissolved into one processing geometry.")
        vector_source_layout.addWidget(self.polygon_vector_status_label)
        self.polygon_vector_status_label.setVisible(False)
        polygon_layout.addWidget(self.polygon_vector_source_frame)

        self.polygon_wkt_group, polygon_wkt_layout = _collapsible_section(polygon_layout, "Advanced WKT", checked=False)
        wkt_form = QFormLayout()
        wkt_form.setVerticalSpacing(SECTION_SPACING)
        self.polygon_wkt_edit = QLineEdit()
        self.polygon_wkt_edit.setPlaceholderText("POLYGON or MULTIPOLYGON WKT")
        self.polygon_crs_edit = QLineEdit("EPSG:4326")
        self.polygon_processing_crs_edit = QLineEdit()
        self.polygon_processing_crs_edit.setPlaceholderText("Optional CRS override, for example EPSG:32610")
        wkt_form.addRow("Polygon WKT", self.polygon_wkt_edit)
        wkt_form.addRow("Source CRS", self.polygon_crs_edit)
        wkt_form.addRow("Processing CRS", self.polygon_processing_crs_edit)
        polygon_wkt_layout.addLayout(wkt_form)
        _wire_collapsible_group(self.polygon_wkt_group)
        polygon_actions = QHBoxLayout()
        polygon_actions.setSpacing(ACTION_ROW_SPACING)
        self.reset_polygon_batch_button = QPushButton("Reset Polygon Batch")
        self.reset_polygon_batch_button.clicked.connect(self.reset_polygon_batch)
        _apply_button_role(self.reset_polygon_batch_button, "danger")
        self.preview_spatial_selection_button = QPushButton("Show Selected Files on Map")
        self.preview_spatial_selection_button.clicked.connect(self.preview_polygon_spatial_selection)
        _apply_button_role(self.preview_spatial_selection_button, "secondary")
        self.preview_spatial_alignment_button = QPushButton("Preview Spatial Alignment")
        self.preview_spatial_alignment_button.clicked.connect(self.preview_polygon_spatial_alignment)
        _apply_button_role(self.preview_spatial_alignment_button, "secondary")
        self.zoom_polygon_button = QPushButton("Zoom to Polygon")
        self.zoom_polygon_button.setProperty("contextHelp", semantic_help("process.polygon.zoom"))
        self.zoom_polygon_button.clicked.connect(lambda: self._show_spatial_action("Zoom to Polygon"))
        _apply_button_role(self.zoom_polygon_button, "neutral")
        self.zoom_repository_button = QPushButton("Zoom to Repository Extent")
        self.zoom_repository_button.clicked.connect(lambda: self._show_spatial_action("Zoom to Repository Extent"))
        _apply_button_role(self.zoom_repository_button, "neutral")
        polygon_actions.addWidget(self.preview_spatial_selection_button)
        polygon_actions.addWidget(self.preview_spatial_alignment_button)
        polygon_actions.addWidget(self.zoom_polygon_button)
        polygon_actions.addWidget(self.zoom_repository_button)
        polygon_actions.addWidget(self.reset_polygon_batch_button)
        polygon_actions.addStretch(1)
        self.advanced_spatial_section, advanced_spatial = _collapsible_section(polygon_layout, "Map and Spatial Tools", checked=False)
        advanced_spatial.addWidget(_details_label("Use these map previews and recovery controls only when reviewing coverage or coordinate-system alignment."))
        advanced_spatial.addLayout(polygon_actions)
        _wire_collapsible_group(self.advanced_spatial_section)
        self.advanced_spatial_section.setVisible(False)
        self.zoom_polygon_button.setText("Zoom to Area")
        self.zoom_polygon_button.setProperty("contextHelp", semantic_help("process.polygon.zoom"))
        self.zoom_polygon_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        polygon_area_actions.addWidget(self.zoom_polygon_button, 0, 2)
        polygon_area_actions.setColumnStretch(3, 1)
        qgis_source_layout.addLayout(polygon_area_actions)

        self.output_section, output_layout = self.create_section("Output")
        self.batch_output_section = self.output_section
        output_row = QHBoxLayout()
        self.output_row = output_row
        self.output_folder_edit = QLineEdit()
        self.output_folder_edit.setPlaceholderText("Choose one output folder for the batch")
        self.output_folder_edit.setProperty("contextHelp", "Folder for this job's final scientific outputs and human-readable processing report. Internal checkpoints remain in the managed workspace.")
        output_browse = QPushButton("Browse")
        output_browse.clicked.connect(self.browse_output_folder)
        output_browse.setProperty("contextHelp", "Choose this job's output folder using the system folder picker.")
        output_row.addWidget(self.output_folder_edit, 1)
        output_row.addWidget(output_browse, 0)
        output_layout.addLayout(output_row)
        self.open_batch_folder_button = QPushButton("Open Batch Output Folder")
        self.open_batch_folder_button.setEnabled(False)
        self.open_batch_folder_button.setVisible(False)
        self.open_batch_folder_button.clicked.connect(self.open_batch_output_folder)
        output_layout.addWidget(self.open_batch_folder_button)

        self.products_section, products_layout = self.create_section("Products", index=self.content_layout.indexOf(self.output_section))
        self.product_checks: dict[ProductType, QCheckBox] = {}
        self.product_grid = QGridLayout()
        self.product_grid.setHorizontalSpacing(SPACING_XL)
        self.product_grid.setVerticalSpacing(SPACING_XS)
        for index, definition in enumerate(MISSION_CONTROL_PRODUCTS):
            product = definition.product
            check = QCheckBox(definition.short_name)
            check.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            product_help_key = f"product.{product.value}"
            try:
                product_help = semantic_help(product_help_key)
            except KeyError:
                product_help = definition.description
            check.setToolTip(product_help)
            check.setProperty("contextHelp", product_help)
            if product is ProductType.CHM:
                check.setChecked(True)
            self.product_checks[product] = check
            self.product_grid.addWidget(check, index // 2, index % 2)
        products_layout.addLayout(self.product_grid)
        product_actions = QHBoxLayout()
        self.select_recommended_products_button = QPushButton("Select Recommended")
        self.clear_products_button = QPushButton("Clear Selection")
        self.select_recommended_products_button.clicked.connect(self._select_recommended_products)
        self.clear_products_button.clicked.connect(lambda: self._set_all_products(False))
        _apply_button_role(self.select_recommended_products_button, "secondary")
        _apply_button_role(self.clear_products_button, "neutral")
        product_actions.addWidget(self.select_recommended_products_button)
        product_actions.addWidget(self.clear_products_button)
        product_actions.addStretch(1)
        products_layout.addLayout(product_actions)
        self.select_recommended_products_button.setVisible(False)
        self.clear_products_button.setVisible(False)
        self.advanced_product_settings_group, settings_layout = _collapsible_section(products_layout, "Advanced Scientific Settings", checked=False)
        # Keep the disclosure content-sized. A Minimum policy here allowed the
        # resizable page scroll area to distribute surplus height into the body.
        self.advanced_product_settings_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.resolution_spin = QDoubleSpinBox()
        self.resolution_spin.setDecimals(3)
        self.resolution_spin.setMinimum(0.01)
        self.resolution_spin.setValue(1.0)
        self.resolution_spin.setProperty("contextHelp", semantic_help("parameter.grid_resolution"))
        self.height_bin_spin = QDoubleSpinBox()
        self.height_bin_spin.setDecimals(3)
        self.height_bin_spin.setMinimum(0.0)
        self.height_bin_spin.setSpecialValueText("Not specified")
        self.height_bin_spin.setValue(1.0)
        self.canopy_threshold_spin = QDoubleSpinBox()
        self.canopy_threshold_spin.setDecimals(3)
        self.canopy_threshold_spin.setMinimum(0.0)
        self.canopy_threshold_spin.setValue(2.0)
        self.canopy_max_height_spin = _automatic_height_spin()
        self.canopy_extinction_spin = QDoubleSpinBox()
        self.canopy_extinction_spin.setRange(0.0, 100.0)
        self.canopy_extinction_spin.setDecimals(3)
        self.canopy_extinction_spin.setValue(0.5)
        self.pad_beer_lambert_spin = QDoubleSpinBox()
        self.pad_beer_lambert_spin.setRange(0.0, 100.0)
        self.pad_beer_lambert_spin.setDecimals(3)
        self.pad_beer_lambert_spin.setValue(1.0)
        self.pad_drop_ground_check = QCheckBox("Exclude ground layer")
        self.pad_drop_ground_check.setChecked(True)
        self.pai_min_height_spin = _height_spin(1.0)
        self.pai_max_height_spin = _automatic_height_spin()
        self.fhd_min_height_spin = _height_spin(0.0)
        self.fhd_max_height_spin = _automatic_height_spin()
        self.rumple_min_height_spin = _automatic_height_spin()
        self.point_density_per_area_check = QCheckBox("Density per unit area")
        self.point_density_per_area_check.setChecked(True)
        self.voxel_stat_dimension_combo = QComboBox()
        self.voxel_stat_dimension_combo.setEditable(True)
        self.voxel_stat_dimension_combo.addItems(("HeightAboveGround", "Z", "Intensity", "Classification", "ReturnNumber", "NumberOfReturns", "ScanAngleRank", "UserData", "PointSourceId", "GpsTime", "Red", "Green", "Blue", "NIR"))
        self.voxel_stat_dimension_combo.setToolTip("Point-array field to aggregate. Choose a listed LAS/COPC dimension or type any field available in the selected source.")
        self.voxel_stat_stat_combo = QComboBox()
        self.voxel_stat_stat_combo.addItems(("count", "mean", "sum", "min", "max", "median", "std"))
        self.voxel_stat_stat_combo.setToolTip("Aggregation supported by PyForestScan calculate_voxel_stat.")
        self.chm_interpolation_combo = QComboBox()
        self.chm_interpolation_combo.addItems(("linear", "nearest", "cubic"))
        self.chm_interpolation_combo.currentTextChanged.connect(self._update_chm_interpolation_help)
        semantic_controls = {
            self.height_bin_spin: "parameter.voxel_height",
            self.canopy_threshold_spin: "parameter.canopy.threshold",
            self.canopy_max_height_spin: "parameter.canopy.max_height",
            self.canopy_extinction_spin: "parameter.canopy.extinction",
            self.pad_beer_lambert_spin: "parameter.pad.beer_lambert",
            self.pad_drop_ground_check: "parameter.pad.drop_ground",
            self.pai_min_height_spin: "parameter.pai.min_height",
            self.pai_max_height_spin: "parameter.pai.max_height",
            self.fhd_min_height_spin: "parameter.fhd.min_height",
            self.fhd_max_height_spin: "parameter.fhd.max_height",
            self.rumple_min_height_spin: "parameter.rumple.min_height",
            self.point_density_per_area_check: "parameter.point_density.per_area",
            self.chm_interpolation_combo: "parameter.chm.interpolation",
        }
        for control, help_key in semantic_controls.items():
            control.setProperty("contextHelp", semantic_help(help_key))
        for control in (
            self.resolution_spin,
            self.height_bin_spin,
            self.canopy_threshold_spin,
            self.canopy_max_height_spin,
            self.canopy_extinction_spin,
            self.pad_beer_lambert_spin,
            self.pad_drop_ground_check,
            self.pai_min_height_spin,
            self.pai_max_height_spin,
            self.fhd_min_height_spin,
            self.fhd_max_height_spin,
            self.rumple_min_height_spin,
            self.point_density_per_area_check,
            self.chm_interpolation_combo,
            self.voxel_stat_dimension_combo,
            self.voxel_stat_stat_combo,
        ):
            control.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            control.setMinimumHeight(control.sizeHint().height())
        self._scientific_column_count = 1
        self.product_setting_rows = (
            ("shared.grid_resolution", "Shared Settings", "Grid resolution", self.resolution_spin),
            ("shared.height_bin_size", "Shared Settings", "Height bin size", self.height_bin_spin),
            ("chm.interpolation", "CHM", "Interpolation", self.chm_interpolation_combo),
            ("pad.beer_lambert_constant", "PAD", "Beer-Lambert coefficient", self.pad_beer_lambert_spin),
            ("pad.drop_ground", "PAD", "", self.pad_drop_ground_check),
            ("pai.min_height", "PAI", "Minimum height", self.pai_min_height_spin),
            ("pai.max_height", "PAI", "Maximum height", self.pai_max_height_spin),
            ("fhd.min_height", "FHD", "Minimum height", self.fhd_min_height_spin),
            ("fhd.max_height", "FHD", "Maximum height", self.fhd_max_height_spin),
            ("canopy_cover.min_height", "Canopy Cover", "Height threshold", self.canopy_threshold_spin),
            ("canopy_cover.max_height", "Canopy Cover", "Maximum height", self.canopy_max_height_spin),
            ("canopy_cover.k", "Canopy Cover", "Extinction coefficient", self.canopy_extinction_spin),
            ("rumple.min_height", "Rumple", "Minimum height", self.rumple_min_height_spin),
            ("point_density.per_area", "Point Density", "", self.point_density_per_area_check),
            ("voxel_stat.dimension", "Voxel Statistic", "Point dimension", self.voxel_stat_dimension_combo),
            ("voxel_stat.stat", "Voxel Statistic", "Aggregation", self.voxel_stat_stat_combo),
        )
        self.scientific_group_order = ("Shared Settings", "CHM", "DTM", "PAD", "PAI", "FHD", "Canopy Cover", "Rumple", "Point Density", "Voxel Statistic")
        self.scientific_groups: dict[str, tuple[QWidget, tuple[QFormLayout, QFormLayout], tuple[QWidget, QWidget]]] = {}
        for group_name in self.scientific_group_order:
            group_widget = QWidget()
            group_widget.setProperty("scientificParameterGroup", group_name)
            group_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            group_layout = QVBoxLayout(group_widget)
            group_layout.setContentsMargins(0, SPACING_XS, 0, SPACING_SM)
            group_layout.setSpacing(SPACING_XS)
            heading = _details_label(group_name)
            heading.setObjectName("scientificGroupHeading")
            heading.setProperty("scientificGroupHeading", True)
            heading.setAccessibleName(f"{group_name} scientific settings")
            heading.setProperty("contextHelp", scientific_group_help(group_name))
            group_layout.addWidget(heading)
            columns = QHBoxLayout()
            columns.setContentsMargins(0, 0, 0, 0)
            columns.setSpacing(SPACING_LG)
            form_widgets = (QWidget(), QWidget())
            forms = (QFormLayout(form_widgets[0]), QFormLayout(form_widgets[1]))
            for form_widget, form in zip(form_widgets, forms):
                form.setContentsMargins(0, 0, 0, 0)
                form.setVerticalSpacing(max(SPACING_SM, SECTION_SPACING))
                form.setRowWrapPolicy(QFormLayout.WrapLongRows)
                columns.addWidget(form_widget, 1)
            group_layout.addLayout(columns)
            settings_layout.addWidget(group_widget)
            self.scientific_groups[group_name] = (group_widget, forms, form_widgets)
        self.scientific_row_labels: dict[str, QLabel | None] = {}
        for key, group_name, label_text, field in self.product_setting_rows:
            field.setProperty("scientificParameterKey", key)
            field.setAccessibleName(f"{group_name} {label_text or field.text()}".strip())
            label = QLabel(label_text) if label_text else None
            if label is not None:
                label.setBuddy(field)
                label.setAccessibleName(f"{group_name} {label_text}")
            self.scientific_row_labels[key] = label
        self._rebuild_product_settings_form({ProductType.CHM})
        settings_footer = QHBoxLayout()
        self.calculation_reference_button = QPushButton("PyForestScan Calculation Guide ↗")
        self.calculation_reference_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://pyforestscan.sefa.ai/api/calculate/"))
        )
        self.calculation_reference_button.setProperty("contextHelp", semantic_help("parameter.calculation_reference"))
        self.calculation_reference_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.restore_scientific_defaults_button = QPushButton("Restore Defaults")
        self.restore_scientific_defaults_button.clicked.connect(self._restore_scientific_defaults)
        self.restore_scientific_defaults_button.setProperty("contextHelp", semantic_help("parameter.restore_defaults"))
        self.restore_scientific_defaults_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        settings_footer.addWidget(self.calculation_reference_button)
        settings_footer.addStretch(1)
        settings_footer.addWidget(self.restore_scientific_defaults_button)
        settings_layout.addLayout(settings_footer)
        _wire_collapsible_group(self.advanced_product_settings_group)
        self._update_chm_interpolation_help(self.chm_interpolation_combo.currentText())
        self.advanced_batch_section, advanced_batch = _collapsible_section(self.content_layout, "Advanced Batch Options", checked=False)
        advanced_form = QFormLayout()
        advanced_form.setVerticalSpacing(SECTION_SPACING)
        self.processing_profile_combo = QComboBox()
        for profile in PROCESSING_PROFILES:
            self.processing_profile_combo.addItem("Automatic (Recommended)" if profile.key == "recommended" else profile.label, profile.key)
        self.processing_profile_combo.setCurrentIndex(self.processing_profile_combo.findData("recommended"))
        self.processing_profile_combo.currentIndexChanged.connect(lambda _index: self._apply_processing_profile())
        profile_row = QHBoxLayout()
        profile_row.addWidget(self.processing_profile_combo, 1)
        profile_row.addWidget(info_badge("batch.processing_concurrency", parent=self), 0)
        advanced_form.addRow("Processing profile", profile_row)
        self.execution_mode_combo = QComboBox()
        self.execution_mode_combo.addItem("Automatic", "automatic")
        self.max_workers_spin = QSpinBox()
        self.max_workers_spin.setMinimum(1)
        self.max_workers_spin.setMaximum(5)
        self.max_workers_spin.setValue(5)
        self.max_workers_spin.valueChanged.connect(lambda _value: self._refresh_footprint_label())
        self.execution_mode_container = QWidget()
        execution_mode_row = QHBoxLayout(self.execution_mode_container)
        execution_mode_row.setContentsMargins(0, 0, 0, 0)
        execution_mode_row.addWidget(self.execution_mode_combo, 1)
        execution_mode_row.addWidget(info_badge("batch.processing_concurrency", parent=self), 0)
        self.max_workers_container = QWidget()
        max_workers_row = QHBoxLayout(self.max_workers_container)
        max_workers_row.setContentsMargins(0, 0, 0, 0)
        max_workers_row.addWidget(self.max_workers_spin, 1)
        max_workers_row.addWidget(info_badge("batch.concurrent_jobs", parent=self), 0)
        advanced_form.addRow("Maximum parallel workers", self.max_workers_container)
        self.advanced_batch_form = advanced_form
        self.max_workers_row = max_workers_row
        advanced_batch.addLayout(advanced_form)
        advanced_batch.addWidget(_details_label("Scheduling is automatic; Maximum parallel workers is an upper limit. External Worker is disabled."))
        self.stop_on_error_check = QCheckBox("Stop batch when a file fails")
        self.skip_completed_check = QCheckBox("Skip already-completed files on resume")
        self.skip_completed_check.setChecked(True)
        self.retry_failed_only_check = QCheckBox("Retry failed files only")
        self.overwrite_existing_check = QCheckBox("Overwrite existing outputs")
        self.stop_on_error_check.setToolTip("When off, Batch records the failed item and continues with independent items.")
        self.retry_failed_only_check.setToolTip("Retry only previously failed logical jobs when resume data is available.")
        self.overwrite_existing_check.setToolTip("When off, existing completed outputs are skipped or treated according to the safe conflict policy.")
        stop_on_error_row = QHBoxLayout()
        stop_on_error_row.addWidget(self.stop_on_error_check, 1)
        stop_on_error_row.addWidget(info_badge("batch.continue_on_error", parent=self), 0)
        advanced_batch.addLayout(stop_on_error_row)
        skip_completed_row = QHBoxLayout()
        skip_completed_row.addWidget(self.skip_completed_check, 1)
        skip_completed_row.addWidget(info_badge("batch.output_conflict_policy", parent=self), 0)
        advanced_batch.addLayout(skip_completed_row)
        retry_failed_row = QHBoxLayout()
        retry_failed_row.addWidget(self.retry_failed_only_check, 1)
        retry_failed_row.addWidget(info_badge("batch.retry_failed_jobs", parent=self), 0)
        advanced_batch.addLayout(retry_failed_row)
        overwrite_existing_row = QHBoxLayout()
        overwrite_existing_row.addWidget(self.overwrite_existing_check, 1)
        overwrite_existing_row.addWidget(info_badge("batch.output_conflict_policy", parent=self), 0)
        advanced_batch.addLayout(overwrite_existing_row)

        polygon_finalization_group, polygon_finalization = _collapsible_section(advanced_batch, "Polygon Finalization", checked=False)
        self.polygon_finalization_group = polygon_finalization_group
        polygon_form = QFormLayout()
        polygon_form.setVerticalSpacing(SECTION_SPACING)
        self.exact_raster_mask_check = QCheckBox("Exact raster mask")
        self.exact_raster_mask_check.setChecked(True)
        self.exact_raster_mask_check.toggled.connect(lambda _checked: self._refresh_footprint_label())
        exact_mask_row = QHBoxLayout()
        exact_mask_row.addWidget(self.exact_raster_mask_check, 1)
        exact_mask_row.addWidget(info_badge("batch.exact_raster_mask", parent=self), 0)
        polygon_form.addRow("Raster finalization", exact_mask_row)
        self.mask_engine_combo = QComboBox()
        self.mask_engine_combo.addItem("Automatic - Recommended", "automatic")
        self.mask_engine_combo.addItem("Managed Backend", "backend_rasterio_mask")
        self.mask_engine_combo.addItem("QGIS/GDAL", "qgis_gdal_mask")
        self.mask_engine_combo.currentIndexChanged.connect(lambda _index: self._refresh_footprint_label())
        mask_engine_row = QHBoxLayout()
        mask_engine_row.addWidget(self.mask_engine_combo, 1)
        mask_engine_row.addWidget(info_badge("batch.mask_implementation", parent=self), 0)
        polygon_form.addRow("Mask implementation", mask_engine_row)
        self.crop_to_polygon_extent_check = QCheckBox("Crop raster to polygon extent")
        self.all_touched_mask_check = QCheckBox("Include touched cells")
        self.retain_unmasked_intermediate_check = QCheckBox("Retain unmasked intermediate")
        self.crop_to_polygon_extent_check.toggled.connect(lambda _checked: self._refresh_footprint_label())
        crop_extent_row = QHBoxLayout()
        crop_extent_row.addWidget(self.crop_to_polygon_extent_check, 1)
        crop_extent_row.addWidget(info_badge("batch.crop_to_polygon_extent", parent=self), 0)
        polygon_form.addRow("", crop_extent_row)
        self.all_touched_mask_check.toggled.connect(lambda _checked: self._refresh_footprint_label())
        all_touched_row = QHBoxLayout()
        all_touched_row.addWidget(self.all_touched_mask_check, 1)
        all_touched_row.addWidget(info_badge("batch.include_touched_cells", parent=self), 0)
        polygon_form.addRow("", all_touched_row)
        self.retain_unmasked_intermediate_check.toggled.connect(lambda _checked: self._refresh_footprint_label())
        retain_intermediate_row = QHBoxLayout()
        retain_intermediate_row.addWidget(self.retain_unmasked_intermediate_check, 1)
        retain_intermediate_row.addWidget(info_badge("batch.retain_unmasked_intermediate", parent=self), 0)
        polygon_form.addRow("", retain_intermediate_row)
        self.mask_failure_policy_combo = QComboBox()
        self.mask_failure_policy_combo.addItem("Fail product if mask fails", "fail_product")
        self.mask_failure_policy_combo.addItem("Keep generated raster with warning", "warn_unmasked")
        self.mask_failure_policy_combo.currentIndexChanged.connect(lambda _index: self._refresh_footprint_label())
        mask_failure_row = QHBoxLayout()
        mask_failure_row.addWidget(self.mask_failure_policy_combo, 1)
        mask_failure_row.addWidget(info_badge("batch.mask_failure_policy", parent=self), 0)
        polygon_form.addRow("Mask failure policy", mask_failure_row)
        polygon_finalization.addLayout(polygon_form)
        polygon_finalization.addWidget(_details_label("Applies to Polygon Selection raster outputs. Final registered outputs use the masked raster when exact masking is enabled."))
        _wire_collapsible_group(polygon_finalization_group)
        _wire_collapsible_group(self.advanced_batch_section)
        self.advanced_batch_section.setVisible(False)
        self.retain_unmasked_intermediate_check.setText("Retain unmasked processing intermediate")
        self.retain_unmasked_intermediate_check.setProperty("contextHelp", "Keep the larger unmasked intermediate only for expert review or troubleshooting.")
        self.refresh_processing_status_button = QPushButton("Refresh Status")
        self.refresh_processing_status_button.clicked.connect(self.refresh_processing_status)
        self.refresh_processing_status_button.setVisible(False)
        _apply_button_role(self.refresh_processing_status_button, "neutral")
        advanced_batch.addWidget(self.refresh_processing_status_button)
        self.recent_error_group, recent_error_layout = _collapsible_section(advanced_batch, "Recent Error", checked=False)
        self.recent_error_group.setVisible(False)
        self.recent_error_label = _details_label("No retained processing error.")
        recent_error_layout.addWidget(self.recent_error_label)
        recent_error_actions = QHBoxLayout()
        self.copy_error_summary_button = QPushButton("Copy Error Summary")
        self.copy_error_summary_button.clicked.connect(self.copy_recent_error_summary)
        self.open_job_diagnostics_button = QPushButton("Open Job Diagnostics")
        self.open_job_diagnostics_button.clicked.connect(self.open_recent_error_diagnostics)
        recent_error_actions.addWidget(self.copy_error_summary_button);recent_error_actions.addWidget(self.open_job_diagnostics_button);recent_error_actions.addStretch(1)
        recent_error_layout.addLayout(recent_error_actions);_wire_collapsible_group(self.recent_error_group)
        self.resolution_spin.valueChanged.connect(lambda _value: self._refresh_footprint_label())
        self.height_bin_spin.valueChanged.connect(lambda _value: self._refresh_footprint_label())
        self.file_list.itemChanged.connect(lambda _item: self._refresh_footprint_label())
        self.file_list.itemChanged.connect(self._on_session_input_changed)

        self.footprint_label = _details_label("Batch performance details are calculated for diagnostics.")
        self.footprint_label.setVisible(False)

        self.prerun_section, prerun_layout = self.create_section("Readiness")
        self.preflight_button = QPushButton("Prerun Check")
        self.preflight_button.setMinimumHeight(PRIMARY_BUTTON_HEIGHT)
        self.preflight_button.clicked.connect(self.run_preflight)
        self.preflight_button.setProperty("contextHelp", "Check the Processing Engine, LiDAR source, polygon, coordinate systems, products, storage, and estimated workload before dispatch.")
        _apply_button_role(self.preflight_button, "primary")
        prerun_layout.addWidget(self.preflight_button)
        self.cancel_preflight_button = QPushButton("Cancel Prerun")
        self.cancel_preflight_button.setVisible(False)
        self.cancel_preflight_button.clicked.connect(self.cancel_polygon_preflight)
        _apply_button_role(self.cancel_preflight_button, "secondary")
        prerun_layout.addWidget(self.cancel_preflight_button)
        self.preflight_summary_label = _body_label("Needs attention: choose data, products, and an output folder.")
        prerun_layout.addWidget(self.preflight_summary_label)
        self.next_action_label = _details_label("Next action: check the Processing Engine.")
        self.next_action_label.setProperty("workflowGuidance", True)
        prerun_layout.addWidget(self.next_action_label)
        self.preflight_text = QTextEdit()
        self.preflight_text.setObjectName("compactTechnicalReport")
        self.preflight_text.setReadOnly(True)
        self.preflight_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.preflight_text.setPlainText("Run the Prerun Check when your data, products, and output folder are ready.")
        self.preflight_text.textChanged.connect(lambda: _size_text_edit_to_content(self.preflight_text))
        self.preflight_details_group, preflight_details = _collapsible_section(prerun_layout, "Details", checked=False)
        preflight_details.addWidget(self.preflight_text)
        _wire_collapsible_group(self.preflight_details_group)
        _size_text_edit_to_content(self.preflight_text)

        self.process_section, process_layout = self.create_section("Process")
        self.run_button = QPushButton(primary_action_label("batch"))
        self.run_button.setMinimumHeight(PRIMARY_BUTTON_HEIGHT)
        self.run_button.clicked.connect(self.run_batch)
        self.run_button.setProperty("contextHelp", "Validate the current plan and start processing with automatic preparation, scheduling, checkpointing, and final clipping.")
        _apply_button_role(self.run_button, "primary")
        self.run_button.setEnabled(False)
        button_row = QHBoxLayout()
        self.process_button_row = button_row
        button_row.addWidget(self.run_button)
        self.engine_setup_button = QPushButton("Set Up Processing Engine")
        self.engine_setup_button.clicked.connect(self.processingEngineSetupRequested.emit)
        _apply_button_role(self.engine_setup_button, "primary")
        self.engine_setup_button.setVisible(False)
        button_row.addWidget(self.engine_setup_button)
        self.resume_button = QPushButton("Resume Batch")
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self.run_batch)
        _apply_button_role(self.resume_button, "secondary")
        button_row.addWidget(self.resume_button)
        self.pause_button = QPushButton("Pause After Current Step")
        self.pause_button.setEnabled(False)
        self.pause_button.setVisible(False)
        self.pause_button.clicked.connect(self.toggle_pause)
        _apply_button_role(self.pause_button, "secondary")
        self.cancel_button = QPushButton("Cancel Processing")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_remaining)
        _apply_button_role(self.cancel_button, "danger")
        self.retry_failed_button = QPushButton("Retry Failed Files")
        self.retry_failed_button.setEnabled(False)
        self.retry_failed_button.setVisible(False)
        self.retry_failed_button.clicked.connect(self.retry_failed_files)
        _apply_button_role(self.retry_failed_button, "secondary")
        button_row.addWidget(self.pause_button)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.retry_failed_button)
        button_row.addStretch(1)
        process_layout.addLayout(button_row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p% complete")
        process_layout.addWidget(self.progress_bar)
        self.processing_detail_label = _body_label(
            "Elapsed 00:00  |  Step: Waiting  |  File: none  |  Product: none"
        )
        self.processing_detail_label.setWordWrap(True)
        process_layout.addWidget(self.processing_detail_label)
        self.status_label = QLabel()
        _set_status_badge(self.status_label, "NOT CONFIGURED", "Status: Needs Attention - choose data and run the Prerun Check.")
        self.status_label.setProperty("compactProcessingStatus", True)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        process_layout.addWidget(self.status_label)
        self.engine_status_label = _body_label("Processing Engine: Checking")
        process_layout.addWidget(self.engine_status_label)
        self.worker_status_label = _body_label("Processing capacity: Automatic")
        self.worker_status_label.setWordWrap(False)
        self.worker_status_label.setMinimumHeight(28)
        self.worker_status_label.setMaximumHeight(28)
        process_layout.addWidget(self.worker_status_label)
        self.processing_confidence_label = _details_label("Completed regions are saved as processing continues. Valid completed regions can be resumed after an interruption.")
        self.processing_confidence_label.setVisible(False)
        process_layout.addWidget(self.processing_confidence_label)
        filter_row = QHBoxLayout()
        self.result_filter_label = QLabel("Show")
        filter_row.addWidget(self.result_filter_label)
        self.result_filter_combo = QComboBox()
        self.result_filter_combo.addItems(("All", "Failed", "Completed", "Skipped"))
        self.result_filter_combo.currentTextChanged.connect(lambda _value: self._refresh_batch_results())
        filter_row.addWidget(self.result_filter_combo)
        filter_row.addStretch(1)
        process_layout.addLayout(filter_row)
        self.summary_label = _body_label("4. Review Results after the batch completes.")
        process_layout.addWidget(self.summary_label)
        self.batch_results = QListWidget()
        self.batch_results.setMaximumHeight(180)
        self.result_filter_label.setVisible(False)
        self.result_filter_combo.setVisible(False)
        self.batch_results.setVisible(False)
        process_layout.addWidget(self.batch_results)
        self.current_result_section, current_result_layout = self.create_section("Recent Results")
        self.current_result_label = _body_label("No current result. Configure the workflow and select Process LiDAR.")
        current_result_layout.addWidget(self.current_result_label)
        current_result_buttons = QHBoxLayout()
        self.load_current_result_button = QPushButton("Load into QGIS")
        self.load_current_result_button.clicked.connect(self.load_current_result)
        self.load_current_result_button.setProperty("contextHelp", "Load the newest job's registered raster and table outputs into the current QGIS project.")
        self.load_current_result_button.setAccessibleName("Load newest job outputs into QGIS")
        _apply_button_role(self.load_current_result_button,"primary")
        self.open_current_result_button = QPushButton("Open Outputs")
        self.open_current_result_button.clicked.connect(self.open_current_outputs)
        self.open_current_result_button.setProperty("contextHelp", "Open the folder containing the newest job's final scientific outputs.")
        self.open_current_result_button.setAccessibleName("Open newest job output folder")
        _apply_button_role(self.open_current_result_button,"secondary")
        self.open_current_report_button = QPushButton("Open Report")
        self.open_current_report_button.clicked.connect(self.open_current_report)
        self.open_current_report_button.setProperty("contextHelp", "Open the newest job's human-readable processing report.")
        self.open_current_report_button.setAccessibleName("Open newest job processing report")
        _apply_button_role(self.open_current_report_button,"secondary")
        self.clear_current_result_button = QPushButton("New Run")
        self.clear_current_result_button.clicked.connect(self.clearCurrentResultRequested.emit)
        self.clear_current_result_button.setProperty("contextHelp", "Reset the current workflow inputs while preserving Recent Results.")
        self.clear_current_result_button.setAccessibleName("Start a new run and preserve result history")
        _apply_button_role(self.clear_current_result_button,"neutral")
        for button in (self.load_current_result_button,self.open_current_result_button,self.open_current_report_button,self.clear_current_result_button):current_result_buttons.addWidget(button)
        current_result_buttons.addStretch(1);current_result_layout.addLayout(current_result_buttons)
        self.set_current_result(())
        self.previous_runs_group,self.previous_runs_layout=_collapsible_section(self.content_layout,"Earlier Results",checked=False)
        self.previous_runs_list=QListWidget();self.previous_runs_list.setMaximumHeight(120);self.previous_runs_layout.addWidget(self.previous_runs_list)
        self.previous_runs_list.setProperty("contextHelp", "Select an earlier registered processing result. Selecting it does not change the current workflow or job.")
        self.previous_runs_list.setAccessibleName("Earlier processing results")
        self.previous_runs_list.currentRowChanged.connect(self._update_historical_result_actions)
        previous_actions = QHBoxLayout()
        self.load_historical_result_button = QPushButton("Load Successful Outputs")
        self.load_historical_result_button.clicked.connect(self.load_selected_historical_result)
        self.load_historical_result_button.setProperty("contextHelp", "Load the selected historical job's successful outputs without changing the current job.")
        self.open_historical_outputs_button = QPushButton("Open Outputs")
        self.open_historical_outputs_button.clicked.connect(self.open_selected_historical_outputs)
        self.open_historical_outputs_button.setProperty("contextHelp", "Open the selected historical job's final output folder.")
        self.open_historical_report_button = QPushButton("Open Report")
        self.open_historical_report_button.clicked.connect(self.open_selected_historical_report)
        self.open_historical_report_button.setProperty("contextHelp", "Open the selected historical job's processing or error report.")
        for button in (self.load_historical_result_button, self.open_historical_outputs_button, self.open_historical_report_button):
            _apply_button_role(button, "secondary")
            previous_actions.addWidget(button)
        previous_actions.addStretch(1)
        self.previous_runs_layout.addLayout(previous_actions)
        self.previous_runs_group.setVisible(False);_wire_collapsible_group(self.previous_runs_group)
        self._process_column_mode = ""
        self._product_column_count = 0
        self._install_process_workspace()
        self._update_processing_density()
        self._update_batch_mode_visibility()
        self._wire_session_state_inputs()
        self._refresh_batch_option_visibility()
        QTimer.singleShot(0, self._publish_session_state)
        self._processing_watchdog = QTimer(self)
        self._processing_watchdog.setInterval(2500)
        self._processing_watchdog.timeout.connect(self._reconcile_processing_ui)
        self._processing_watchdog.start()
        self._processing_elapsed_timer = QTimer(self)
        self._processing_elapsed_timer.setInterval(1000)
        self._processing_elapsed_timer.timeout.connect(self._refresh_processing_elapsed)

    def _install_process_workspace(self) -> None:
        """Compose one top-to-bottom workflow with responsive section internals."""
        self.process_workspace = QWidget(self.content_widget)
        self.process_workspace.setObjectName("responsiveProcessWorkspace")
        self.process_workspace.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.process_workspace_layout = QVBoxLayout(self.process_workspace)
        self.process_workspace_layout.setSizeConstraint(QLayout.SetNoConstraint)
        self.process_workspace_layout.setContentsMargins(0, 0, 0, 0)
        self.process_workspace_layout.setSpacing(SECTION_GAP)
        self._routine_process_sections = (
            self.mode_section, self.repository_section, self.polygon_section,
            self.products_section, self.output_section, self.prerun_section, self.process_section,
        )
        for section in self._routine_process_sections:
            _take_layout_widget(self.content_layout, section)
            section.setParent(self.process_workspace)
            section.setProperty("processSection", True)
            section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            section.style().unpolish(section)
            section.style().polish(section)
        compact_headings = (
            (self.repository_section, "LiDAR Data"),
            (self.polygon_section, "Processing Area"),
            (self.products_section, "Products"),
            (self.process_section, "Processing"),
        )
        for section, text in compact_headings:
            section.setTitle("")
            heading = QLabel(text)
            heading.setObjectName("compactSectionHeading")
            section.layout().insertWidget(0, heading)
        self.mode_section.setTitle("")
        self.prerun_section.setTitle("")
        self.output_section.setTitle("")
        output_heading = QLabel("Output")
        output_heading.setObjectName("compactSectionHeading")
        self.output_row.insertWidget(0, output_heading, 0)
        _take_layout_widget(self.prerun_section.layout(), self.preflight_button)
        _take_layout_widget(self.process_button_row, self.run_button)
        self.workflow_action_row = QGridLayout()
        self.workflow_action_row.setHorizontalSpacing(ACTION_ROW_SPACING)
        self.workflow_action_row.setVerticalSpacing(ROW_GAP)
        self.preflight_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.run_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        equal_height = max(self.preflight_button.sizeHint().height(), self.run_button.sizeHint().height())
        self.preflight_button.setFixedHeight(equal_height)
        self.run_button.setFixedHeight(equal_height)
        self.prerun_section.layout().insertLayout(0, self.workflow_action_row)
        self.content_layout.insertWidget(0, self.process_workspace)
        for section in self._routine_process_sections:
            self.process_workspace_layout.addWidget(section)
        self._apply_process_layout(760)

    def _apply_process_layout(self, width: int) -> None:
        """Adapt Products and Advanced without changing workflow reading order."""
        columns = 4 if width >= 720 else 2
        mode = "wide" if columns == 4 else "narrow"
        _take_layout_widget(self.workflow_action_row, self.preflight_button)
        _take_layout_widget(self.workflow_action_row, self.run_button)
        if width < 420:
            self.workflow_action_row.addWidget(self.preflight_button, 0, 0)
            self.workflow_action_row.addWidget(self.run_button, 1, 0)
        else:
            self.workflow_action_row.addWidget(self.preflight_button, 0, 0)
            self.workflow_action_row.addWidget(self.run_button, 0, 1)
        self.workflow_action_row.setColumnStretch(0, 1)
        self.workflow_action_row.setColumnStretch(1, 1 if width >= 420 else 0)
        _take_layout_widget(self.qgis_layer_row, self.polygon_layer_combo)
        _take_layout_widget(self.qgis_layer_row, self.polygon_layer_mode_combo)
        if width < 480:
            self.qgis_layer_row.addWidget(self.polygon_layer_combo, 0, 0)
            self.qgis_layer_row.addWidget(self.polygon_layer_mode_combo, 1, 0)
        else:
            self.qgis_layer_row.addWidget(self.polygon_layer_combo, 0, 0)
            self.qgis_layer_row.addWidget(self.polygon_layer_mode_combo, 0, 1)
        self.qgis_layer_row.setColumnStretch(0, 1)
        for button in (
            self.polygon_refresh_layers_button,
            self.use_selected_features_button,
            self.use_entire_layer_button,
            self.zoom_polygon_button,
        ):
            _take_layout_widget(self.polygon_area_actions, button)
        if width < 480:
            self.polygon_area_actions.addWidget(self.polygon_refresh_layers_button, 0, 0)
            self.polygon_area_actions.addWidget(self.zoom_polygon_button, 0, 1)
            self.polygon_area_actions.addWidget(self.use_selected_features_button, 1, 0, 1, 2)
            self.polygon_area_actions.addWidget(self.use_entire_layer_button, 2, 0, 1, 2)
            self.polygon_area_actions.setColumnStretch(2, 1)
        else:
            self.polygon_area_actions.addWidget(self.polygon_refresh_layers_button, 0, 0)
            self.polygon_area_actions.addWidget(self.use_selected_features_button, 0, 1)
            self.polygon_area_actions.addWidget(self.use_entire_layer_button, 0, 2)
            self.polygon_area_actions.addWidget(self.zoom_polygon_button, 0, 3)
            self.polygon_area_actions.setColumnStretch(4, 1)
        if columns != self._product_column_count:
            self._product_column_count = columns
            for check in self.product_checks.values():
                _take_layout_widget(self.product_grid, check)
            for index, definition in enumerate(MISSION_CONTROL_PRODUCTS):
                self.product_grid.addWidget(self.product_checks[definition.product], index // columns, index % columns)
        column_count = scientific_form_column_count(width)
        for _group_widget, forms, _form_widgets in self.scientific_groups.values():
            for form in forms:
                form.setRowWrapPolicy(QFormLayout.DontWrapRows if width >= 720 else QFormLayout.WrapAllRows)
                form.setVerticalSpacing(SPACING_SM if column_count == 2 else SPACING_MD)
        if column_count != self._scientific_column_count:
            self._scientific_column_count = column_count
            selected = {product for product, check in self.product_checks.items() if check.isChecked()}
            self._rebuild_product_settings_form(selected)
        self._process_column_mode = mode

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        """Adapt routine workflow columns without rebuilding any controls."""
        super().resizeEvent(event)
        if hasattr(self, "process_workspace"):
            self._apply_process_layout(self.scroll_area.viewport().width())

    def showEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        """Refresh responsive controls after a previously hidden page is shown."""
        super().showEvent(event)
        if hasattr(self, "process_workspace"):
            QTimer.singleShot(0, lambda: self._apply_process_layout(self.scroll_area.viewport().width()))

    def set_job_token_factory(self,factory) -> None:
        self._job_token_factory=factory

    def set_smart_status(self, headline: str, detail: str = "") -> None:
        """Project the shared workflow summary without exposing child widgets."""
        self.smart_status_label.setText(headline + (f" - {detail}" if detail else ""))

    def set_processing_engine_state(self, engine: object) -> None:
        """Show one compact setup action without discarding current selections."""
        ready = bool(getattr(engine, "ready_for_processing", False))
        repair = bool(getattr(engine, "repair_needed", False))
        status = str(getattr(getattr(engine, "status", None), "value", "SETUP_REQUIRED"))
        action_visible, action_label = processing_engine_setup_action(status)
        self._engine_ready = ready
        self.engine_setup_button.setText(action_label)
        self.engine_setup_button.setProperty("contextHelp", semantic_action_help(action_label))
        self.engine_setup_button.setVisible(not ready)
        self.engine_status_label.setText("Processing Engine: Ready" if ready else ("Processing Engine: Needs repair" if repair else "Processing Engine: Setup required"))
        if not ready:
            self.run_button.setEnabled(False)
            _set_status_badge(self.status_label, getattr(getattr(engine, "status", None), "value", "SETUP_REQUIRED"), getattr(engine, "message", "Processing Engine setup required."))
        elif hasattr(self, "preflight_report"):
            had_preflight = self.preflight_report is not None
            self.preflight_report = None
            self.preflight_summary_label.setText("Processing plan: Refreshing for the current Processing Engine..." if had_preflight else "Processing plan: Needs Prerun.")
            _set_status_badge(self.status_label, "READY", "Processing Engine: Ready. Processing plan: Refreshing..." if had_preflight else "Processing Engine: Ready. Processing plan needs Prerun.")
            self._update_run_button_enabled()
            if had_preflight:
                QTimer.singleShot(0, self.run_preflight)

    def _begin_logical_job(self):
        if self._job_token_factory is None:return None
        try:token=self._job_token_factory()
        except RuntimeError as exc:
            _set_status_badge(self.status_label,"WARNING",f"Status: Needs attention - {exc}");return False
        self._current_job_token=token;self.logicalJobStarted.emit(token);self.set_current_result(());return token

    def set_previous_runs(self,records) -> None:
        """Retained compatibility hook; durable Recent Results owns presentation."""

    def set_current_result(self,paths,output_folder=None,report_path=None) -> None:
        paths=tuple(Path(path) for path in paths);has_result=bool(paths or output_folder or report_path)
        self._current_result_paths = paths
        self._current_output_folder = Path(output_folder) if output_folder else None
        self._current_report_path = Path(report_path) if report_path else None
        self.current_result_section.setVisible(has_result)
        self.current_result_label.setText(_compact_output_summary(paths) if paths else ("No successful outputs. Open the report for details." if has_result else "No recent results."))
        self.load_current_result_button.setEnabled(bool(paths))
        self.open_current_result_button.setEnabled(bool(self._current_output_folder))
        self.open_current_report_button.setEnabled(bool(self._current_report_path))
        self.clear_current_result_button.setEnabled(has_result)
        if not has_result:
            self.open_current_report_button.setText("Open Report")

    def set_recent_results(self, entries: tuple[ProcessingHistoryEntry, ...]) -> None:
        """Project bounded registry history without changing active workflow state."""
        self._recent_results = tuple(entries[:15])
        self.previous_runs_list.clear()
        if self._recent_results:
            newest = self._recent_results[0]
            report_path = newest.error_report_path or newest.report_path
            self.set_current_result(newest.outputs, newest.output_folder, report_path)
            self.open_current_report_button.setText("View Error" if newest.error_report_path else "Open Report")
        else:
            self.set_current_result(())
        for entry in self._recent_results[1:]:
            self.previous_runs_list.addItem(format_recent_result(entry))
        self.previous_runs_group.setVisible(len(self._recent_results) > 1)
        if self.previous_runs_list.count():
            self.previous_runs_list.setCurrentRow(0)
        self._update_historical_result_actions()

    def _selected_historical_result(self) -> ProcessingHistoryEntry | None:
        row = self.previous_runs_list.currentRow()
        return self._recent_results[row + 1] if 0 <= row < len(self._recent_results) - 1 else None

    def _update_historical_result_actions(self, _row: int = -1) -> None:
        entry = self._selected_historical_result()
        self.load_historical_result_button.setEnabled(bool(entry and entry.outputs))
        self.open_historical_outputs_button.setEnabled(bool(entry and entry.output_folder))
        self.open_historical_report_button.setEnabled(bool(entry and (entry.error_report_path or entry.report_path)))
        self.open_historical_report_button.setText("View Error" if entry and entry.error_report_path else "Open Report")

    def load_selected_historical_result(self) -> None:
        entry = self._selected_historical_result()
        if entry is not None:
            self.loadResultOutputsRequested.emit(tuple(Path(path) for path in entry.outputs))

    def load_current_result(self) -> None:
        if self._current_result_paths:
            self.loadResultOutputsRequested.emit(self._current_result_paths)

    def open_current_outputs(self) -> None:
        self._open_result_path(self._current_output_folder, "output folder")

    def open_current_report(self) -> None:
        self._open_result_path(self._current_report_path, "processing report")

    def open_selected_historical_outputs(self) -> None:
        entry = self._selected_historical_result()
        self._open_result_path(Path(entry.output_folder) if entry and entry.output_folder else None, "output folder")

    def open_selected_historical_report(self) -> None:
        entry = self._selected_historical_result()
        value = entry.error_report_path or entry.report_path if entry else ""
        self._open_result_path(Path(value) if value else None, "processing report")

    def _open_result_path(self, path: Path | None, label: str) -> bool:
        if path is None or not path.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            shown = str(path) if path is not None else "Unavailable"
            message = f"Could not open the {label}. Path: {shown}"
            self.resultNavigationFailed.emit(message)
            return False
        return True

    def _select_recommended_products(self) -> None:
        """Apply the concise guided default without requiring Advisor."""
        self._set_all_products(False)
        if ProductType.CHM in self.product_checks:
            self.product_checks[ProductType.CHM].setChecked(True)

    def _restore_scientific_defaults(self) -> None:
        """Restore official release-supported defaults without changing workflow inputs."""
        self.resolution_spin.setValue(1.0)
        self.height_bin_spin.setValue(1.0)
        self.canopy_threshold_spin.setValue(2.0)
        self.canopy_max_height_spin.setValue(0.0)
        self.canopy_extinction_spin.setValue(0.5)
        self.pad_beer_lambert_spin.setValue(1.0)
        self.pad_drop_ground_check.setChecked(True)
        self.pai_min_height_spin.setValue(1.0)
        self.pai_max_height_spin.setValue(0.0)
        self.fhd_min_height_spin.setValue(0.0)
        self.fhd_max_height_spin.setValue(0.0)
        self.rumple_min_height_spin.setValue(0.0)
        self.point_density_per_area_check.setChecked(True)
        self.chm_interpolation_combo.setCurrentText("linear")
        self._on_product_selection_changed()

    def _update_chm_interpolation_help(self, method: str) -> None:
        """Explain the selected interpolation method rather than its widget label."""
        descriptions = {
            "nearest": "Fills gaps using the nearest valid canopy-height cell. This preserves observed values but can create blockier transitions.",
            "linear": "Estimates gaps from surrounding valid cells, producing smoother transitions while remaining less aggressive than cubic interpolation.",
            "cubic": "Uses a smoother higher-order interpolation that may produce smoother surfaces but can be more sensitive around sparse data.",
            "none": "Leaves cells without valid canopy-height observations as gaps instead of estimating values.",
        }
        text = descriptions.get(str(method).casefold(), semantic_help("parameter.chm.interpolation"))
        self.chm_interpolation_combo.setProperty("contextHelp", text)
        self.chm_interpolation_combo.setProperty("resolvedContextHelp", text)

    def _set_all_products(self, checked: bool) -> None:
        for product_check in self.product_checks.values():
            product_check.setChecked(checked)

    def _wire_session_state_inputs(self) -> None:
        """Publish authoritative snapshots when retained Batch inputs change."""
        for combo in (self.batch_mode_combo, self.polygon_source_combo, self.polygon_layer_combo,
                      self.polygon_layer_mode_combo, self.polygon_vector_layer_combo):
            combo.currentIndexChanged.connect(self._on_session_input_changed)
        for edit in (self.input_folder_edit, self.folder_clip_bounds_edit, self.polygon_lidar_folder_edit,
                     self.polygon_vector_file_edit, self.polygon_wkt_edit,
                     self.polygon_crs_edit, self.polygon_processing_crs_edit,
                     self.output_folder_edit):
            edit.textChanged.connect(self._on_session_input_changed)
        self.polygon_dissolve_check.toggled.connect(self._on_session_input_changed)
        self.recursive_check.toggled.connect(self._on_session_input_changed)
        self.folder_clip_bounds_check.toggled.connect(self._on_session_input_changed)
        self.polygon_direct_fallback_check.toggled.connect(self._on_product_selection_changed)
        scientific_values = (
            self.resolution_spin, self.height_bin_spin, self.canopy_threshold_spin, self.max_workers_spin,
            self.canopy_max_height_spin, self.canopy_extinction_spin, self.pad_beer_lambert_spin,
            self.pai_min_height_spin, self.pai_max_height_spin, self.fhd_min_height_spin,
            self.fhd_max_height_spin, self.rumple_min_height_spin,
        )
        for control in scientific_values:
            control.valueChanged.connect(self._on_product_selection_changed)
        for combo in (self.processing_profile_combo, self.execution_mode_combo, self.chm_interpolation_combo,
                      self.polygon_index_strategy_combo, self.polygon_selection_mode_combo, self.mask_engine_combo,
                      self.mask_failure_policy_combo, self.voxel_stat_dimension_combo, self.voxel_stat_stat_combo):
            combo.currentIndexChanged.connect(self._on_product_selection_changed)
        for option in (self.stop_on_error_check,
                       self.skip_completed_check, self.retry_failed_only_check, self.overwrite_existing_check,
                       self.exact_raster_mask_check, self.crop_to_polygon_extent_check,
                       self.all_touched_mask_check, self.retain_unmasked_intermediate_check):
            option.toggled.connect(self._on_product_selection_changed)
        self.pad_drop_ground_check.toggled.connect(self._on_product_selection_changed)
        self.point_density_per_area_check.toggled.connect(self._on_product_selection_changed)
        for check in self.product_checks.values():
            check.toggled.connect(self._on_product_selection_changed)
        self.polygon_lidar_folder_edit.textChanged.connect(self._update_adaptive_visibility)

    def _on_session_input_changed(self, *_args: object) -> None:
        self.preflight_report = None
        self._refresh_batch_option_visibility()
        if hasattr(self,"run_button"):self._update_run_button_enabled()
        if hasattr(self, "preflight_text"):
            self.preflight_text.setPlainText("Prerun Check needs refresh for the current inputs.")
        if hasattr(self, "preflight_summary_label"):
            self.preflight_summary_label.setText("Needs attention: Prerun Check must be refreshed.")
        self._publish_session_state()

    def _on_product_selection_changed(self, *_args: object) -> None:
        """Invalidate the plan without inspecting source, polygon, or backend state."""
        self.preflight_report = None
        self._refresh_batch_option_visibility()
        self._update_run_button_enabled()
        self.preflight_text.setPlainText("Prerun Check needs refresh for the selected products.")
        self.preflight_summary_label.setText("Needs attention: Prerun Check must be refreshed.")
        selected = tuple(PRODUCT_LABELS[p] for p, check in self.product_checks.items() if check.isChecked())
        if self._last_session_state is not None:
            state = replace(self._last_session_state, selected_products=selected, plan_status="needs refresh")
            self._last_session_state = state
            self.sessionStateChanged.emit(state)

    def _publish_session_state(self, *, plan_status: str = "needs refresh") -> None:
        mode = self._current_batch_mode()
        repository = (self.polygon_lidar_folder_edit.text().strip() if mode == "polygon"
                      else self.input_folder_edit.text().strip())
        source = ""
        feature_count = 0
        geometry_signature = ""
        area = None
        crs = ""
        polygon = None
        if mode == "polygon":
            source = str(self.polygon_source_combo.currentData() or "")
            polygon = self._adopted_polygon_selection
            if polygon is not None:
                feature_count = int(polygon.feature_count)
                geometry_signature = __import__("hashlib").sha256(polygon.geometry_wkt.encode()).hexdigest()
                hectares = getattr(polygon, "area_hectares", None)
                area = float(hectares) * 10000.0 if hectares is not None else None
                crs = polygon.processing_crs or polygon.source_crs
        if hasattr(self, "polygon_summary_label"):
            hectares = getattr(polygon, "area_hectares", None) if polygon is not None else None
            self.polygon_summary_label.setText(processing_area_summary(
                feature_count=feature_count, area_hectares=hectares, crs=crs,
                adopted=polygon is not None,
            ))
        products = tuple(PRODUCT_LABELS[p] for p, check in self.product_checks.items() if check.isChecked())
        signature = workflow_input_signature({
            "mode": mode, "repository": repository, "polygon_source": source,
            "polygon_geometry": geometry_signature, "polygon_crs": crs,
            "products": products, "output": self.output_folder_edit.text().strip(),
            "resolution": self.resolution_spin.value(), "height_bin": self.height_bin_spin.value(),
            "canopy_threshold": self.canopy_threshold_spin.value(),
            "chm_interpolation": self.chm_interpolation_combo.currentData() or self.chm_interpolation_combo.currentText(),
            "recursive": self.recursive_check.isChecked(), "profile": self.processing_profile_combo.currentData(),
            "folder_clip_enabled": self.folder_clip_bounds_check.isChecked(),
            "folder_clip_bounds": self.folder_clip_bounds_edit.text().strip(),
            "execution_mode": self.execution_mode_combo.currentData(), "max_workers": self.max_workers_spin.value(),
            "stop_on_error": self.stop_on_error_check.isChecked(), "load_outputs": True,
            "parallel_confirmed": True,
            "skip_completed": self.skip_completed_check.isChecked(), "retry_failed": self.retry_failed_only_check.isChecked(),
            "overwrite": self.overwrite_existing_check.isChecked(),
            "repository_strategy": self.polygon_index_strategy_combo.currentData(),
            "selection_mode": self.polygon_selection_mode_combo.currentData(),
            "direct_fallback": self.polygon_direct_fallback_check.isChecked(),
            "exact_mask": self.exact_raster_mask_check.isChecked(), "mask_engine": self.mask_engine_combo.currentData(),
            "crop": self.crop_to_polygon_extent_check.isChecked(), "all_touched": self.all_touched_mask_check.isChecked(),
            "retain_intermediate": self.retain_unmasked_intermediate_check.isChecked(),
            "mask_failure_policy": self.mask_failure_policy_combo.currentData(),
        })
        state = MissionControlSessionState(
            input_signature=signature,
            current_mode=mode, repository_path=repository,
            repository_kind=("EPT dataset" if repository.lower().endswith("ept.json") else "LiDAR repository"),
            repository_status=("selected" if repository else "not configured"),
            selected_polygon_source=source, selected_polygon_feature_count=feature_count,
            polygon_geometry_signature=geometry_signature, polygon_area=area, polygon_crs=crs,
            selected_products=products, output_resolution=self.resolution_spin.value(),
            output_folder=self.output_folder_edit.text().strip(), plan_status=plan_status)
        self._last_session_state = state
        self.sessionStateChanged.emit(state)

    def _current_batch_mode(self) -> str:
        return str(self.batch_mode_combo.currentData() or "standard")

    def _update_batch_mode_visibility(self, *_args: object) -> None:
        mode = self._current_batch_mode()
        polygon = mode == "polygon"
        self.standard_batch_section.setVisible(not polygon)
        self.polygon_batch_section.setVisible(polygon)
        self.batch_mode_summary_label.setText(
            "Process LiDAR covering a selected polygon."
            if polygon else
            "Process LiDAR files found in a selected folder."
        )
        self.preflight_report = None
        self.preflight_text.setPlainText("Run the Prerun Check before processing the selected polygon." if polygon else "Run the Prerun Check before processing the folder.")
        self.run_button.setText("Process LiDAR")
        self.resume_button.setVisible(not polygon)
        self.retry_failed_button.setText("Retry Failed" if polygon else "Retry Failed Files")
        self.summary_label.setText("Review Polygon Batch outputs after execution." if polygon else "4. Review Results after the batch completes.")
        self._update_polygon_source_visibility()
        self._update_adaptive_visibility()
        self._update_run_button_enabled()

    def _on_execution_mode_changed(self, *_args: object) -> None:
        self._refresh_batch_option_visibility()
        self._refresh_footprint_label()

    def _refresh_batch_option_visibility(self, *_args: object) -> None:
        """Project one idempotent semantic visibility model onto durable widgets."""
        selected = {product for product, check in self.product_checks.items() if check.isChecked()}
        self.advanced_product_settings_group.setVisible(bool(selected))
        if hasattr(self, "scientific_groups"):
            self._rebuild_product_settings_form(selected)
            if self.advanced_product_settings_group.isChecked():
                _fit_collapsible_to_visible_content(self.advanced_product_settings_group)
        visibility = batch_control_visibility(
            profile=str(self.processing_profile_combo.currentData() or "recommended"),
            execution_mode=str(self.execution_mode_combo.currentData() or SEQUENTIAL_MODE),
            polygon_mode=self._current_batch_mode() == "polygon",
            repository_selected=bool(self.polygon_lidar_folder_edit.text().strip()),
        )
        if hasattr(self, 'advanced_batch_form'):
            _set_form_field_visible(self.advanced_batch_form, self.execution_mode_container, visibility.execution_mode)
            _set_form_field_visible(self.advanced_batch_form, self.max_workers_container, visibility.maximum_workers)
        self.polygon_finalization_group.setVisible(False)
        self.advanced_repository_section.setVisible(False)
        self.advanced_spatial_section.setVisible(False)
        self.advanced_batch_section.setVisible(False)
        _refresh_layout_geometry(self.advanced_batch_section)

    def _rebuild_product_settings_form(self, selected: set[ProductType]) -> None:
        """Project stable semantic rows into stacked product-owned groups."""
        fields = {field for _key, _group, _label, field in self.product_setting_rows}
        raster_products = set(ProductType)
        binned_products = {ProductType.PAD, ProductType.PAI, ProductType.FHD, ProductType.CANOPY_COVER, ProductType.VOXEL_STAT}
        active = {
            self.resolution_spin: bool(selected & raster_products),
            self.height_bin_spin: bool(selected & binned_products),
            self.canopy_threshold_spin: ProductType.CANOPY_COVER in selected,
            self.canopy_max_height_spin: ProductType.CANOPY_COVER in selected,
            self.canopy_extinction_spin: ProductType.CANOPY_COVER in selected,
            self.pad_beer_lambert_spin: ProductType.PAD in selected,
            self.pad_drop_ground_check: ProductType.PAD in selected,
            self.pai_min_height_spin: ProductType.PAI in selected,
            self.pai_max_height_spin: ProductType.PAI in selected,
            self.fhd_min_height_spin: ProductType.FHD in selected,
            self.fhd_max_height_spin: ProductType.FHD in selected,
            self.rumple_min_height_spin: ProductType.RUMPLE in selected,
            self.point_density_per_area_check: ProductType.POINT_DENSITY in selected,
            self.chm_interpolation_combo: ProductType.CHM in selected,
            self.voxel_stat_dimension_combo: ProductType.VOXEL_STAT in selected,
            self.voxel_stat_stat_combo: ProductType.VOXEL_STAT in selected,
        }
        for group_name in self.scientific_group_order:
            group_widget, forms, form_widgets = self.scientific_groups[group_name]
            group_widget.setMinimumHeight(0)
            for form in forms:
                while form.count():
                    form.takeAt(0)
            rows = [
                (key, label, field)
                for key, group, label, field in self.product_setting_rows
                if group == group_name and active[field]
            ]
            group_widget.setVisible(bool(rows))
            internal_columns = self._scientific_column_count if len(rows) > 1 else 1
            form_widgets[1].setVisible(internal_columns == 2)
            for index, (key, _label_text, field) in enumerate(rows):
                form = forms[index % internal_columns]
                field.setVisible(True)
                label = self.scientific_row_labels[key]
                if label is None:
                    form.addRow(field)
                else:
                    label.setVisible(True)
                    form.addRow(label, field)
        for field in fields:
            if not active[field]:
                field.setVisible(False)
        for group_widget, forms, _form_widgets in self.scientific_groups.values():
            for form in forms:
                form.invalidate()
                form.activate()
            if group_widget.isVisible():
                layout = group_widget.layout()
                if layout is not None:
                    layout.invalidate()
                    layout.activate()
                    group_widget.setMinimumHeight(layout.sizeHint().height())
            group_widget.updateGeometry()

    def _update_adaptive_visibility(self, *_args: object) -> None:
        """Compatibility alias for callers retained during workflow consolidation."""
        self._refresh_batch_option_visibility()

    def _apply_processing_profile(self) -> None:
        if not hasattr(self, "processing_profile_combo"):
            return
        profile = profile_by_key(str(self.processing_profile_combo.currentData() or "recommended"))
        if profile.key != "custom":
            self.max_workers_spin.setValue(5)
        self.max_workers_spin.setToolTip("Upper limit; adaptive planning may use fewer workers for memory, storage, or source safety.")
        self._refresh_batch_option_visibility()
        self._refresh_footprint_label()

    def _polygon_guided_review_text(self, report: object) -> str:
        plan = getattr(report, "execution_plan", None)
        products = tuple(getattr(getattr(report, "request", None), "products", ()))
        lines = [
            "Polygon Processing Review",
            "", "ENGINE", "READY" if getattr(report, "backend_ready", False) else "BLOCKED",
            "", "PLAN", "READY" if not getattr(report, "blockers", ()) else "BLOCKED",
            "", "SPATIAL", str(getattr(report, "spatial_alignment_status", "Unknown")).upper(),
            "", "PRODUCTS", f"READY - {len(products)} selected" if products else "BLOCKED - none selected",
            "", "DISPATCH", "Not started - dispatch validation begins only after Process LiDAR is clicked.",
            "", *guided_review_summary(plan), "",
        ]
        lines.append("Warnings:")
        warnings = getattr(report, "warnings", ())
        lines.extend(f"- {item}" for item in warnings[:5])
        if not warnings:
            lines.append("- None")
        lines.extend(("", "Blockers:"))
        blockers = getattr(report, "blockers", ())
        lines.extend(f"- {item}" for item in blockers[:5])
        if not blockers:
            lines.append("- None")
        lines.extend(("", "Technical Report:", polygon_preflight_text(report)))
        return "\n".join(lines)

    def _current_spatial_report(self):
        """Return current spatial state, refreshing readiness on demand."""
        report = self.preflight_report
        if report is None or getattr(report, "source_selection", None) is None:
            self.run_preflight()
            report = self.preflight_report
        return report if report is not None and getattr(report, "source_selection", None) is not None else None

    def preview_polygon_spatial_selection(self) -> None:
        report = self._current_spatial_report()
        if report is None:
            self.preflight_text.setPlainText("Map preview needs valid LiDAR data and polygon inputs. Review Readiness for the next action.")
            return
        selection = report.source_selection
        coverage_result = add_selected_lidar_to_qgis(report, self.iface)
        lines = [
            "Selected LiDAR Map Preview",
            "Temporary QGIS group: PyForestScan - Selected LiDAR",
            f"Live QGIS action: {coverage_result.message if coverage_result else 'Repository extent unavailable; preview text only.'}",
            f"Repository kind: {selection.repository_kind}",
            f"Intersecting LiDAR files: {len(selection.selected_sources)}",
            f"Raw coordinate overlap: {'Yes' if selection.overlap_result == 'yes' else ('No' if selection.overlap_result == 'no' else 'Not evaluated')}",
            f"Spatial alignment: {getattr(report, 'spatial_alignment_status', 'Unknown')}",
            f"Polygon extent ({selection.transformed_envelope.crs}): {selection.transformed_envelope.xmin:g}, {selection.transformed_envelope.ymin:g}, {selection.transformed_envelope.xmax:g}, {selection.transformed_envelope.ymax:g}",
        ]
        if selection.source_extent is not None:
            extent = selection.source_extent
            lines.append(f"Repository extent ({extent.crs}): {extent.xmin:g}, {extent.ymin:g}, {extent.xmax:g}, {extent.ymax:g}")
        if selection.rejected_sources:
            lines.append("Rejected sources:")
            lines.extend(f"- {item.path}: {item.rejection_code} - {item.user_reason}" for item in selection.rejected_sources[:8])
        self.preflight_text.setPlainText("\n".join(lines))

    def preview_polygon_spatial_alignment(self) -> None:
        report = self._current_spatial_report()
        if report is None:
            self.preflight_text.setPlainText("Alignment preview needs valid LiDAR data and polygon inputs. Review Readiness for the next action.")
            return
        result = preview_spatial_alignment_in_qgis(report, self.iface)
        selection = report.source_selection
        alignment = getattr(selection, "spatial_alignment", None)
        lines = [
            "Spatial Alignment Preview",
            "Temporary QGIS group: PyForestScan - Spatial Alignment",
            f"Live QGIS action: {result.message if result else 'Spatial alignment preview unavailable.'}",
            f"Spatial alignment: {getattr(report, 'spatial_alignment_status', 'Unknown')}",
            f"Polygon CRS: {getattr(report.request.polygon, 'source_crs', '')}",
            f"Repository CRS: {getattr(report.repository, 'source_crs', '')}",
            f"Transformation required: {'Yes' if alignment and alignment.transformation_required else 'No'}",
            f"Raw coordinate overlap: {'Yes' if selection.overlap_result == 'yes' else ('No' if selection.overlap_result == 'no' else 'Not evaluated')}",
            f"Final source selected: {'Yes' if selection.selected_sources else 'No'}",
        ]
        self.preflight_text.setPlainText("\n".join(lines))

    def _show_spatial_action(self, action: str) -> None:
        report = self._current_spatial_report()
        if report is None:
            self.preflight_text.setPlainText(f"{action} needs valid LiDAR data and polygon inputs. Review Readiness for the next action.")
            return
        selection = report.source_selection
        lines = [action]
        if action == "Zoom to Polygon":
            extent = selection.transformed_envelope
            result = zoom_canvas_to_bounds(extent.to_bounds(), extent.crs, self.iface, label="polygon")
            lines.append(result.message)
            lines.append(f"Target polygon extent ({extent.crs}): {extent.xmin:g}, {extent.ymin:g}, {extent.xmax:g}, {extent.ymax:g}")
        elif action == "Zoom to Repository Extent":
            extent = selection.source_extent
            if extent is None:
                lines.append("Repository coverage cannot be mapped until its coordinate system is known.")
            else:
                result = zoom_canvas_to_bounds(extent.to_bounds(), extent.crs, self.iface, label="repository extent")
                lines.append(result.message)
                lines.append(f"Target repository extent ({extent.crs}): {extent.xmin:g}, {extent.ymin:g}, {extent.xmax:g}, {extent.ymax:g}")
        else:
            extent = selection.source_extent
            poly = selection.transformed_envelope
            combined = combine_bounds(poly.to_bounds(), None if extent is None else extent.to_bounds())
            result = zoom_canvas_to_bounds(combined, poly.crs, self.iface, label="combined extent")
            lines.append(result.message)
            if extent is None:
                lines.append(f"Combined extent uses polygon only because repository extent is unavailable: {poly.xmin:g}, {poly.ymin:g}, {poly.xmax:g}, {poly.ymax:g}")
            else:
                lines.append(f"Combined extent ({poly.crs}): {min(poly.xmin, extent.xmin):g}, {min(poly.ymin, extent.ymin):g}, {max(poly.xmax, extent.xmax):g}, {max(poly.ymax, extent.ymax):g}")
                if not poly.to_bounds().intersects(extent.to_bounds()):
                    lines.append("Polygon and repository coverage are separated.")
        self.preflight_text.setPlainText("\n".join(lines))

    def browse_polygon_lidar_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose LiDAR repository")
        if path:
            self.polygon_lidar_folder_edit.setText(path)
            self.use_polygon_repository_path()

    def use_polygon_repository_path(self) -> None:
        path_text = self.polygon_lidar_folder_edit.text().strip()
        if not path_text:
            self.polygon_catalog_status_label.setText("No Catalog - paste or browse to a LiDAR repository path.")
            return
        status = select_lidar_repository_path(path_text)
        self.polygon_lidar_folder_edit.setText(str(status.normalized_path))
        self.preflight_report = None
        self.refresh_catalog_status()
        self.preflight_text.setPlainText(status.message + " No repository scan was performed.")
        self._update_run_button_enabled()

    def refresh_polygon_lidar_folder(self) -> None:
        self.preflight_report = None
        self.refresh_catalog_status()
        self.preflight_text.setPlainText("Catalog status refreshed. No repository scan was performed. Run the Prerun Check to find intersecting sources.")
        self._update_run_button_enabled()

    def inspect_polygon_repository(self) -> None:
        folder = self.polygon_lidar_folder_edit.text().strip()
        if not folder:
            self.polygon_catalog_status_label.setText("Inspect Repository needs a LiDAR repository path.")
            return
        discovery = discover_lidar_repository(folder)
        path = self._polygon_catalog_path()
        integrity = inspect_catalog_integrity(path, discovery.normalized_root) if path is not None else None
        recommendation, action = repository_setup_recommendation(discovery, integrity)
        lines = ["Repository Inspection", *discovery.summary_lines(), "", "Existing catalog:"]
        if integrity is None:
            lines.append("- Not found")
        else:
            lines.extend(f"- {line}" for line in integrity.summary_lines())
            if integrity.skip_reason_counts:
                lines.append("Skipped/problem sources:")
                for code, count in sorted(integrity.skip_reason_counts.items()):
                    lines.append(f"- {count:,} {code}")
        lines.extend(("", f"Recommended action: {action}", recommendation))
        self.polygon_index_plan_text.setPlainText("\n".join(lines))
        self.polygon_catalog_status_label.setText(recommendation)
        self.refresh_catalog_status()

    def quick_probe_polygon_repository(self) -> None:
        folder = self.polygon_lidar_folder_edit.text().strip()
        if not folder:
            self.polygon_catalog_status_label.setText("Quick Probe needs a LiDAR repository path.")
            return
        probe = quick_probe_lidar_repository(folder)
        examples = ", ".join(probe.source_type_examples) if probe.source_type_examples else "none in bounded top-level sample"
        dirs = ", ".join(probe.top_level_directory_examples[:4]) if probe.top_level_directory_examples else "none sampled"
        limit = " stopped at probe limit" if probe.stopped_by_limit else " completed within probe budget"
        self.polygon_catalog_status_label.setText(
            f"Quick Probe:{limit}; inspected {probe.inspected_entries} top-level entr{'y' if probe.inspected_entries == 1 else 'ies'} "
            f"in {probe.elapsed_seconds:.2f}s. Catalog: {'found' if probe.selection.catalog_exists else 'not found'}. "
            f"Source examples: {examples}. Directories: {dirs}. {probe.filesystem_note}"
        )
        self.preflight_text.setPlainText(probe.recommendation)

    def _polygon_catalog_path(self) -> Path | None:
        folder = self.polygon_lidar_folder_edit.text().strip()
        if not folder:
            return None
        selection = select_lidar_repository_path(folder)
        if selection.valid:
            return selection.catalog_path
        return default_lidar_catalog_path(Path(folder))

    def refresh_catalog_status(self) -> None:
        if not hasattr(self, "polygon_catalog_status_label"):
            return
        folder = self.polygon_lidar_folder_edit.text().strip()
        running = self.catalog_thread is not None
        if not folder:
            self.polygon_catalog_status_label.setText("Repository needs attention - choose a LiDAR repository, then Build Index.")
            self.detect_index_strategy_button.setEnabled(False)
            self.build_relevant_index_button.setEnabled(False)
            self.inspect_repository_button.setEnabled(False)
            self.build_catalog_button.setEnabled(False)
            self.update_catalog_button.setEnabled(False)
            self.resume_catalog_button.setEnabled(False)
            self.pause_catalog_button.setEnabled(False)
            self.repair_catalog_button.setEnabled(False)
            self.assign_repository_crs_button.setEnabled(False)
            self.add_coverage_button.setEnabled(False)
            self.view_sources_button.setEnabled(False)
            self.export_repository_diagnostic_button.setEnabled(False)
            self.repair_ept_catalog_button.setVisible(False)
            self.repair_ept_catalog_button.setEnabled(False)
            self.move_catalog_local_button.setVisible(False)
            self.move_catalog_local_button.setEnabled(False)
            self.open_catalog_folder_button.setEnabled(False)
            return
        path = self._polygon_catalog_path()
        selection = select_lidar_repository_path(folder)
        latest = latest_catalog_job_state(path) if path is not None else None
        if latest is not None and latest.status in {CatalogJobStatus.INTERRUPTED, CatalogJobStatus.PAUSED, CatalogJobStatus.FAILED}:
            state_text = f"Catalog {latest.status.value.title()} - {latest.stage.value}; discovered {latest.discovered:,}; indexed {latest.indexed:,}; errors {latest.errors:,}."
        else:
            state_text = catalog_status_text(selection.normalized_path, path)
        if selection.valid and (selection.normalized_path / "ept.json").is_file():
            state_text = "EPT source ready — direct spatial selection is active; a LiDAR catalog is not required."
        incorrect_ept_catalog = bool(selection.valid and path and path.exists() and incorrect_ept_catalog_detected(path, Path(folder)))
        if incorrect_ept_catalog:
            state_text = "Incorrect EPT Catalog Detected - this catalog indexes internal EPT node files individually. Use Repair EPT Catalog."
        self.polygon_catalog_status_label.setText(state_text if selection.valid else selection.message)
        exists = bool(path and path.exists())
        interrupted = bool(latest is not None and latest.status in {CatalogJobStatus.INTERRUPTED, CatalogJobStatus.PAUSED})
        integrity = inspect_catalog_integrity(path, selection.normalized_path) if path is not None else None
        states = repository_action_states(has_repository=selection.valid, repository_readable=selection.readable, catalog_exists=exists, integrity=integrity, latest_job=latest, running=running)
        self.detect_index_strategy_button.setEnabled(selection.valid and not running)
        self.build_relevant_index_button.setEnabled(selection.valid and not running)
        self.inspect_repository_button.setEnabled(states.inspect_repository.enabled)
        self.inspect_repository_button.setToolTip(states.inspect_repository.disabled_reason)
        self.build_catalog_button.setEnabled(states.scan_file_headers.enabled)
        self.update_catalog_button.setEnabled(states.update_catalog.enabled)
        self.update_catalog_button.setToolTip(states.update_catalog.disabled_reason)
        self.resume_catalog_button.setEnabled(states.resume_catalog_build.enabled)
        self.resume_catalog_button.setToolTip(states.resume_catalog_build.disabled_reason)
        self.pause_catalog_button.setEnabled(states.pause_after_current_chunk.enabled)
        self.repair_catalog_button.setEnabled(states.repair_catalog.enabled)
        self.repair_catalog_button.setToolTip(states.repair_catalog.disabled_reason)
        self.assign_repository_crs_button.setEnabled(bool(integrity and integrity.status == 'CRS Assignment Required') and not running)
        self.assign_repository_crs_button.setToolTip('Assign an explicit CRS to bounded sources that lack embedded CRS metadata.')
        self.add_coverage_button.setEnabled(states.add_coverage_to_map.enabled)
        self.add_coverage_button.setToolTip(states.add_coverage_to_map.disabled_reason)
        self.view_sources_button.setEnabled(exists and not running)
        self.export_repository_diagnostic_button.setEnabled(selection.valid and exists and not running)
        local_catalog_path = default_lidar_catalog_path(selection.normalized_path)
        move_local_available = bool(selection.valid and path and path.exists() and Path(path) != local_catalog_path)
        self.repair_ept_catalog_button.setVisible(incorrect_ept_catalog)
        self.repair_ept_catalog_button.setEnabled(incorrect_ept_catalog and not running)
        self.move_catalog_local_button.setVisible(move_local_available)
        self.move_catalog_local_button.setEnabled(move_local_available and not running)
        self.open_catalog_folder_button.setEnabled(bool(path) and not running)

    def repair_polygon_ept_catalog(self) -> None:
        folder = self.polygon_lidar_folder_edit.text().strip()
        path = self._polygon_catalog_path()
        if not folder or path is None:
            self.polygon_index_plan_text.setPlainText("Choose a LiDAR repository before repairing an EPT catalog.")
            return
        report = repair_ept_catalog(path, Path(folder))
        self.polygon_index_plan_text.setPlainText(report.message + (f"\nBackup: {report.backup_path}" if report.backup_path else ""))
        self.refresh_catalog_status()

    def move_polygon_catalog_local(self) -> None:
        folder = self.polygon_lidar_folder_edit.text().strip()
        path = self._polygon_catalog_path()
        if not folder or path is None:
            self.polygon_index_plan_text.setPlainText("Choose a LiDAR repository before moving a catalog.")
            return
        selection = select_lidar_repository_path(folder)
        if not selection.valid:
            self.polygon_index_plan_text.setPlainText(selection.message)
            return
        report = move_lidar_catalog_to_local_storage(selection.normalized_path, path)
        detail = f"\nOriginal catalog preserved: {report.source_path}" if report.moved else ""
        self.polygon_index_plan_text.setPlainText(report.message + detail)
        self.refresh_catalog_status()

    def choose_polygon_existing_index(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose existing LiDAR footprint index",
            "",
            "LiDAR indexes (*.geojson *.json *.csv *.gpkg *.shp *.fgb);;All files (*.*)",
        )
        if path:
            self.polygon_existing_index_edit.setText(path)
            self.current_index_plan = None
            self.detect_polygon_index_strategy()

    def detect_polygon_index_strategy(self) -> None:
        folder_text = self.polygon_lidar_folder_edit.text().strip()
        if not folder_text:
            self.polygon_index_plan_text.setPlainText("Choose a LiDAR repository before detecting an indexing strategy.")
            return
        requested = LidarIndexStrategy(self.polygon_index_strategy_combo.currentData() or LidarIndexStrategy.AUTOMATIC.value)
        existing_index = self.polygon_existing_index_edit.text().strip() or None
        capabilities = detect_repository_capabilities(folder_text, existing_index_path=existing_index)
        plan = choose_index_strategy(capabilities, requested=requested)
        self.current_index_plan = plan
        self.polygon_index_plan_text.setPlainText(format_repository_index_plan(plan))
        self.polygon_catalog_status_label.setText(f"Repository setup: {plan.selected_strategy.value}; cost {plan.expected_build_cost.value}. No full scan was performed.")

    def build_relevant_polygon_index(self) -> None:
        if self.current_index_plan is None:
            self.detect_polygon_index_strategy()
        plan = self.current_index_plan
        if plan is not None and getattr(plan, "selected_strategy", None) is LidarIndexStrategy.EXISTING_SPATIAL_INDEX and getattr(plan, "sources_to_register", None):
            source = plan.sources_to_register[0]
            if Path(source).suffix.lower() == ".sqlite":
                self.polygon_index_plan_text.setPlainText(f"Existing PyForestScan catalog is ready: {source}.")
                self.refresh_catalog_status()
                return
            try:
                catalog = register_existing_footprint_index(source, self.polygon_lidar_folder_edit.text().strip(), self._polygon_catalog_path())
            except Exception as exc:  # pragma: no cover - defensive UI boundary
                self.polygon_index_plan_text.setPlainText(f"Existing index registration failed: {exc}")
                return
            self.polygon_index_plan_text.setPlainText(f"Registered existing spatial index into {catalog}.")
            self.refresh_catalog_status()
            return
        if plan is not None and getattr(plan, "selected_strategy", None) is LidarIndexStrategy.NATIVE_HIERARCHICAL_SOURCE and getattr(plan, "sources_to_register", None):
            try:
                catalog = register_native_sources(self.polygon_lidar_folder_edit.text().strip(), plan.sources_to_register, self._polygon_catalog_path())
            except Exception as exc:  # pragma: no cover - defensive UI boundary
                self.polygon_index_plan_text.setPlainText(f"Native EPT/COPC registration failed: {exc}")
                return
            self.polygon_index_plan_text.setPlainText(f"Registered native EPT/COPC sources into {catalog}.")
            self.refresh_catalog_status()
            return
        self.polygon_index_plan_text.setPlainText("Selected strategy requires the durable catalog worker; indexing starts with the current repository safeguards.")
        self._start_polygon_catalog_job("lidar_catalog_build")

    def build_polygon_catalog(self) -> None:
        self._start_polygon_catalog_job("lidar_catalog_build")

    def update_polygon_catalog(self) -> None:
        self._start_polygon_catalog_job("lidar_catalog_update")

    def resume_polygon_catalog(self) -> None:
        path = self._polygon_catalog_path()
        latest = latest_catalog_job_state(path) if path is not None else None
        if latest is None or latest.status not in {CatalogJobStatus.INTERRUPTED, CatalogJobStatus.PAUSED}:
            self.polygon_index_plan_text.setPlainText("No paused or incomplete catalog build exists for this repository.")
            self.refresh_catalog_status()
            return
        self._start_polygon_catalog_job("lidar_catalog_resume")

    def repair_polygon_catalog(self) -> None:
        folder = self.polygon_lidar_folder_edit.text().strip()
        path = self._polygon_catalog_path()
        if not folder or path is None:
            self.polygon_index_plan_text.setPlainText("Choose a LiDAR repository before repairing a catalog.")
            return
        selection = select_lidar_repository_path(folder)
        report = repair_catalog(path, selection.normalized_path)
        lines = [report.message, "", "Before:", *report.before.summary_lines(), "", "After:", *report.after.summary_lines()]
        if report.backup_path:
            lines.append(f"Backup: {report.backup_path}")
        self.polygon_index_plan_text.setPlainText("\n".join(lines))
        self.refresh_catalog_status()

    def assign_polygon_repository_crs(self) -> None:
        folder = self.polygon_lidar_folder_edit.text().strip()
        if not folder:
            self.polygon_index_plan_text.setPlainText("Choose a LiDAR repository before assigning a coordinate system.")
            return
        crs, ok = QInputDialog.getText(self, "Assign Repository Coordinate System", "CRS auth id, for example EPSG:6635")
        if not ok or not crs.strip():
            self.polygon_index_plan_text.setPlainText("Repository CRS assignment cancelled.")
            return
        selection = select_lidar_repository_path(folder)
        assignment = default_spatial_assignment_store().assign_repository(selection.normalized_path, crs.strip())
        self.polygon_index_plan_text.setPlainText("\n".join([
            "Repository Coordinate System Assigned",
            f"CRS: {assignment.horizontal_crs}",
            "Unknown member files now inherit this trusted repository assignment.",
            "Original LAS/LAZ files were not modified.",
        ]))
        self.preflight_report = None
        self.refresh_catalog_status()
        self.run_preflight()

    def add_polygon_repository_coverage(self) -> None:
        folder = self.polygon_lidar_folder_edit.text().strip()
        path = self._polygon_catalog_path()
        if not folder or path is None:
            self.polygon_index_plan_text.setPlainText("Choose a LiDAR repository before adding coverage.")
            return
        selection = select_lidar_repository_path(folder)
        model = build_repository_coverage_model(path, selection.normalized_path, mode="outline")
        result = add_repository_coverage_to_qgis(model, self.iface)
        lines = [
            "Repository Coverage",
            result.message,
            f"QGIS group: {model.group_name}",
            f"Mode: {model.mode}",
            f"Layer ids: {', '.join(result.layer_ids) if result.layer_ids else 'none'}",
            f"Feature count: {result.feature_count}",
            f"CRS: {model.crs}",
        ]
        if model.union_extent is not None:
            lines.append(f"Extent: X {model.union_extent.xmin:g}-{model.union_extent.xmax:g}; Y {model.union_extent.ymin:g}-{model.union_extent.ymax:g}")
        self.polygon_index_plan_text.setPlainText("\n".join(lines))

    def view_polygon_repository_sources(self) -> None:
        folder = self.polygon_lidar_folder_edit.text().strip()
        path = self._polygon_catalog_path()
        if not folder or path is None:
            self.polygon_index_plan_text.setPlainText("Choose a LiDAR repository before viewing sources.")
            return
        selection = select_lidar_repository_path(folder)
        rows = source_view_rows(path, selection.normalized_path, limit=40)
        inspection = inspect_catalog_records(path, selection.normalized_path)
        lines = ["Repository Sources", f"Showing {len(rows)} source row(s)", f"Catalog rows: {inspection.source_row_count}; RTree rows: {inspection.rtree_row_count}", "file | type | status | embedded CRS | effective CRS | bounds | problem"]
        if inspection.extent_defining_sources:
            lines.append("Extent-defining files:")
            for item in inspection.extent_defining_sources:
                lines.append(f"- {item.role}: {item.source_path.name} = {item.value:g}")
        for row in rows[:40]:
            bounds = "unavailable" if row.xmin is None else f"{row.xmin:g},{row.ymin:g},{row.xmax:g},{row.ymax:g}"
            lines.append(f"{row.file} | {row.source_type} | {row.status} | {row.embedded_crs} | {row.effective_crs} | {bounds} | {row.problem or 'usable'}")
        if not rows:
            lines.append("No source rows are available.")
        self.polygon_index_plan_text.setPlainText("\n".join(lines))

    def export_polygon_repository_diagnostic(self) -> None:
        folder = self.polygon_lidar_folder_edit.text().strip()
        path = self._polygon_catalog_path()
        if not folder or path is None:
            self.polygon_index_plan_text.setPlainText("Choose a LiDAR repository before exporting diagnostics.")
            return
        selection = select_lidar_repository_path(folder)
        output = Path(path).with_name("repository_diagnostic_report.json")
        export_repository_diagnostic_report(selection.normalized_path, path, output)
        self.polygon_index_plan_text.setPlainText(f"Repository diagnostic report exported:\n{output}")


    def _start_polygon_catalog_job(self, job_type: str) -> None:
        folder_text = self.polygon_lidar_folder_edit.text().strip()
        if not folder_text:
            _set_status_badge(self.status_label, "WARNING", "Status: Needs review - choose a LiDAR repository before building a catalog.")
            return
        selection = select_lidar_repository_path(folder_text)
        if not selection.valid or not selection.readable:
            _set_status_badge(self.status_label, "FAILED", f"Status: Failed - {selection.message}")
            return
        catalog = self._polygon_catalog_path()
        if catalog is None:
            return
        self.catalog_pause_requested = False
        self.detect_index_strategy_button.setEnabled(False)
        self.build_relevant_index_button.setEnabled(False)
        self.build_catalog_button.setEnabled(False)
        self.update_catalog_button.setEnabled(False)
        self.resume_catalog_button.setEnabled(False)
        self.pause_catalog_button.setEnabled(True)
        self.preflight_button.setEnabled(False)
        self.polygon_catalog_status_label.setText(f"Catalog job queued - {job_type.replace('_', ' ')} for {selection.normalized_path}.")
        _set_status_badge(self.status_label, "RUNNING", "Status: Running - catalog job active.")
        spec = CatalogJobSpec.create(job_type, selection.normalized_path, catalog)
        self.catalog_thread = QThread(self)
        self.catalog_worker = _CatalogBuildWorker(spec, self._catalog_pause_state)
        self.catalog_worker.moveToThread(self.catalog_thread)
        self.catalog_thread.started.connect(self.catalog_worker.run)
        self.catalog_worker.progress.connect(self._on_catalog_build_progress)
        self.catalog_worker.completed.connect(self._on_catalog_build_complete)
        self.catalog_worker.failed.connect(self._on_catalog_build_failed)
        self.catalog_worker.completed.connect(self.catalog_thread.quit)
        self.catalog_worker.failed.connect(self.catalog_thread.quit)
        self.catalog_thread.finished.connect(self.catalog_worker.deleteLater)
        self.catalog_thread.finished.connect(self.catalog_thread.deleteLater)
        self.catalog_thread.finished.connect(self._clear_catalog_thread)
        self.catalog_thread.start()

    def pause_polygon_catalog(self) -> None:
        self.catalog_pause_requested = True
        self.pause_catalog_button.setEnabled(False)
        self.polygon_catalog_status_label.setText("Pause requested. Catalog job will pause after the current safe chunk commits.")

    def _catalog_pause_state(self) -> bool:
        return self.catalog_pause_requested

    def _on_catalog_build_progress(self, progress: object) -> None:
        if hasattr(progress, "to_dict"):
            status = getattr(progress, "status", None)
            stage = getattr(progress, "stage", None)
            rate = getattr(progress, "rate_per_second", None)
            rate_text = "rate pending" if rate is None else f"{rate:.1f} sources/sec"
            percent = getattr(progress, "percent", None)
            percent_text = "indeterminate" if percent is None else f"{percent}%"
            latest = getattr(progress, "latest_source", "")
            latest_text = f" Latest: {latest}" if latest else ""
            self.polygon_catalog_status_label.setText(
                f"Catalog {getattr(status, 'value', status)} - {getattr(stage, 'value', stage)}; "
                f"progress {percent_text}; discovered {getattr(progress, 'discovered', 0):,}; "
                f"indexed {getattr(progress, 'indexed', 0):,}; unchanged {getattr(progress, 'unchanged', 0):,}; "
                f"errors {getattr(progress, 'errors', 0):,}; {rate_text}.{latest_text}"
            )
            return
        if isinstance(progress, dict):
            self.polygon_catalog_status_label.setText(
                "Catalog build running - "
                f"discovered {progress.get('discovered', 0):,}; "
                f"indexed {progress.get('indexed', 0):,}; "
                f"unchanged {progress.get('unchanged', 0):,}; "
                f"errors {progress.get('errors', 0):,}."
            )

    def _on_catalog_build_complete(self, result: object) -> None:
        self.preflight_button.setEnabled(True)
        self.preflight_report = None
        if getattr(result, "cancelled", False):
            _set_status_badge(self.status_label, "WARNING", f"Status: Warning - catalog interrupted after indexing {getattr(result, 'indexed_count', 0):,}; resume is available.")
            self.preflight_text.setPlainText("Catalog build was interrupted after a safe chunk. Resume Catalog Build to continue without discarding indexed records.")
        else:
            _set_status_badge(self.status_label, "READY", f"Status: Ready - catalog indexed {getattr(result, 'indexed_count', 0):,}; unchanged {getattr(result, 'unchanged_count', 0):,}; errors {getattr(result, 'error_count', 0):,}.")
            self.preflight_text.setPlainText("Catalog ready. Run the Prerun Check to find intersecting sources.")
        self.refresh_catalog_status()

    def _on_catalog_build_failed(self, message: str) -> None:
        self.preflight_button.setEnabled(True)
        _set_status_badge(self.status_label, "FAILED", f"Status: Failed - catalog job failed: {message}")
        self.polygon_catalog_status_label.setText(f"Catalog job failed: {message}")
        self.refresh_catalog_status()

    def _clear_catalog_thread(self) -> None:
        self.catalog_thread = None
        self.catalog_worker = None
        self.catalog_pause_requested = False
        self.refresh_catalog_status()

    def open_polygon_catalog_folder(self) -> None:
        path = self._polygon_catalog_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def browse_polygon_vector_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose polygon vector file", "", POLYGON_VECTOR_FILE_FILTER)
        if path:
            self.polygon_vector_file_edit.setText(path)
            self._refresh_polygon_vector_layers(path)

    def refresh_polygon_layers(self) -> None:
        self._adopted_polygon_selection = None
        previous = self.polygon_layer_combo.currentData()
        previous_id = getattr(previous, "layer_id", None)
        self.polygon_layer_combo.blockSignals(True)
        self.polygon_layer_combo.clear()
        items = polygon_layer_items(self.iface)
        for item in items:
            self.polygon_layer_combo.addItem(item.label, item)
        if previous_id:
            for index in range(self.polygon_layer_combo.count()):
                item = self.polygon_layer_combo.itemData(index)
                if getattr(item, "layer_id", None) == previous_id:
                    self.polygon_layer_combo.setCurrentIndex(index)
                    break
        self.polygon_layer_combo.blockSignals(False)
        if not items:
            self.polygon_layer_status_label.setText("No polygon layers are loaded. Add a polygon layer to QGIS or choose a vector file from disk.")
        else:
            current = self.polygon_layer_combo.currentData()
            if current is not None and getattr(current, "selected_feature_count", 0) > 0:
                self.polygon_layer_mode_combo.setCurrentIndex(0)
            elif current is not None:
                self.polygon_layer_mode_combo.setCurrentIndex(1)
            self._update_selected_polygon_layer_status()
        self.preflight_report = None
        self._update_run_button_enabled()

    def _update_selected_polygon_layer_status(self, *_args: object) -> None:
        item = self.polygon_layer_combo.currentData()
        if item is None:
            self.polygon_layer_status_label.setText("No polygon layer selected.")
            self.polygon_layer_status_label.setVisible(True)
            self.use_selected_features_button.setEnabled(False)
            return
        selected = selected_feature_count_text(getattr(item, "selected_feature_count", 0))
        guidance = "Use Selected Features is ready." if getattr(item, "selected_feature_count", 0) else "No selected features; use the entire layer or select polygon features on the map."
        self.polygon_layer_status_label.setText(f"{item.name}: {selected}; CRS {item.crs or 'unknown'}. {guidance}")
        self.polygon_layer_status_label.setVisible(not bool(getattr(item, "selected_feature_count", 0)))
        self.use_selected_features_button.setEnabled(bool(getattr(item, "selected_feature_count", 0)))
        self.use_entire_layer_button.setEnabled(bool(getattr(item, "feature_count", 0)))

    def use_selected_polygon_features(self) -> None:
        """Adopt the current lightweight QGIS feature selection without planning."""
        self.refresh_polygon_layers()
        item = self.polygon_layer_combo.currentData()
        if int(getattr(item, "selected_feature_count", 0) or 0):
            self.polygon_layer_mode_combo.setCurrentIndex(
                self.polygon_layer_mode_combo.findData("selected")
            )
            self._adopted_polygon_selection = self._normalized_polygon_selection()
            self._on_session_input_changed()
            self._publish_session_state()
        self._update_selected_polygon_layer_status()

    def use_entire_polygon_layer(self) -> None:
        """Explicitly adopt the entire chosen QGIS polygon layer."""
        item = self.polygon_layer_combo.currentData()
        if item is None:
            return
        self.polygon_layer_mode_combo.setCurrentIndex(self.polygon_layer_mode_combo.findData("full"))
        self._adopted_polygon_selection = self._normalized_polygon_selection()
        self._on_session_input_changed()
        self._publish_session_state()

    def _update_polygon_source_visibility(self, *_args: object) -> None:
        if not hasattr(self, "polygon_source_combo"):
            return
        mode = self.polygon_source_combo.currentData()
        self._adopted_polygon_selection = None
        container_visible = self._current_batch_mode() == "polygon"
        self.polygon_qgis_source_frame.setVisible(container_visible and mode == "qgis")
        self.polygon_vector_source_frame.setVisible(container_visible and mode == "file")
        self.polygon_wkt_group.setVisible(container_visible and mode == "wkt")
        if mode == "wkt":
            self.polygon_wkt_group.setChecked(True)
        if mode == "qgis" and container_visible:
            self.refresh_polygon_layers()

    def _refresh_polygon_vector_layers(self, path: str) -> None:
        self.polygon_vector_layer_combo.clear()
        self.polygon_vector_layer_combo.setVisible(False)
        try:
            options = vector_file_layer_options(path)
        except Exception as exc:  # noqa: BLE001
            self.polygon_vector_status_label.setText(f"Vector file could not be inspected: {exc}")
            return
        if not options:
            self.polygon_vector_status_label.setText("No polygon layers were found in this vector file. Point and line layers are not accepted.")
            return
        for option in options:
            self.polygon_vector_layer_combo.addItem(option.label, option)
        self.polygon_vector_layer_combo.setVisible(len(options) > 1)
        if len(options) > 1:
            self.polygon_vector_status_label.setText("GeoPackage/container has multiple polygon layers. Choose one; all features are dissolved for preflight.")
        else:
            option = options[0]
            self.polygon_vector_status_label.setText(f"{option.name}: {option.geometry_type}, CRS {option.crs or 'unknown'}. All features will be dissolved.")
        self.preflight_report = None
        self._update_run_button_enabled()

    def _normalized_polygon_selection(self):
        mode = self.polygon_source_combo.currentData()
        processing_crs = self.polygon_processing_crs_edit.text().strip() if hasattr(self, "polygon_processing_crs_edit") else ""
        if mode == "qgis":
            item = self.polygon_layer_combo.currentData()
            if item is None:
                raise ValueError("Choose a loaded QGIS polygon layer or choose a vector file.")
            return normalize_qgis_layer_selection(
                self.iface,
                item.layer_id,
                use_selected=self.polygon_layer_mode_combo.currentData() == "selected",
                dissolve=self.polygon_dissolve_check.isChecked(),
                processing_crs=processing_crs,
            )
        if mode == "file":
            path = self.polygon_vector_file_edit.text().strip()
            if not path:
                raise ValueError("Choose a polygon vector file.")
            if self.polygon_vector_layer_combo.count() == 0:
                self._refresh_polygon_vector_layers(path)
            option = self.polygon_vector_layer_combo.currentData()
            return normalize_vector_file_selection(
                path,
                layer_uri=getattr(option, "uri", None),
                layer_name=getattr(option, "name", None),
                processing_crs=processing_crs,
            )
        return normalize_polygon_source(
            PolygonSource(
                source_mode="wkt",
                polygon_wkt=self.polygon_wkt_edit.text(),
                source_crs=self.polygon_crs_edit.text(),
                processing_crs=processing_crs or self.polygon_crs_edit.text(),
            )
        )

    def reset_polygon_batch(self) -> None:
        self.preflight_report = None
        self._adopted_polygon_selection = None
        self.batch_items = []
        self.failed_paths = []
        self.batch_results.clear()
        self.progress_bar.setValue(0)
        removed = remove_spatial_preview_layers(self.iface)
        self.preflight_text.setPlainText(f"Polygon Batch reset. {removed.message} Cleared current plan and source selection. Catalog and generated outputs were preserved.")
        _set_status_badge(self.status_label, "NOT CONFIGURED", "Status: Not set up - run the Prerun Check.")
        self._publish_session_state()
        self._update_run_button_enabled()

    def set_default_output_folder(self, folder: Path | None) -> None:
        """Use configured default output folder when empty."""
        if folder is not None and not self.output_folder_edit.text().strip():
            self.output_folder_edit.setText(str(folder))

    def browse_input_folder(self) -> None:
        """Choose the folder to scan for lidar datasets."""
        path = QFileDialog.getExistingDirectory(self, "Choose input folder")
        if path:
            self.input_folder_edit.setText(path)

    def use_current_map_extent_for_folder_bounds(self) -> None:
        """Populate folder clipping bounds from the current QGIS map canvas."""
        try:
            extent = self.iface.mapCanvas().extent()
            values = (extent.xMinimum(), extent.xMaximum(), extent.yMinimum(), extent.yMaximum())
            if not all(math.isfinite(float(value)) for value in values):
                raise ValueError("The current map extent is not finite.")
            self.folder_clip_bounds_edit.setText(",".join(f"{float(value):.12g}" for value in values))
        except Exception as exc:  # noqa: BLE001 - map canvas availability is runtime-specific.
            self.preflight_text.setPlainText(f"Could not use the current map extent: {exc}")

    def _folder_clip_bounds(self) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if not self.folder_clip_bounds_check.isChecked():
            return None
        text = self.folder_clip_bounds_edit.text().strip()
        try:
            values = tuple(float(part.strip()) for part in text.split(","))
        except ValueError as exc:
            raise BatchExecutionError("Folder clipping bounds must contain four numbers: xmin,xmax,ymin,ymax.") from exc
        if len(values) != 4 or not all(math.isfinite(value) for value in values):
            raise BatchExecutionError("Folder clipping bounds must contain four finite numbers: xmin,xmax,ymin,ymax.")
        xmin, xmax, ymin, ymax = values
        if xmin >= xmax or ymin >= ymax:
            raise BatchExecutionError("Folder clipping bounds require xmin < xmax and ymin < ymax.")
        return ((xmin, xmax), (ymin, ymax))

    def show_spatial_assignment_prompt(self, visible: bool = True) -> None:
        """Expose the compact resolver only when missing spatial meaning blocks preparation."""
        self.spatial_assignment_frame.setVisible(bool(visible))

    def set_spatial_intervention(self, blockers: object = ()) -> None:
        """Show only the source-specific spatial controls required by preflight."""
        text = " ".join(str(item) for item in (blockers or ())).upper()
        units_needed = "SOURCE_UNITS_UNKNOWN" in text
        crs_needed = any(token in text for token in ("CRS_UNKNOWN", "POLYGON_CRS_UNKNOWN", "COORDINATE", "ALIGNMENT", "AMBIGU"))
        visible = units_needed or crs_needed
        self.spatial_assignment_frame.setVisible(visible)
        self.source_units_combo.setVisible(units_needed)
        self.confirm_source_units_button.setVisible(units_needed)
        self.choose_source_crs_button.setVisible(crs_needed)
        self.use_project_crs_button.setVisible(crs_needed)
        self.assignment_scope_combo.setVisible(visible)
        if units_needed and crs_needed:
            self.spatial_assignment_title.setText("LiDAR spatial reference needed")
            self.spatial_assignment_help.setText("Choose the source units and coordinate system to continue. Coordinates are not transformed.")
        elif units_needed:
            self.spatial_assignment_title.setText("LiDAR units needed")
            self.spatial_assignment_help.setText("Choose meters, international feet, or US survey feet to continue.")
        elif crs_needed:
            self.spatial_assignment_title.setText("Coordinate system needed")
            self.spatial_assignment_help.setText("Use the project CRS only when it truly matches the LiDAR, or choose the correct CRS.")

    def _assignment_target(self) -> tuple[Path, AssignmentScope]:
        scope = AssignmentScope(str(self.assignment_scope_combo.currentData() or AssignmentScope.FILE.value))
        selected = self._selected_paths()
        if scope is AssignmentScope.FILE:
            if len(selected) != 1:
                raise ValueError("Select one LiDAR file for a file-specific assignment.")
            return Path(selected[0]), scope
        folder = self.input_folder_edit.text().strip()
        if not folder:
            raise ValueError("Choose a LiDAR repository first.")
        return Path(folder), scope

    def assign_selected_source_units(self) -> None:
        try:
            target, scope = self._assignment_target()
            units = LinearUnit.parse(self.source_units_combo.currentData())
            default_spatial_assignment_store().assign_units(target, units, scope=scope, notes="Confirmed in Mission Control before processing.")
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Spatial Assignment", str(exc))
            return
        self.preflight_report = None
        self.show_spatial_assignment_prompt(False)
        self.preflight_summary_label.setText("Source units saved. Run Prerun Check to rebuild the preparation plan.")
        self.run_preflight()

    def assign_selected_source_crs(self) -> None:
        try:
            target, scope = self._assignment_target()
            from qgis.gui import QgsProjectionSelectionDialog
            dialog = QgsProjectionSelectionDialog(self)
            if not dialog.exec_():
                return
            crs = dialog.crs()
            authid = crs.authid() or crs.toWkt()
            default_spatial_assignment_store().assign(target, scope=scope, crs=authid, provenance="qgis_crs_selector", notes="Coordinates unchanged; CRS assigned in Mission Control.")
        except (ImportError, ValueError, OSError) as exc:
            QMessageBox.warning(self, "Coordinate System", str(exc))
            return
        self.preflight_report = None
        self.show_spatial_assignment_prompt(False)
        self.run_preflight()

    def assign_project_crs_to_selected_source(self) -> None:
        try:
            target, scope = self._assignment_target()
            from qgis.core import QgsProject
            crs = QgsProject.instance().crs()
            authid = crs.authid() or crs.toWkt()
            if not authid:
                raise ValueError("The current QGIS project has no valid coordinate system.")
            answer = QMessageBox.question(self, "Use Project CRS", f"Confirm that {target.name} coordinates are already expressed in {authid}. Coordinates will not be transformed.")
            if answer != QMessageBox.Yes:
                return
            default_spatial_assignment_store().assign(target, scope=scope, crs=authid, provenance="confirmed_project_crs", notes="Explicitly confirmed as matching the QGIS project CRS; coordinates unchanged.")
        except (ImportError, ValueError, OSError) as exc:
            QMessageBox.warning(self, "Project Coordinate System", str(exc))
            return
        self.preflight_report = None
        self.show_spatial_assignment_prompt(False)
        self.run_preflight()

    def browse_output_folder(self) -> None:
        """Choose the batch output root folder."""
        path = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if path:
            self.output_folder_edit.setText(path)

    def discover_files(self) -> None:
        """Discover supported lidar datasets for batch selection."""
        folder = self.input_folder_edit.text().strip()
        if not folder:
            _set_status_badge(self.status_label, "WARNING", "Status: Needs review - choose an input folder before discovery.")
            return
        self.discover_button.setEnabled(False)
        _set_status_badge(self.status_label, "RUNNING", "Status: Running - discovering files.")
        QApplication.processEvents()
        try:
            datasets = discover_lidar_files(folder, self.recursive_check.isChecked())
        except ValueError as exc:
            _set_status_badge(self.status_label, "FAILED", f"Status: Failed - discovery failed: {exc}")
            self.discover_button.setEnabled(True)
            return
        finally:
            self.discover_button.setEnabled(True)
        self.discovered_paths = [item.path for item in datasets]
        self.file_list.clear()
        for item in datasets:
            row = QListWidgetItem(f"{item.path.name}\nStatus: {item.status}; bounds: {item.bounds_summary}\n{item.path}")
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked if item.selected else Qt.Unchecked)
            row.setSizeHint(QSize(0, 72))
            self.file_list.addItem(row)
        self.file_list.setVisible(bool(datasets))
        self.file_empty_label.setVisible(not datasets)
        _size_list_to_content(self.file_list, row_height=72)
        _set_status_badge(self.status_label, "READY", f"Status: Ready - discovered {len(datasets)} supported dataset(s).")
        self._refresh_footprint_label()

    def run_preflight(self) -> None:
        """Run batch preflight and update readiness display."""
        self._prerun_terminal = False
        self.preflight_button.setEnabled(False)
        self.preflight_text.setPlainText("Running Prerun Check...")
        self.status_label.setText("Checking request...")
        self._update_processing_density(ProcessingUiState.VALIDATING)
        QApplication.processEvents()
        if self._current_batch_mode() == "polygon":
            # run_polygon_batch_preflight is executed by _PolygonPreflightWorker;
            # this UI method only captures the immutable request and submits it.
            try:
                request = self.build_current_processing_request()
            except (BatchExecutionError, ValueError) as exc:
                self.preflight_text.setPlainText(f"BLOCKER: {exc}")
                self.preflight_report = None
                self._update_run_button_enabled()
                self.preflight_button.setEnabled(True)
                self._update_processing_density(ProcessingUiState.IDLE)
                return
            self.preflight_cancel_event.clear()
            self.cancel_preflight_button.setVisible(True)
            self.cancel_preflight_button.setEnabled(True)
            self.preflight_summary_label.setText("Analyzing selected area...")
            self.preflight_thread = QThread(self)
            self.preflight_worker = _PolygonPreflightWorker(request, self.preflight_cancel_event.is_set)
            self.preflight_worker.moveToThread(self.preflight_thread)
            self.preflight_thread.started.connect(self.preflight_worker.run)
            self.preflight_worker.progress.connect(self._on_polygon_preflight_progress)
            self.preflight_worker.completed.connect(self._on_polygon_preflight_complete)
            self.preflight_worker.failed.connect(self._on_polygon_preflight_failed)
            self.preflight_worker.completed.connect(self.preflight_thread.quit)
            self.preflight_worker.failed.connect(self.preflight_thread.quit)
            self.preflight_thread.finished.connect(self.preflight_worker.deleteLater)
            self.preflight_thread.finished.connect(self.preflight_thread.deleteLater)
            self.preflight_thread.finished.connect(self._clear_preflight_thread)
            self.preflight_thread.start()
            return
        try:
            request = self.build_current_processing_request()
        except BatchExecutionError as exc:
            self.preflight_text.setPlainText(f"BLOCKER: {exc}")
            self.preflight_report = None
            self._update_run_button_enabled()
            self.preflight_button.setEnabled(True)
            self._update_processing_density(ProcessingUiState.IDLE)
            return
        try:
            runtime_token = BackendService().processing_engine_service().runtime_token_for(tuple(product.value for product in request.settings.products))
            request = replace(request, runtime_token=runtime_token)
        except Exception as exc:
            self.preflight_text.setPlainText(f"BLOCKER: Processing Engine could not publish a runtime identity: {exc}")
            self.preflight_report = None
            self._update_run_button_enabled()
            self.preflight_button.setEnabled(True)
            self._update_processing_density(ProcessingUiState.IDLE)
            return
        try:
            report = run_batch_preflight(request, adapter=self.adapter)
        finally:
            self.preflight_button.setEnabled(True)
        self.preflight_report = report
        self.preflight_text.setPlainText(_format_preflight_report(report))
        self.preflight_summary_label.setText("Ready to process." if not report.blockers else f"{len(report.blockers)} item(s) need attention.")
        self.set_spatial_intervention(report.blockers)
        self._update_run_button_enabled()
        self._publish_session_state(plan_status="ready")
        self._update_processing_density(ProcessingUiState.IDLE)

    def cancel_polygon_preflight(self) -> None:
        """Request cancellation at the next pure-core planning safe point."""
        self.preflight_cancel_event.set()
        self.cancel_preflight_button.setEnabled(False)
        # Reflect the request immediately; the worker may still be finishing
        # one catalog/network call, but the UI must never look unresponsive.
        self.preflight_button.setEnabled(True)
        self._update_run_button_enabled()
        self.preflight_report = None
        self._refresh_footprint_label()
        self._update_selected_polygon_layer_status()
        self.preflight_summary_label.setText("Cancelling Prerun after the current planning step...")
        thread = self.preflight_thread
        if thread is not None and thread.isRunning():
            thread.requestInterruption()
            # Repository/catalog inspection can be a blocking filesystem or
            # network call that cannot observe the Python callback.  Bound the
            # cancellation latency and terminate only this disposable
            # preflight worker; no scientific execution owns this thread.
            QTimer.singleShot(250, lambda: self._force_stop_preflight(thread))

    def _force_stop_preflight(self, thread: QThread) -> None:
        if thread is self.preflight_thread and thread.isRunning():
            thread.terminate()
            thread.wait(1000)
            self.preflight_thread = None
            self.preflight_worker = None
            self.cancel_preflight_button.setVisible(False)
            self.preflight_button.setEnabled(True)
            self.preflight_summary_label.setText("Prerun cancelled. Refresh inputs, then run Prerun Check again.")
            self.preflight_text.setPlainText("Prerun cancelled before the current planning operation completed.")
            self._update_processing_density(ProcessingUiState.IDLE)

    def _on_polygon_preflight_progress(self, message: str) -> None:
        if self._prerun_terminal:
            return
        self.preflight_summary_label.setText(f"Analyzing selected area... {message}")
        self.preflight_text.setPlainText(f"Prerun is running in the background.\nCurrent stage: {message}")

    def _on_polygon_preflight_complete(self, report: object) -> None:
        if self._prerun_terminal:
            return
        self._prerun_terminal = True
        self.preflight_report = report
        self.set_spatial_intervention(report.blockers)
        self.preflight_text.setPlainText(self._polygon_guided_review_text(report))
        self.preflight_summary_label.setText("Ready to process." if not report.blockers else f"{len(report.blockers)} item(s) need attention.")
        self._update_run_button_enabled()
        self._refresh_footprint_label()
        self._publish_session_state(plan_status="ready")
        self.preflight_button.setEnabled(True)
        self.cancel_preflight_button.setVisible(False)
        self._update_processing_density(ProcessingUiState.IDLE)

    def _on_polygon_preflight_failed(self, message: str) -> None:
        self._prerun_terminal = True
        cancelled = "cancelled" in message.lower()
        self.preflight_text.setPlainText(("Prerun cancelled." if cancelled else "PRERUN_FAILED: ") + ("" if cancelled else message))
        self.preflight_summary_label.setText("Prerun cancelled." if cancelled else "Prerun failed. Review the diagnostic artifact.")
        self.preflight_report = None
        self._update_run_button_enabled()
        self._publish_session_state()
        self.preflight_button.setEnabled(True)
        self.cancel_preflight_button.setVisible(False)
        self.cancel_preflight_button.setEnabled(False)
        self._update_processing_density(ProcessingUiState.FAILED if not cancelled else ProcessingUiState.IDLE)

    def _on_polygon_layer_changed(self, *_args: object) -> None:
        self._adopted_polygon_selection = None
        self.preflight_report = None
        self._update_selected_polygon_layer_status()
        self._publish_session_state()
        self.preflight_button.setEnabled(True)
        self.cancel_preflight_button.setVisible(False)
        self._update_processing_density(ProcessingUiState.FAILED)

    def _clear_preflight_thread(self) -> None:
        self.preflight_thread = None
        self.preflight_worker = None

    def run_batch(self) -> None:
        """Validate current inputs and launch from an immutable execution snapshot."""
        if self.preflight_report is None:
            self.run_preflight()
            if self.preflight_report is None:return
        if self._current_batch_mode() == "polygon":
            self._run_polygon_batch()
            return
        report = self.preflight_report
        if report.blockers:
            _set_status_badge(self.status_label, "FAILED", "Status: Failed - Prerun Check issues must be resolved before processing.")
            return
        try:
            current_request = self._build_batch_request(report.batch_folder)
            execution = prepare_batch_execution(current_request, report, profile=str(self.processing_profile_combo.currentText()))
            request = execution.request
        except BatchExecutionError as exc:
            _set_status_badge(self.status_label, "FAILED", f"Status: Failed - batch could not start: {exc}")
            return
        token=self._begin_logical_job()
        if token is False:return
        self._completed_job_summary=None
        self._active_processing_profile=str(self.processing_profile_combo.currentText())
        self._transition_processing_ui_state(ProcessingUiState.STARTING)
        selected = list(request.datasets)
        self.batch_items = []
        self._batch_items_by_dataset = {}
        self.failed_paths = []
        self.cancel_requested = False
        self.pause_requested = False
        self._mark_selected_files_queued()
        self.batch_results.clear()
        self.progress_bar.setValue(0)
        BatchPage._start_processing_clock(self)
        self.run_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.pause_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)
        self.retry_failed_button.setEnabled(False)
        self.retry_failed_button.setVisible(False)
        _set_status_badge(self.status_label, "RUNNING", f"Status: Running - {len(selected)} dataset(s).")
        self._processed_items = 0
        self._total_items = max(1, execution.logical_inputs)
        executor = BatchExecutor(adapter_factory=lambda: PyForestScanAdapter(execution_mode="pbm_backend"))
        try:
            guardrail = executor.guardrails(request)
        except BatchExecutionError as exc:
            _set_status_badge(self.status_label, "FAILED", f"Status: Failed - batch could not start: {exc}")
            self._finish_batch_run()
            return
        self.active_workers = guardrail.max_workers if guardrail.is_parallel else 1
        mode_label = guardrail.effective_mode.replace("_", " ")
        self.worker_status_label.setText("Processing capacity: Automatic")
        self.processing_confidence_label.setVisible(True)
        backend_label = PyForestScanAdapter(execution_mode="pbm_backend").selected_execution_backend().replace("_", " ")
        _set_status_badge(self.status_label, "RUNNING", f"Status: Running - {len(selected)} dataset(s) in {mode_label}. Execution backend: {backend_label}.")
        self.batch_thread = QThread(self)
        self.batch_worker = _BatchExecutionWorker(request, self._batch_control_state)
        self.batch_worker.moveToThread(self.batch_thread)
        self.batch_thread.started.connect(self.batch_worker.run)
        self.batch_worker.itemReady.connect(self._on_batch_item)
        self.batch_worker.jobReady.connect(self._on_batch_job_update)
        self.batch_worker.completed.connect(self._on_batch_complete)
        self.batch_worker.failed.connect(self._on_batch_failed)
        self.batch_worker.completed.connect(self.batch_thread.quit)
        self.batch_worker.failed.connect(self.batch_thread.quit)
        self.batch_thread.finished.connect(self.batch_worker.deleteLater)
        self.batch_thread.finished.connect(self.batch_thread.deleteLater)
        self.batch_thread.finished.connect(self._clear_batch_thread)
        self.batch_thread.start()
        self._transition_processing_ui_state(ProcessingUiState.RUNNING)

    def _run_polygon_batch(self) -> None:
        """Launch polygon work and terminate synchronous controller failures."""
        self._active_launch_attempt = None
        try:
            self._dispatch_polygon_batch()
        except Exception as exc:  # noqa: BLE001 - controller boundary preserves diagnostics and UI state.
            diagnostic = traceback.format_exc()
            append_attempt_stage(
                self._active_launch_attempt, "DISPATCH_FAILED",
                failure_domain="PLUGIN", code="DISPATCH_INTERNAL_ERROR",
                exception_type=type(exc).__name__, reason=str(exc), traceback=diagnostic,
            )
            append_attempt_stage(
                self._active_launch_attempt, "FAILED",
                failure_domain="PLUGIN", code="DISPATCH_INTERNAL_ERROR",
                exception_type=type(exc).__name__, reason=str(exc), traceback=diagnostic,
            )
            self._retain_recent_error(
                diagnostic, code="DISPATCH_INTERNAL_ERROR", category="PLUGIN",
                recommended_action="Open Diagnostics and report this internal launch error. Processing Engine repair is not required.",
                stage="plugin_dispatch",
            )
            _set_status_badge(self.status_label, "FAILED", "PyForestScan could not start this job because the plugin encountered an internal launch error.")
            self._finish_batch_run(ProcessingUiState.FAILED)

    def _dispatch_polygon_batch(self) -> None:
        report = self.preflight_report
        if report is None:
            return
        products = tuple(product.value for product in report.request.products)
        launch_attempt = create_launch_attempt(
            report.batch_folder,
            products,
            str(getattr(report, "plan_signature", "")),
        )
        self._active_launch_attempt = launch_attempt
        self._last_launch_heartbeat_ms = 0
        installation = verify_session_files_unchanged()
        if installation.status in {PLUGIN_MIXED_INSTALL, PLUGIN_CORRUPT}:
            append_attempt_stage(launch_attempt, "FAILED", reason=installation.message, failure_domain="PLUGIN_INSTALLATION")
            _set_status_badge(self.status_label, "FAILED", installation.message)
            self._retain_recent_error(
                installation.message,
                code=installation.status,
                category="PLUGIN_INSTALLATION",
                recommended_action="Reinstall the PyForestScan plugin ZIP and restart QGIS. Do not repair the Processing Engine.",
                stage="plugin_integrity",
            )
            return
        append_attempt_stage(launch_attempt, "TOKEN_RECEIVED", runtime_generation_id=getattr(report.request.runtime_token, "runtime_generation_id", ""))
        current_policy = default_source_local_policy_store().read()
        if getattr(getattr(report, "request", None), "spatial_policy", None) != current_policy:
            self.preflight_report = None
            self.run_preflight()
            report = self.preflight_report
            if report is None:
                append_attempt_stage(launch_attempt, "FAILED", reason="Prerun did not produce a report.")
                return
        if getattr(report, "blockers", ()):
            append_attempt_stage(launch_attempt, "FAILED", reason="Polygon Prerun Check has blockers.")
            _set_status_badge(self.status_label, "FAILED", "Status: Failed - polygon Prerun Check issues must be resolved before processing.")
            return
        try:
            runtime_validation: dict[str, dict[str, str]] = BackendService().processing_engine_service().validate_runtime_token_for_launch(
                report.request.runtime_token,
                tuple(product.value for product in report.request.products),
                report.batch_folder,
            )
        except Exception as exc:
            technical = getattr(exc, "technical_message", str(exc))
            code = getattr(exc, "code", "ENGINE_RUNTIME_TOKEN_MISMATCH")
            _set_status_badge(self.status_label, "REPAIR_REQUIRED", f"Processing Engine changed before launch: {technical}")
            self._retain_recent_error(technical, code=code, category="ENGINE", recommended_action="Run Prerun Check again; no scientific attempt started.", stage="runtime_prelaunch")
            append_attempt_stage(launch_attempt, "FAILED", reason=technical, code=code, failure_domain="PROCESSING_ENGINE")
            return
        append_attempt_stage(launch_attempt, "TOKEN_VALIDATED", runtime_generation_id=getattr(report.request.runtime_token, "runtime_generation_id", ""))
        append_attempt_stage(launch_attempt, "DISPATCH_VALIDATION_STARTED")
        record_polygon_dispatch_validation(report, runtime_validation, attempt_folder=launch_attempt.folder)
        append_attempt_stage(launch_attempt, "DISPATCH_VALIDATION_RECORDED", runtime_generation_id=getattr(report.request.runtime_token, "runtime_generation_id", ""))
        token=self._begin_logical_job()
        if token is False:
            append_attempt_stage(launch_attempt, "FAILED", reason="Logical job admission was denied.")
            return
        self._completed_job_summary=None
        self._active_processing_profile=str(self.processing_profile_combo.currentText())
        self._transition_processing_ui_state(ProcessingUiState.STARTING)
        selected = list(getattr(report, "selected_sources", ()))
        self.batch_items = []
        self._batch_items_by_dataset = {}
        self.failed_paths = []
        self.cancel_requested = False
        self.pause_requested = False
        self.batch_results.clear()
        self.progress_bar.setValue(0)
        BatchPage._start_processing_clock(self)
        self.run_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.pause_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)
        self.retry_failed_button.setEnabled(False)
        self.retry_failed_button.setVisible(False)
        self._processed_items = 0
        self._total_items = max(1, len(selected))
        self._polygon_progress = PolygonProgressProjection(
            total_datasets=self._total_items,
            total_products=len(report.request.products),
        )
        _set_status_badge(self.status_label, "RUNNING", f"Status: Starting - preparing {len(selected)} intersecting source(s) for background processing.")
        self.worker_status_label.setText("Background ownership: launching (step progress is estimated)")
        self.batch_thread = QThread(self)
        self.batch_worker = _PolygonBatchExecutionWorker(report, self._batch_control_state, launch_attempt)
        self.batch_worker.moveToThread(self.batch_thread)
        self.batch_thread.started.connect(self.batch_worker.run)
        self.batch_worker.itemReady.connect(self._on_batch_item)
        self.batch_worker.jobReady.connect(self._on_batch_job_update)
        self.batch_worker.progressUpdated.connect(getattr(self, "_on_polygon_progress", lambda _event: None))
        self.batch_worker.completed.connect(self._on_batch_complete)
        self.batch_worker.failed.connect(self._on_batch_failed)
        self.batch_worker.completed.connect(self.batch_thread.quit)
        self.batch_worker.failed.connect(self.batch_thread.quit)
        self.batch_thread.finished.connect(self.batch_worker.deleteLater)
        self.batch_thread.finished.connect(self.batch_thread.deleteLater)
        self.batch_thread.finished.connect(self._clear_batch_thread)
        append_attempt_stage(launch_attempt, "DISPATCH_STARTED", operation="Starting background Qt worker.")
        self.batch_thread.start()
        self._transition_processing_ui_state(ProcessingUiState.RUNNING)

    def _scientific_settings_kwargs(self) -> dict[str, object]:
        """Validate and return product parameters that affect scientific output."""
        def optional(spin: QDoubleSpinBox) -> float | None:
            return None if spin.value() <= 0 else spin.value()

        pai_max = optional(self.pai_max_height_spin)
        fhd_max = optional(self.fhd_max_height_spin)
        canopy_max = optional(self.canopy_max_height_spin)
        if pai_max is not None and pai_max <= self.pai_min_height_spin.value():
            raise BatchExecutionError("PAI maximum height must be greater than its minimum integration height.")
        if fhd_max is not None and fhd_max <= self.fhd_min_height_spin.value():
            raise BatchExecutionError("FHD maximum height must be greater than its minimum canopy height.")
        if canopy_max is not None and canopy_max <= self.canopy_threshold_spin.value():
            raise BatchExecutionError("Canopy Cover maximum height must be greater than its minimum height.")
        return {
            "canopy_cover_height_threshold": self.canopy_threshold_spin.value(),
            "canopy_cover_max_height": canopy_max,
            "canopy_cover_extinction_coefficient": self.canopy_extinction_spin.value(),
            "pad_beer_lambert_constant": self.pad_beer_lambert_spin.value(),
            "pad_drop_ground": self.pad_drop_ground_check.isChecked(),
            "pai_min_height": self.pai_min_height_spin.value(),
            "pai_max_height": pai_max,
            "fhd_min_height": self.fhd_min_height_spin.value(),
            "fhd_max_height": fhd_max,
            "rumple_min_height": optional(self.rumple_min_height_spin),
            "point_density_per_area": self.point_density_per_area_check.isChecked(),
            "voxel_stat_dimension": self.voxel_stat_dimension_combo.currentText().strip(),
            "voxel_stat_stat": self.voxel_stat_stat_combo.currentText().strip().lower(),
        }

    def _build_batch_request(self, batch_folder: Path | None = None, datasets: tuple[Path, ...] | None = None) -> BatchRequest:
        """Build a typed batch request from current UI state."""
        selected = tuple(datasets) if datasets is not None else tuple(self._selected_paths())
        output_folder = self.output_folder_edit.text().strip()
        if not selected:
            raise BatchExecutionError("Select at least one discovered file.")
        if not output_folder:
            raise BatchExecutionError("Choose an output folder.")
        products = tuple(product for product, check in self.product_checks.items() if check.isChecked())
        if not products:
            raise BatchExecutionError("Select at least one product.")
        settings = BatchProductSettings(
            products=products,
            grid_resolution=self.resolution_spin.value(),
            height_bin_size=self.height_bin_spin.value() if self.height_bin_spin.value() > 0 else None,
            chm_interpolation=self.chm_interpolation_combo.currentText(),
            **self._scientific_settings_kwargs(),
            stop_on_error=self.stop_on_error_check.isChecked(),
            load_outputs_into_qgis=True,
            execution_mode="automatic",
            max_workers=self.max_workers_spin.value(),
            confirm_large_parallel=True,
            skip_completed=self.skip_completed_check.isChecked(),
            retry_failed_only=self.retry_failed_only_check.isChecked(),
            overwrite_existing=self.overwrite_existing_check.isChecked(),
            preflight_acknowledged=True,
        )
        return BatchRequest(
            input_folder=Path(self.input_folder_edit.text().strip()),
            output_folder=Path(output_folder),
            recursive=self.recursive_check.isChecked(),
            datasets=selected,
            settings=settings,
            title="PyForestScan Batch",
            batch_folder=batch_folder,
            clip_bounds=self._folder_clip_bounds(),
        )

    def build_current_processing_request(self, batch_folder: Path | None = None):
        """Build the current immutable request through one mode-aware contract."""
        if self._current_batch_mode() == "polygon":
            request = self._build_polygon_batch_request()
            return replace(request, batch_folder=batch_folder) if batch_folder is not None else request
        return self._build_batch_request(batch_folder)

    def _build_polygon_batch_request(self) -> PolygonBatchRequest:
        folder = self.polygon_lidar_folder_edit.text().strip()
        output_folder = self.output_folder_edit.text().strip()
        if not folder:
            raise BatchExecutionError("Choose a LiDAR repository for Polygon Selection.")
        if not output_folder:
            raise BatchExecutionError("Choose an output folder.")
        products = tuple(product for product, check in self.product_checks.items() if check.isChecked())
        if not products:
            raise BatchExecutionError("Select at least one product.")
        settings = BatchProductSettings(
            products=products,
            grid_resolution=self.resolution_spin.value(),
            height_bin_size=self.height_bin_spin.value() if self.height_bin_spin.value() > 0 else None,
            chm_interpolation=self.chm_interpolation_combo.currentText(),
            **self._scientific_settings_kwargs(),
            stop_on_error=self.stop_on_error_check.isChecked(),
            load_outputs_into_qgis=True,
            execution_mode="automatic",
            max_workers=self.max_workers_spin.value(),
            confirm_large_parallel=True,
            skip_completed=self.skip_completed_check.isChecked(),
            retry_failed_only=self.retry_failed_only_check.isChecked(),
            overwrite_existing=self.overwrite_existing_check.isChecked(),
            preflight_acknowledged=True,
        )
        catalog_path = self._polygon_catalog_path()
        repository_crs_override = None
        try:
            selection = select_lidar_repository_path(folder)
            assignment = default_spatial_assignment_store().spatial_assignment_for(selection.normalized_path, selection.normalized_path)
            repository_crs_override = assignment.horizontal_crs if assignment is not None else None
        except Exception:
            repository_crs_override = None
        return PolygonBatchRequest(
            lidar_folder=Path(folder),
            output_folder=Path(output_folder),
            polygon=self._normalized_polygon_selection(),
            products=products,
            settings=settings,
            recursive=True,
            title="PyForestScan Polygon Batch",
            catalog_path=catalog_path,
            shared_execution_options=BatchExecutionOptions.from_batch_settings(settings),
            selection_mode=str(self.polygon_selection_mode_combo.currentData() or "automatic"),
            direct_header_fallback=self.polygon_direct_fallback_check.isChecked(),
            repository_crs_override=repository_crs_override,
            existing_index_path=Path(self.polygon_existing_index_edit.text().strip()) if self.polygon_existing_index_edit.text().strip() else None,
            spatial_policy=default_source_local_policy_store().read(),
            polygon_options=PolygonBatchOptions(
                exact_raster_mask=self.exact_raster_mask_check.isChecked(),
                mask_engine=str(self.mask_engine_combo.currentData() or "automatic"),
                all_touched=self.all_touched_mask_check.isChecked(),
                crop_to_polygon_extent=self.crop_to_polygon_extent_check.isChecked(),
                retain_unmasked_intermediate=self.retain_unmasked_intermediate_check.isChecked(),
                mask_failure_policy=str(self.mask_failure_policy_combo.currentData() or "fail_product"),
            ),
        )

    def _update_run_button_enabled(self) -> None:
        """Enable Process from continuous basic readiness; click performs final validation."""
        report = self.preflight_report
        polygon_mode = self._current_batch_mode() == "polygon"
        source_ready = bool(self.polygon_lidar_folder_edit.text().strip()) if polygon_mode else bool(self.input_folder_edit.text().strip())
        action = next_processing_action(
            engine_ready=self._engine_ready, source_ready=source_ready,
            area_required=polygon_mode, area_ready=(self._adopted_polygon_selection is not None),
            prerun_ready=report is not None,
            processing=self.processing_ui_state in {ProcessingUiState.STARTING, ProcessingUiState.RUNNING, ProcessingUiState.FINALIZING},
        )
        if hasattr(self, "next_action_label"):
            self.next_action_label.setText(f"Next action: {action}.")
        _apply_button_role(self.preflight_button, "primary" if action == "Run Prerun Check" else "neutral")
        _apply_button_role(self.run_button, "primary" if action == "Process LiDAR" else "neutral")
        if report is None:
            products=any(check.isChecked() for check in self.product_checks.values());output=bool(self.output_folder_edit.text().strip())
            source=source_ready
            self.run_button.setEnabled(products and output and source);self.resume_button.setEnabled(False);self.resume_button.setVisible(False);return
        if self._current_batch_mode() == "polygon":
            selected = getattr(report, "selected_sources", ()) if report is not None else ()
            blockers = getattr(report, "blockers", ()) if report is not None else ()
            enabled = bool(report and selected and not blockers)
            self.run_button.setEnabled(enabled)
            self.resume_button.setEnabled(False)
            self.resume_button.setVisible(False)
            return
        enabled = bool(report and report.files_to_process and not report.blockers)
        self.run_button.setEnabled(enabled)
        resumable = bool(report and report.manifest_path.exists() and (report.files_completed or report.files_to_retry or report.files_to_skip))
        self.resume_button.setEnabled(enabled and resumable)
        self.resume_button.setVisible(enabled and resumable)

    def _on_batch_complete(self, result: object) -> None:
        """Finalize UI state after a worker-thread batch completes."""
        self.latest_result = result
        outcome = str(getattr(result, "scientific_outcome", "FAILED"))
        self._last_durable_state = {"SUCCEEDED": "complete", "PARTIAL_SUCCESS": "partial_success", "CANCELLED": "interrupted"}.get(outcome, "failed")
        terminal = {"SUCCEEDED": ProcessingUiState.COMPLETE, "PARTIAL_SUCCESS": ProcessingUiState.COMPLETE_WITH_WARNING, "CANCELLED": ProcessingUiState.INTERRUPTED}.get(outcome, ProcessingUiState.FAILED)
        try:
            self._completed_job_summary=completed_job_summary(result,self.preflight_report,processing_profile=self._active_processing_profile)
            self.batch_items=list(getattr(result,"items",()))
            self._batch_items_by_dataset={str(getattr(item, "dataset_path", "")): item for item in self.batch_items}
            self._refresh_batch_results()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(100 if getattr(result, "items", ()) else 0)
            completion_badge = "READY" if terminal is ProcessingUiState.COMPLETE else ("WARNING" if terminal is ProcessingUiState.COMPLETE_WITH_WARNING else "FAILED")
            succeeded = int(getattr(result, "product_success_count", 0))
            failed = int(getattr(result, "product_failure_count", 0))
            skipped = int(getattr(result, "product_skipped_count", 0))
            failed_names = ", ".join(product.product for item in getattr(result, "items", ()) for product in getattr(item, "product_results", ()) if product.status == "FAILED")
            if outcome == "PARTIAL_SUCCESS":
                summary = f"Completed with issues - {succeeded} products succeeded; {failed} failed" + (f" ({failed_names})" if failed_names else "") + f"; {skipped} skipped. Successful outputs were preserved."
            elif outcome == "SUCCEEDED":
                completed_count = succeeded or getattr(result, "success_count", 0)
                noun = "product" if completed_count == 1 else "products"
                summary = f"Complete - {completed_count} {noun} created successfully."
            else:
                summary = f"{status_display_word(completion_badge)} - {succeeded or getattr(result, 'success_count', 0)} completed; {failed or getattr(result, 'failure_count', 0)} failed."
            _set_status_badge(self.status_label, completion_badge, f"Status: {summary}")
            self._set_batch_summary(result)
            self.open_batch_folder_button.setEnabled(True)
            self.open_batch_folder_button.setVisible(True)
            self.retry_failed_button.setEnabled(bool(self.failed_paths))
            self.retry_failed_button.setVisible(bool(self.failed_paths))
            self.batchCompleted.emit(result)
            self.batchCompletedForJob.emit(result,self._current_job_token)
        except Exception as exc:  # noqa: BLE001 - convenience finalization cannot retain the running lock.
            terminal = ProcessingUiState.COMPLETE_WITH_WARNING
            self._last_durable_state = "complete_with_warning"
            self._retain_recent_error(
                f"Post-job finalization failed after processing completed: {exc}",
                code="POST_JOB_FINALIZATION_FAILED",
                category="OUTPUT",
                recommended_action="Outputs are preserved. Refresh processing status or open diagnostics; do not rerun successful science.",
            )
            _set_status_badge(self.status_label, "WARNING", "Status: Complete with warning - outputs were created, but Mission Control could not finish a convenience action. Open Recent Error for details.")
        finally:
            self._finish_batch_run(terminal)

    def _on_batch_failed(self, message: str) -> None:
        """Display an executor-level batch failure."""
        self._last_durable_state = "failed"
        cancelled = "cancelled" in message.lower()
        try:
            if cancelled:
                self._last_durable_state = "interrupted"
                _set_status_badge(self.status_label, "WARNING", "Status: Cancelled - completed outputs were preserved.")
                return
            runtime_prelaunch = "ENGINE_RUNTIME_" in message or "runtime token" in message.lower()
            if runtime_prelaunch:
                code = next((item.rstrip(":") for item in message.split() if item.startswith("ENGINE_RUNTIME_")), "ENGINE_RUNTIME_TOKEN_MISMATCH")
                self._retain_recent_error(message, code=code, category="ENGINE", recommended_action="Run Prerun Check again; no scientific attempt started.", stage="runtime_prelaunch")
                _set_status_badge(self.status_label, "REPAIR_REQUIRED", f"Status: Processing Engine changed before launch - no scientific attempt started. {message}")
            else:
                self._retain_recent_error(message)
                _set_status_badge(self.status_label, "FAILED", f"Status: Failed - batch could not start: {message}")
        finally:
            self._finish_batch_run(ProcessingUiState.INTERRUPTED if cancelled else ProcessingUiState.FAILED)

    def _finish_batch_run(self, terminal_state: ProcessingUiState = ProcessingUiState.FAILED) -> None:
        """Restore controls after a batch worker exits."""
        self._transition_processing_ui_state(terminal_state)
        self.pause_requested = False
        self.pause_button.setText("Pause After Current Step")
        self.active_workers = 0
        self.worker_status_label.setText("Processing capacity: Automatic")
        self.processing_confidence_label.setVisible(False)
        timer = getattr(self, "_processing_elapsed_timer", None)
        if timer is not None:
            timer.stop()

    def _transition_processing_ui_state(self, state: ProcessingUiState) -> None:
        """Project one authoritative processing state onto all workflow controls."""
        self.processing_ui_state = state
        ready = self.preflight_report is not None
        policy = control_policy(state, ready_to_process=ready)
        self._set_workflow_inputs_enabled(policy.run_inputs_enabled)
        self.run_button.setEnabled(policy.process_enabled)
        if policy.run_inputs_enabled:
            self._update_run_button_enabled()
        self.pause_button.setEnabled(policy.pause_enabled)
        self.pause_button.setVisible(policy.pause_enabled)
        self.cancel_button.setEnabled(policy.cancel_enabled)
        self.cancel_button.setVisible(policy.cancel_enabled)
        self.refresh_processing_status_button.setVisible(state in {ProcessingUiState.INTERRUPTED, ProcessingUiState.RECOVERABLE})
        self._update_processing_density(state)

    def _update_processing_density(self, state: ProcessingUiState | None = None) -> None:
        """Size Processing from the information useful in its current state."""
        state = state or self.processing_ui_state
        active = state in {
            ProcessingUiState.VALIDATING, ProcessingUiState.STARTING,
            ProcessingUiState.RUNNING, ProcessingUiState.PAUSED,
            ProcessingUiState.FINALIZING,
        }
        failed = state in {
            ProcessingUiState.FAILED, ProcessingUiState.INTERRUPTED,
            ProcessingUiState.RECOVERABLE,
        }
        terminal = state in {
            ProcessingUiState.COMPLETE, ProcessingUiState.COMPLETE_WITH_WARNING,
            ProcessingUiState.CANCELLED,
        }
        self.progress_bar.setVisible(active)
        self.engine_status_label.setVisible(active)
        self.worker_status_label.setVisible(active)
        self.summary_label.setVisible(active)
        self.processing_confidence_label.setVisible(False)
        self.result_filter_label.setVisible(False)
        self.result_filter_combo.setVisible(False)
        self.batch_results.setVisible(False)
        if state is ProcessingUiState.IDLE:
            if not self._engine_ready:
                _set_status_badge(self.status_label, "NOT CONFIGURED", "Processing Engine: Setup required")
            else:
                self.status_label.setText("Ready for Prerun" if self.preflight_report is None else "Prerun ready")
        elif terminal:
            self.recent_error_group.setVisible(False)
        elif failed:
            self.recent_error_group.setVisible(bool(self._recent_error_path))
        _refresh_layout_geometry(self.process_section)

    def _reconcile_processing_ui(self) -> None:
        launch = read_attempt_status(self._active_launch_attempt)
        # Polygon tile progress is the authoritative live message.  Launch
        # heartbeat reconciliation must not overwrite it on its timer tick,
        # which previously caused the visible two-message jump.
        if getattr(self, "_polygon_progress", None) is not None and self.batch_thread is not None and self.batch_thread.isRunning():
            return
        if self.batch_thread is not None and self.batch_thread.isRunning() and launch["stage"]:
            elapsed_seconds = int(launch["elapsed_ms"] / 1000)
            labels = {
                "DISPATCH_STARTED": "Starting background processing",
                "WORKER_STARTED": "Background worker owns the request",
                "REQUEST_SERIALIZATION_STARTED": "Preparing processing request",
                "SOURCE_PREPARATION_CHECK_STARTED": "Checking LiDAR source",
                "POLYGON_INPUT_PREPARATION_STARTED": "Preparing bounded LiDAR input",
                "FIRST_WORKER_STARTED": "Computing selected products",
                "FINALIZING": "Finalizing outputs",
            }
            current = labels.get(launch["stage"], launch["operation"] or launch["stage"].replace("_", " ").title())
            if launch["stalled"]:
                _set_status_badge(self.status_label, "WARNING", "Processing appears stalled before background ownership. Open Diagnostics or cancel the run.")
                self.worker_status_label.setText(f"Launch stalled - {elapsed_seconds} s without background ownership")
            else:
                self.worker_status_label.setText(f"{current} - {elapsed_seconds} s; background heartbeat active")
                if launch["elapsed_ms"] - self._last_launch_heartbeat_ms >= 5000:
                    append_attempt_stage(self._active_launch_attempt, "HEARTBEAT", active_stage=launch["stage"], elapsed_seconds=elapsed_seconds)
                    self._last_launch_heartbeat_ms = launch["elapsed_ms"]
        active = self.batch_thread is not None and self.batch_thread.isRunning()
        repaired = reconcile_ui_state(self.processing_ui_state, self._last_durable_state, coordinator_active=active)
        if repaired is not self.processing_ui_state:
            self._transition_processing_ui_state(repaired)

    def refresh_processing_status(self) -> None:
        """Re-read durable state and repair only the UI projection."""
        if self.latest_result is not None:
            try:
                self._completed_job_summary=completed_job_summary(self.latest_result,self.preflight_report,processing_profile=self._active_processing_profile)
                self.batch_items=list(getattr(self.latest_result,"items",()))
                self._refresh_batch_results();self._set_batch_summary(self.latest_result)
            except Exception as exc:  # noqa: BLE001 - recovery remains non-destructive.
                self._retain_recent_error(f"Current-job projection could not be rebuilt: {exc}",code="PROJECTION_RECOVERY_FAILED",category="RECOVERY")
        self._reconcile_processing_ui()

    def _retain_recent_error(self, message: str, *, code: str = "EXECUTION_FAILED", category: str = "PROCESS", recommended_action: str = "Review job diagnostics and retry when appropriate.", stage: str = "batch_terminal") -> None:
        folder = getattr(self.latest_result, "batch_folder", None)
        if folder is None and self.preflight_report is not None:
            folder = getattr(self.preflight_report, "batch_folder", None)
        if folder is None:
            return
        try:
            self._recent_error_path=write_recent_error(folder, DurableErrorRecord(code, category, "Processing needs attention.", str(message), stage, job_id=str(getattr(self._current_job_token, "logical_job_id", "")) if stage == "batch_terminal" else "", recommended_action=recommended_action))
            self.recent_error_label.setText(f"{code}: {message}")
            self.recent_error_group.setVisible(True)
        except OSError:
            pass

    def copy_recent_error_summary(self) -> None:
        if self._recent_error_path is None:return
        record=read_recent_error(self._recent_error_path.parents[1])
        if record is not None:QApplication.clipboard().setText(f"{record.code} [{record.category}] {record.user_message}\n{record.technical_message}\nAction: {record.recommended_action}")

    def open_recent_error_diagnostics(self) -> None:
        if self._recent_error_path is not None:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._recent_error_path.parent)))

    def _set_workflow_inputs_enabled(self, enabled: bool) -> None:
        """Freeze run-defining controls while one coordinator owns the job."""
        for section in (
            self.mode_section, self.repository_section, self.polygon_section,
            self.products_section, self.output_section, self.advanced_batch_section,
        ):
            section.setEnabled(enabled)
        self.preflight_button.setEnabled(enabled)

    def _clear_batch_thread(self) -> None:
        """Clear worker references after Qt has cleaned up the thread."""
        self.batch_thread = None
        self.batch_worker = None

    def _mark_selected_files_queued(self) -> None:
        """Mark selected discovered files as queued before execution starts."""
        products = ", ".join(PRODUCT_LABELS[product] for product, check in self.product_checks.items() if check.isChecked()) or "none"
        blocked = self.file_list.blockSignals(True)
        try:
            for index, path in enumerate(self.discovered_paths):
                item = self.file_list.item(index)
                if item is not None and item.checkState() == Qt.Checked:
                    item.setText(f"{path.name}\nStatus: queued; products: {products}\n{path}")
        finally:
            self.file_list.blockSignals(blocked)

    def _update_file_row(self, path: Path, status: str, bounds: str, message: str) -> None:
        """Update one discovered file row with current batch status."""
        for index, candidate in enumerate(self.discovered_paths):
            if candidate == path:
                item = self.file_list.item(index)
                if item is not None:
                    item.setText(f"{path.name}\nStatus: {status}; bounds: {bounds}\nMessage: {message}\n{path}")
                return

    def _selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for index, path in enumerate(self.discovered_paths):
            item = self.file_list.item(index)
            if item is not None and item.checkState() == Qt.Checked:
                paths.append(path)
        return paths

    def _set_all_files(self, selected: bool) -> None:
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            item.setCheckState(Qt.Checked if selected else Qt.Unchecked)
        self._refresh_footprint_label()

    def _refresh_footprint_label(self) -> None:
        if hasattr(self, "batch_mode_combo") and self._current_batch_mode() == "polygon":
            selected_count = len(getattr(self.preflight_report, "selected_sources", ())) if self.preflight_report is not None else 0
            selected_products = [product for product, check in self.product_checks.items() if check.isChecked()]
            products = [PRODUCT_LABELS[product] for product in selected_products]
            source_types = {getattr(source, "source_type", "unknown") for source in getattr(self.preflight_report, "selected_sources", ())} if self.preflight_report is not None else set()
            settings = BatchProductSettings(
                products=tuple(selected_products),
                grid_resolution=self.resolution_spin.value(),
                execution_mode="automatic",
                max_workers=self.max_workers_spin.value(),
                load_outputs_into_qgis=True,
                stop_on_error=self.stop_on_error_check.isChecked(),
                retry_failed_only=self.retry_failed_only_check.isChecked(),
                overwrite_existing=self.overwrite_existing_check.isChecked(),
                skip_completed=self.skip_completed_check.isChecked(),
            )
            concurrency = requested_effective_concurrency(BatchExecutionOptions.from_batch_settings(settings), source_types=source_types, product_count=max(1, len(selected_products)))
            self.footprint_label.setText(
                f"Polygon intersecting sources: {selected_count} after preflight\n"
                f"Selected products: {', '.join(products) if products else 'none'}\n"
                f"Shared grid resolution: {self.resolution_spin.value():g}\n"
                f"Processing strategy: Automatic (up to {concurrency['effective_concurrent_jobs']} isolated workers)\n"
                f"Exact raster mask: {'on' if self.exact_raster_mask_check.isChecked() else 'off'}; engine {self.mask_engine_combo.currentText()}\n"
                "Output loading: automatic after completion"
            )
            return
        selected_count = len(self._selected_paths())
        selected_products = [product for product, check in self.product_checks.items() if check.isChecked()]
        products = [PRODUCT_LABELS[product] for product in selected_products]
        warnings: list[str] = []
        if selected_count >= 10:
            warnings.append("Large batch: many files selected.")
        if selected_count * max(1, len(selected_products)) >= 30:
            warnings.append("Large workload: many file/product combinations selected.")
        if ProductType.PAD in selected_products:
            warnings.append("PAD storage depends on height-bin count and can be large.")
        warning_text = ("\nWarnings: " + " ".join(warnings)) if warnings else ""
        self.footprint_label.setText(
            f"Selected files: {selected_count}\n"
            f"Selected products: {', '.join(products) if products else 'none'}\n"
            f"Shared grid resolution: {self.resolution_spin.value():g}\n"
            "Raster dimensions and storage are estimated per file after inspection; summaries record observed output storage when files exist. "
            "Processing time depends on machine, storage speed, point density, and product selection."
            f"{warning_text}"
        )

    def _on_batch_item(self, item: object) -> None:
        dataset_path = Path(getattr(item, "dataset_path"))
        status = str(getattr(item, "status"))
        key = str(dataset_path)
        self._batch_items_by_dataset = getattr(self, "_batch_items_by_dataset", {})
        self._batch_items_by_dataset[key] = item
        self.batch_items = list(self._batch_items_by_dataset.values())
        terminal = {"completed", "failed", "cancelled", "skipped"}
        self._processed_items = sum(
            str(getattr(candidate, "status", "")) in terminal
            for candidate in self.batch_items
        )
        total = max(1, getattr(self, "_total_items", 1))
        if status == "running":
            # Running item notifications describe activity; they must not
            # reset the authoritative tile progress projected by
            # _on_polygon_progress.
            pass
        elif self._processed_items:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(min(99, int((self._processed_items / total) * 100)))
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
        dataset_name = Path(getattr(item, "dataset_path")).name
        self._processing_current_file = dataset_name
        message = getattr(item, "message")
        run_folder = getattr(getattr(item, "run_context"), "run_folder")
        bounds = getattr(item, "bounds_summary", "Unavailable")
        self._update_file_row(Path(getattr(item, "dataset_path")), status, getattr(item, "bounds_summary", "Unavailable"), message)
        if status in {"failed", "partial_success"}:
            self.failed_paths.append(Path(getattr(item, "dataset_path")))
        self._refresh_batch_results()
        _set_status_badge(self.status_label, "RUNNING", f"Status: Running - Datasets: {self._processed_items} / {total} complete.")

    def _on_polygon_progress(self, event: object) -> None:
        """Update current progress without creating dataset completion records."""
        if not isinstance(event, dict):
            return
        projection = getattr(self, "_polygon_progress", None)
        if projection is None or not projection.apply(event):
            return
        stage = str(event.get("active_stage") or event.get("stage") or "PROCESSING")
        message = str(event.get("message") or stage.replace("_", " ").title())
        elapsed = float(event.get("elapsed_seconds", 0) or 0)
        entity_id = str(event.get("entity_id", ""))
        if entity_id and event.get("entity_type") == "dataset":
            source = Path(entity_id)
            folder = getattr(getattr(self, "preflight_report", None), "batch_folder", source.parent)
            self._on_batch_item(BatchItemResult(
                source, batch_run_context(source, folder, reuse_existing=True),
                "running", message, (), stage,
            ))
        # Project the scheduler's durable tile state into the existing visible
        # Processing card. This is the authoritative path for both folder and
        # polygon workflows; it prevents the card from remaining at Starting.
        source_value = str(event.get("source") or entity_id or self._processing_current_file or "LiDAR source")
        self._processing_current_file = Path(source_value).name if source_value else "LiDAR source"
        product_value = str(event.get("product") or "")
        if not product_value:
            product_value = message.split(":", 1)[0] if ":" in message else "Processing"
        self._processing_current_product = product_value.replace("_", " ").title()
        current_id = str(event.get("current_work_unit_id") or "")
        completed = int(event.get("completed", 0) or 0)
        total = int(event.get("required_work_units", event.get("total", 0)) or 0)
        if "tile" in message.lower() or stage in {"Processing Regions", "WORK_UNIT_PROGRESS", "Processing tiles"}:
            step = f"Processing tile {min(total, completed + 1)} of {total}" if total else "Processing tiles"
        else:
            step = stage.replace("_", " ").title()
        self._refresh_processing_elapsed(step=step)
        # Keep the legacy indeterminate branch documented for compatibility;
        # live polygon work uses the determinate projection below.
        # self.progress_bar.setRange(0, 0)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(projection.progress_percent(event))
        active = int(event.get("running", 0) or 0)
        remaining = max(0, total - completed - int(event.get("failed", 0) or 0))
        eta = event.get("eta_seconds")
        eta_text = "Calculating" if eta is None else _compact_duration(float(eta))
        health = str(event.get("health") or "WORKING").replace("_", " ").title()
        self.worker_status_label.setText(
            f"{message}  |  {completed} of {total or projection.total_datasets or '?'} regions complete  |  "
            f"{active} regions processing  |  {remaining} remaining  |  "
            f"Elapsed {_compact_duration(elapsed)}  |  ETA {eta_text}  |  {health}"
        )
        self.processing_confidence_label.setVisible(True)
        _set_status_badge(self.status_label, "RUNNING", f"Status: Running - {projection.summary()}.")

    def _on_batch_job_update(self, job: JobRecord) -> None:
        title = str(getattr(job, "title", ""))
        marker = "PyForestScan Batch - "
        if title.startswith(marker):
            self._processing_current_file = title[len(marker):]
        message = str(getattr(getattr(job, "progress", None), "message", "") or job.status.value.title())
        products = tuple(getattr(job, "requested_products", ()) or ())
        completed_products = len(tuple(getattr(job, "pipeline_results", ()) or ()))
        if message.startswith("Processing "):
            self._processing_current_product = message.removeprefix("Processing ").split(" (", 1)[0]
        elif completed_products < len(products):
            self._processing_current_product = str(products[completed_products]).replace("_", " ").title()
        elif products:
            self._processing_current_product = str(products[-1]).replace("_", " ").title()
        percent = float(getattr(getattr(job, "progress", None), "percent", 0.0) or 0.0)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(max(0, min(99, int(percent))))
        self._refresh_processing_elapsed(step=message)
        self.jobUpdated.emit(job)
        self.jobUpdatedForJob.emit(job,self._current_job_token)

    def _start_processing_clock(self) -> None:
        self._processing_started_at = time.monotonic()
        self._processing_current_file = "Preparing dataset"
        self._processing_current_product = "Preparing products"
        timer = getattr(self, "_processing_elapsed_timer", None)
        if timer is not None:
            timer.start()
        if hasattr(self, "processing_detail_label"):
            self._refresh_processing_elapsed(step="Starting")

    def _refresh_processing_elapsed(self, *, step: str | None = None) -> None:
        started = self._processing_started_at
        elapsed = 0.0 if started is None else max(0.0, time.monotonic() - started)
        if step is None:
            step = str(self.processing_detail_label.property("currentStep") or "Processing")
        else:
            self.processing_detail_label.setProperty("currentStep", step)
        self.processing_detail_label.setText(
            f"Elapsed {_compact_duration(elapsed)}  |  Step: {step}  |  "
            f"File: {self._processing_current_file}  |  Product: {self._processing_current_product}"
        )

    def _batch_control_state(self) -> str | None:
        """Return pause/cancel state for the core batch runner."""
        if self.cancel_requested:
            return "cancel"
        if self.pause_requested:
            return "pause"
        return None

    def toggle_pause(self) -> None:
        """Pause or resume between batch files."""
        self.pause_requested = not self.pause_requested
        self.pause_button.setText("Resume" if self.pause_requested else "Pause After Current Step")
        _set_status_badge(self.status_label, "RUNNING", "Status: Running - current step will finish; products will not start until resumed." if self.pause_requested else "Status: Running - processing resumed.")

    def cancel_remaining(self) -> None:
        """Cancel files that have not started yet."""
        self.cancel_requested = True
        append_attempt_stage(self._active_launch_attempt, "CANCEL_REQUESTED", operation="Stopping active processing and owned child processes.")
        self.pause_requested = False
        self.pause_button.setText("Pause After Current Step")
        _set_status_badge(self.status_label, "RUNNING", "Status: Cancelling - stopping active processing.")

    def retry_failed_files(self) -> None:
        """Retry failed files from the last batch with current settings."""
        if not self.failed_paths:
            _set_status_badge(self.status_label, "WARNING", "Status: Needs review - no failed files are available to retry.")
            return
        self.discovered_paths = list(self.failed_paths)
        self.file_list.clear()
        for path in self.discovered_paths:
            row = QListWidgetItem(f"{path.name}\nStatus: retry queued; bounds: previous failure\n{path}")
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked)
            row.setSizeHint(QSize(0, 72))
            self.file_list.addItem(row)
        self.file_list.setVisible(bool(self.discovered_paths))
        self.file_empty_label.setVisible(not self.discovered_paths)
        _size_list_to_content(self.file_list, row_height=72)
        self.failed_paths = []
        self.retry_failed_button.setEnabled(False)
        self.retry_failed_button.setVisible(False)
        _set_status_badge(self.status_label, "WARNING", "Status: Needs review - failed files are queued for retry. Click Process LiDAR.")
        self._refresh_footprint_label()

    def open_batch_output_folder(self) -> None:
        """Open the latest batch folder, or the selected output root before a run."""
        folder = getattr(self.latest_result, "batch_folder", None)
        if folder is None:
            folder = Path(self.output_folder_edit.text().strip()) if self.output_folder_edit.text().strip() else None
        if folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _refresh_batch_results(self) -> None:
        """Refresh the visible batch result list using the selected filter."""
        selected_filter = self.result_filter_combo.currentText().lower() if hasattr(self, "result_filter_combo") else "all"
        self.batch_results.clear()
        products = (", ".join(self._completed_job_summary.requested_products) if self._completed_job_summary is not None else ", ".join(PRODUCT_LABELS[product] for product, check in self.product_checks.items() if check.isChecked())) or "none"
        for item in self.batch_items:
            status = getattr(item, "status")
            if selected_filter != "all" and status != selected_filter:
                continue
            dataset_name = Path(getattr(item, "dataset_path")).name
            message = getattr(item, "message")
            run_folder = getattr(getattr(item, "run_context"), "run_folder")
            bounds = getattr(item, "bounds_summary", "Unavailable")
            progress = "100%" if status == "completed" else ("0%" if status == "skipped" else "needs review")
            output_count = len(getattr(item, "outputs", ()))
            self.batch_results.addItem(
                f"{status.upper()} - {dataset_name}\n"
                f"Products: {products}\n"
                f"Progress: {progress}; outputs: {output_count}\n"
                f"Output folder: {run_folder}\n"
                f"Bounds: {bounds}\n"
                f"Message: {message}"
            )
        has_results = self.batch_results.count() > 0
        self.result_filter_label.setVisible(bool(self.batch_items))
        self.result_filter_combo.setVisible(bool(self.batch_items))
        self.batch_results.setVisible(has_results)
        _size_list_to_content(self.batch_results, row_height=94)

    def _set_batch_summary(self, result: object) -> None:
        """Display the completed batch summary."""
        summary=self._completed_job_summary or completed_job_summary(result,self.preflight_report,processing_profile=self._active_processing_profile)
        self._completed_job_summary=summary
        self.summary_label.setText(format_completed_job_summary(summary))


class ResultsPage(MissionPage):
    """Friendly report links and job history page."""

    outputsLoaded = pyqtSignal(str, int, int)
    currentRunCleared = pyqtSignal()
    goToBatchRequested = pyqtSignal()

    def __init__(self, iface: object | None = None, parent: QWidget | None = None) -> None:
        """Create the results page."""
        super().__init__("Results", parent)
        self.iface = iface
        self._friendly_paths: list[Path] = []
        self._advanced_paths: list[Path] = []
        self._job_result_paths: list[Path] = []
        self._job_result_types: dict[Path, str] = {}
        self._loaded_output_paths: set[Path] = set()
        self._current_output_folder: Path | None = None

        links = self.add_section("Generated Outputs")
        self.results_empty_label = _body_label("No products have been generated yet.")
        links.addWidget(self.results_empty_label)
        self.go_to_batch_button = QPushButton("Go to Batch")
        self.go_to_batch_button.clicked.connect(self.goToBatchRequested.emit)
        _apply_button_role(self.go_to_batch_button, "primary")
        links.addWidget(self.go_to_batch_button)
        self.friendly_links = QListWidget()
        self.friendly_links.setMaximumHeight(180)
        links.addWidget(self.friendly_links)
        self.friendly_links.setVisible(False)
        button_row = QHBoxLayout()
        self.open_output_folder_button = QPushButton("Open Output Folder")
        self.open_output_folder_button.setEnabled(False)
        self.open_output_folder_button.clicked.connect(self.open_output_folder)
        _apply_button_role(self.open_output_folder_button, "secondary")
        self.load_outputs_button = QPushButton("Load into QGIS")
        self.load_outputs_button.setEnabled(False)
        self.load_outputs_button.setToolTip("Load GeoTIFF and CSV outputs into the current QGIS project.")
        self.load_outputs_button.clicked.connect(self.load_outputs_to_qgis)
        _apply_button_role(self.load_outputs_button, "primary")
        self.refresh_results_button = QPushButton("Refresh Results")
        self.refresh_results_button.clicked.connect(self.refresh_results)
        _apply_button_role(self.refresh_results_button, "neutral")
        self.clear_current_run_button = QPushButton("Clear Current Run")
        self.clear_current_run_button.setEnabled(False)
        self.clear_current_run_button.clicked.connect(self.clear_current_run)
        _apply_button_role(self.clear_current_run_button, "danger")
        button_row.addWidget(self.open_output_folder_button)
        button_row.addWidget(self.load_outputs_button)
        button_row.addWidget(self.refresh_results_button)
        button_row.addWidget(self.clear_current_run_button)
        button_row.addStretch(1)
        links.addLayout(button_row)
        self.product_status_label = _body_label("No generated products.")
        links.addWidget(self.product_status_label)
        self.load_message_label = _body_label("")
        self.load_message_label.setVisible(False)
        links.addWidget(self.load_message_label)

        jobs = self.add_section("Job History")
        self.jobs_section = jobs.parentWidget()
        self.job_history = QListWidget()
        jobs.addWidget(self.job_history)
        self.jobs_section.setVisible(False)

        advanced, advanced_layout = _collapsible_section(self.content_layout, "Processing Summary and Diagnostics", checked=False)
        advanced_layout.addWidget(_details_label("Internal JSON, CSV, HTML reports, and logs are available here for reproducibility and troubleshooting."))
        row = QHBoxLayout()
        self.report_path_edit = QLineEdit()
        self.report_path_edit.setPlaceholderText("Choose JSON, CSV, or HTML report")
        browse = QPushButton("Browse")
        browse.clicked.connect(self.browse_report)
        open_button = QPushButton("Open")
        open_button.clicked.connect(self.open_report)
        row.addWidget(self.report_path_edit)
        row.addWidget(browse)
        row.addWidget(open_button)
        advanced_layout.addLayout(row)
        self.previous_reports = QListWidget()
        self.previous_reports.setMaximumHeight(120)
        advanced_layout.addWidget(self.previous_reports)
        _wire_collapsible_group(advanced)
        self._sync_compact_visibility(False)

    def _sync_compact_visibility(self, has_outputs: bool) -> None:
        """Keep empty Results content small and reveal actions only when useful."""
        self.results_empty_label.setVisible(not has_outputs)
        self.go_to_batch_button.setVisible(not has_outputs)
        self.friendly_links.setVisible(has_outputs and bool(self._friendly_paths))
        self.product_status_label.setVisible(has_outputs)
        self.open_output_folder_button.setVisible(has_outputs)
        self.load_outputs_button.setVisible(has_outputs)
        self.clear_current_run_button.setVisible(has_outputs)

    def set_project_summary(self, summary: ProjectSummary) -> None:
        """Display generated, loaded, and available product state."""
        generated = ", ".join(item.label for item in summary.generated_products) or "None"
        loaded = ", ".join(item.label for item in summary.loaded_products) or "None"
        available = ", ".join(item.label for item in summary.available_products) or "None"
        missing = ", ".join(item.label for item in summary.missing_products) or "None"
        if summary.generated_products:
            self.product_status_label.setText(
                f"Generated: {generated}   Loaded: {loaded}   Ready to load: {available}"
                + (f"   Missing: {missing}" if summary.missing_products else "")
            )
        else:
            self.product_status_label.setText("No generated products.")
        self._sync_compact_visibility(bool(summary.generated_products or self._friendly_paths or self._job_result_paths))

    def loaded_output_paths(self) -> tuple[Path, ...]:
        """Return outputs loaded through the Results page in this session."""
        return tuple(self._loaded_output_paths)

    def begin_current_job(self) -> None:
        """Clear current display without touching historical durable jobs."""
        self.set_run_context(None);self.job_history.clear();self.jobs_section.setVisible(False)

    def refresh_results(self) -> None:
        """Refresh Results button states from current paths without clearing outputs."""
        candidates = self._candidate_output_paths()
        loadable = collect_loadable_outputs(tuple(path for path in candidates if path.exists() and path.is_file()), self._job_result_types)
        has_paths = bool(candidates)
        self.friendly_links.setVisible(bool(self._friendly_paths))
        self.results_empty_label.setVisible(not self._friendly_paths)
        self.open_output_folder_button.setEnabled(self._current_output_folder is not None and self._current_output_folder.exists())
        self.load_outputs_button.setEnabled(bool(loadable))
        self.clear_current_run_button.setEnabled(has_paths or self._current_output_folder is not None)
        self._sync_compact_visibility(has_paths)
        self._set_load_message("Results refreshed." if has_paths else "Results refreshed. No current outputs found.")

    def set_run_context(self, context: RunContext | None) -> None:
        """Display friendly run links for the active context."""
        self.friendly_links.clear()
        self.previous_reports.clear()
        self._friendly_paths = []
        self._advanced_paths = []
        self._job_result_paths = []
        self._job_result_types = {}
        self._current_output_folder = None
        self.open_output_folder_button.setEnabled(False)
        self.load_outputs_button.setEnabled(False)
        self.clear_current_run_button.setEnabled(False)
        self.friendly_links.setVisible(False)
        self.results_empty_label.setVisible(True)
        self.load_message_label.setVisible(False)
        if context is None:
            self._loaded_output_paths = set()
            self._sync_compact_visibility(False)
            return
        self._current_output_folder = context.outputs_dir
        self.clear_current_run_button.setEnabled(True)
        for label, path in context.friendly_links:
            self._friendly_paths.append(path)
            self.friendly_links.addItem(f"{label}: {path}")
        has_outputs = bool(self._friendly_paths)
        self.friendly_links.setVisible(has_outputs)
        self.results_empty_label.setVisible(not has_outputs)
        self.open_output_folder_button.setEnabled(has_outputs)
        self.load_outputs_button.setEnabled(has_outputs)
        self._sync_compact_visibility(has_outputs)
        for label, path in context.advanced_paths:
            self._advanced_paths.append(path)
            self.previous_reports.addItem(f"{label}: {path}")

    def browse_report(self) -> None:
        """Choose a report file."""
        path, _ = QFileDialog.getOpenFileName(self, "Choose report", "", "Reports (*.json *.csv *.html);;All files (*.*)")
        if path:
            self.report_path_edit.setText(path)
            self.previous_reports.addItem(path)
            self._advanced_paths.append(Path(path))

    def set_report_paths(self, paths: tuple[Path, ...]) -> None:
        """Display recently recorded report paths in advanced details."""
        for path in paths:
            text = str(path)
            if _is_friendly_result_path(path) and path not in self._friendly_paths:
                self._friendly_paths.append(path)
                label = _friendly_result_label(path)
                self.friendly_links.addItem(f"{label}: {path}")
                self.friendly_links.setVisible(True)
                self.results_empty_label.setVisible(False)
                self.open_output_folder_button.setEnabled(self._current_output_folder is not None)
                self.load_outputs_button.setEnabled(True)
                self.clear_current_run_button.setEnabled(True)
                self._sync_compact_visibility(True)
            if not any(self.previous_reports.item(index).text().endswith(text) for index in range(self.previous_reports.count())):
                self.previous_reports.addItem(text)
                self._advanced_paths.append(path)

    def set_jobs(self, jobs: tuple[JobRecord, ...]) -> None:
        """Display job history."""
        self.job_history.clear()
        self._job_result_paths = []
        self._job_result_types = {}
        self.jobs_section.setVisible(bool(jobs))
        for job in jobs:
            detail = f"{job.title} - {job.status.value} - {job.progress.percent:.0f}%"
            if job.status == JobStatus.COMPLETED:
                for result in job.results:
                    self._job_result_paths.append(result.path)
                    self._job_result_types[result.path] = result.result_type
            if job.results:
                detail = f"{detail} - {job.results[-1].path}"
            self.job_history.addItem(detail)
        self.load_outputs_button.setEnabled(bool(self._candidate_output_paths()))
        self._sync_compact_visibility(bool(self._candidate_output_paths()))

    def load_outputs_to_qgis(self, primary_only: bool = False) -> None:
        """Load current run GeoTIFF and CSV outputs into QGIS without duplicates."""
        self.load_outputs_button.setEnabled(False)
        self._set_load_message("Loading outputs into QGIS...")
        QApplication.processEvents()
        paths = [path for path in self._candidate_output_paths() if path.exists() and path.is_file()]
        all_candidates = collect_loadable_outputs(paths, self._job_result_types, primary_only=primary_only)
        existing_sources = tuple(self._loaded_output_paths) + self._project_layer_sources()
        candidates = collect_loadable_outputs(paths, self._job_result_types, existing_sources, primary_only=primary_only)
        already_loaded = max(0, len(all_candidates) - len(candidates))
        if not candidates:
            message = output_loading_summary(0, len(all_candidates), already_loaded_count=already_loaded)
            self._set_load_message(message)
            self.load_outputs_button.setEnabled(bool(all_candidates))
            self.outputsLoaded.emit(message, 0, len(all_candidates))
            return
        if self.iface is None:
            message = output_loading_summary(0, len(candidates), already_loaded_count=already_loaded, failed_count=len(candidates))
            self._set_load_message("QGIS interface unavailable.\n" + message)
            self.load_outputs_button.setEnabled(True)
            self.outputsLoaded.emit("QGIS interface unavailable.", 0, len(candidates))
            return
        loaded = 0
        for output in candidates:
            if self._load_output(output):
                self._loaded_output_paths.add(output.path)
                loaded += 1
        failed = max(0, len(candidates) - loaded)
        message = output_loading_summary(loaded, len(all_candidates), already_loaded_count=already_loaded, failed_count=failed)
        self._set_load_message(message)
        self.load_outputs_button.setEnabled(bool(self._candidate_output_paths()))
        self.outputsLoaded.emit(message, loaded, len(candidates))

    def _candidate_output_paths(self) -> tuple[Path, ...]:
        """Return explicitly registered current-job paths that may be loadable."""
        paths: list[Path] = []
        paths.extend(self._friendly_paths)
        paths.extend(self._advanced_paths)
        paths.extend(self._job_result_paths)
        return tuple(dict.fromkeys(paths))

    def load_specific_outputs_to_qgis(self, paths: object) -> None:
        """Load a historical registry entry without changing current-job identity."""
        all_candidates = collect_loadable_outputs(tuple(Path(path) for path in paths), primary_only=True)
        existing = tuple(self._loaded_output_paths) + self._project_layer_sources()
        candidates = collect_loadable_outputs(tuple(output.path for output in all_candidates), existing_sources=existing, primary_only=True)
        loaded = 0
        for output in candidates:
            if self._load_output(output):
                self._loaded_output_paths.add(output.path)
                loaded += 1
        message = output_loading_summary(loaded,len(all_candidates),already_loaded_count=max(0,len(all_candidates)-len(candidates)),failed_count=max(0,len(candidates)-loaded))
        self.outputsLoaded.emit(message,loaded,len(all_candidates))

    def _project_layer_sources(self) -> tuple[str, ...]:
        """Return existing QGIS layer source paths, if QGIS APIs are available."""
        try:
            from qgis.core import QgsProject

            layers = QgsProject.instance().mapLayers().values()
        except Exception:  # noqa: BLE001 - tests and some QGIS states may not expose QgsProject.
            return ()
        sources: list[str] = []
        for layer in layers:
            source = getattr(layer, "source", None)
            if callable(source):
                try:
                    sources.append(str(source()))
                except Exception:  # noqa: BLE001 - one bad layer should not block loading.
                    continue
        return tuple(sources)

    def _load_output(self, output: LoadableOutput) -> bool:
        """Load one output into QGIS and apply product styling when relevant."""
        layer_name = self._output_layer_name(output)
        try:
            if output.layer_kind == "raster":
                layer = self.iface.addRasterLayer(str(output.path), layer_name)
                if layer is not None:
                    apply_generated_raster_renderer(layer, output.result_type)
            else:
                layer = self.iface.addVectorLayer(str(output.path), layer_name, "ogr")
        except Exception:  # noqa: BLE001 - loading feedback should stay concise.
            return False
        return layer is not None

    def _output_layer_name(self, output: LoadableOutput) -> str:
        """Return a readable QGIS layer name for a loadable output."""
        if output.layer_kind == "raster":
            dataset_stem = output.path.parent.parent.name if output.path.parent.name == "outputs" else output.path.stem
            return layer_display_name(output.result_type, dataset_stem)
        return _friendly_result_label(output.path)

    def _set_load_message(self, message: str) -> None:
        self.load_message_label.setText(message)
        self.load_message_label.setVisible(True)

    def open_selected_link(self) -> None:
        """Open the selected friendly result link."""
        row = self.friendly_links.currentRow()
        if 0 <= row < len(self._friendly_paths):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._friendly_paths[row])))

    def open_output_folder(self) -> None:
        """Open the current run output folder."""
        if self._current_output_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current_output_folder)))

    def clear_current_run(self) -> None:
        """Clear current run links from the Results page."""
        self.friendly_links.clear()
        self.previous_reports.clear()
        self.job_history.clear()
        self._friendly_paths = []
        self._advanced_paths = []
        self._job_result_paths = []
        self._job_result_types = {}
        self._loaded_output_paths = set()
        self._current_output_folder = None
        self.open_output_folder_button.setEnabled(False)
        self.load_outputs_button.setEnabled(False)
        self.clear_current_run_button.setEnabled(False)
        self.friendly_links.setVisible(False)
        self.results_empty_label.setVisible(True)
        self.jobs_section.setVisible(False)
        self.currentRunCleared.emit()

    def open_report(self) -> None:
        """Open the selected advanced report with the desktop handler."""
        path = self.report_path_edit.text().strip()
        if not path and self.previous_reports.currentItem() is not None:
            text = self.previous_reports.currentItem().text()
            path = text.split(": ", 1)[-1]
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class SettingsPage(MissionPage):
    """Plugin settings page."""

    defaultOutputFolderChanged = pyqtSignal(object)
    backendStateChanged = pyqtSignal(str, str)
    processingEngineStateChanged = pyqtSignal(object)
    verifyEnvironmentRequested = pyqtSignal()
    openToolboxRequested = pyqtSignal()
    guidanceDetailsRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the settings page."""
        super().__init__("Tools & Setup", parent)
        defaults_group, defaults = _collapsible_section(self.content_layout, "Preferences", checked=False)
        form = QFormLayout()
        self.default_output_folder = QLineEdit()
        self.default_output_folder.editingFinished.connect(self.emit_default_output_folder)
        self.default_output_folder.setProperty("contextHelp", "Default folder where PyForestScan saves final scientific outputs and human-readable reports. Internal checkpoints and worker files use the managed workspace.")
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.default_output_folder)
        browse = QPushButton("Browse")
        browse.clicked.connect(self.browse_default_output_folder)
        browse.setProperty("contextHelp", "Choose the default output folder using the system folder picker.")
        folder_row.addWidget(browse)
        form.addRow("Default output folder", folder_row)
        self.fallback_crs_edit = QLineEdit()
        self.fallback_crs_edit.setReadOnly(True)
        self.fallback_crs_edit.setPlaceholderText("Not set")
        self.fallback_crs_edit.setProperty("contextHelp", semantic_help("tools.fallback_crs"))
        fallback_row = QHBoxLayout()
        fallback_row.addWidget(self.fallback_crs_edit, 1)
        self.choose_fallback_crs_button = QPushButton("Choose CRS")
        self.choose_fallback_crs_button.clicked.connect(self.choose_fallback_crs)
        self.choose_fallback_crs_button.setProperty("contextHelp", semantic_help("tools.fallback_crs"))
        self.clear_fallback_crs_button = QPushButton("Clear")
        self.clear_fallback_crs_button.clicked.connect(self.clear_fallback_crs)
        self.clear_fallback_crs_button.setProperty("contextHelp", "Clear the optional Fallback CRS preference. Valid source CRS metadata and saved source assignments are not changed.")
        fallback_row.addWidget(self.choose_fallback_crs_button)
        fallback_row.addWidget(self.clear_fallback_crs_button)
        form.addRow("Fallback CRS", fallback_row)
        defaults.addLayout(form)
        self.fallback_crs_explanation = _details_label(semantic_help("tools.fallback_crs"))
        defaults.addWidget(self.fallback_crs_explanation)
        self.default_output_preview_label = _details_label("Global preferences apply to new runs. Job-specific scientific settings stay on Process.")
        defaults.addWidget(self.default_output_preview_label)
        _wire_collapsible_group(defaults_group)

        # Session continuity remains an internal default, not a settings wall.
        self.remember_workspace_check = QCheckBox("Remember last workspace")
        self.remember_workspace_check.setChecked(True)
        self.remember_dataset_check = QCheckBox("Remember last dataset")
        self.remember_dataset_check.setChecked(True)
        self.remember_output_folder_check = QCheckBox("Remember last output folder")
        self.remember_output_folder_check.setChecked(True)
        self.auto_save_workspace_check = QCheckBox("Auto-save workspace state")
        self.auto_save_workspace_check.setChecked(True)
        self._maximum_recent_items = 10

        backend = self.add_section("Processing Engine")
        self.backend_service = BackendService()
        self.backend_install_running = False
        self.backend_install_thread: QThread | None = None
        self.backend_install_worker: _BackendInstallWorker | None = None
        self.backend_install_started_at: float | None = None
        self.backend_install_timer = QTimer(self)
        self.backend_install_timer.setInterval(1000)
        self.backend_install_timer.timeout.connect(self._refresh_backend_install_elapsed)
        self.backend_status_label = _body_label("")
        _set_status_badge(self.backend_status_label, "NOT CONFIGURED", readiness_status_text("NOT CONFIGURED", "Processing Engine: Setup required"))
        self.backend_location_label = _body_label("Backend Location: Unknown")
        self.backend_environment_label = _body_label("Environment Location: Unknown")
        self.backend_installed_version_label = _body_label("Installed Version: Not installed")
        self.backend_plugin_version_label = _body_label("Plugin Version: Unknown")
        self.backend_manifest_version_label = _body_label("Backend recipe version: Unknown")
        self.backend_python_label = _body_label("Python Version: Not detected")
        self.backend_pdal_label = _body_label("PDAL Version: Not detected")
        self.backend_dependency_label = _body_label("Verification: Not checked")
        self.zip_install_ready_label = _body_label("Plugin ZIP: not checked")
        self.backend_auto_install_ready_label = _body_label("Backend installer: Not checked")
        self.manual_dependency_setup_label = _body_label("Manual setup: Required until the backend is ready")
        self.qgis_compatibility_label = _body_label("QGIS compatibility: Not checked")
        self.backend_install_readiness_label = _body_label("Processing setup: Not checked")
        backend.addWidget(self.backend_status_label)
        backend.addWidget(self.manual_dependency_setup_label)

        self.backend_detail_group, backend_detail_layout = _collapsible_section(self.content_layout, "Details", checked=False)
        for label in (
            self.backend_dependency_label,
            self.qgis_compatibility_label,
            self.backend_install_readiness_label,
            self.zip_install_ready_label,
            self.backend_auto_install_ready_label,
            self.backend_location_label,
            self.backend_environment_label,
            self.backend_installed_version_label,
            self.backend_plugin_version_label,
            self.backend_manifest_version_label,
            self.backend_python_label,
            self.backend_pdal_label,
        ):
            backend_detail_layout.addWidget(label)
        _wire_collapsible_group(self.backend_detail_group)

        self.backend_install_progress_bar = QProgressBar()
        self.backend_install_progress_bar.setRange(0, 100)
        self.backend_install_progress_bar.setValue(0)
        backend.addWidget(self.backend_install_progress_bar)
        self.backend_install_stage_label = _body_label("Stage: Not running")
        self.backend_install_action_label = _body_label("Current step: None")
        self.backend_install_elapsed_label = _body_label("Elapsed time: 00:00")
        self.backend_install_message_label = _body_label("Latest message: No backend install is running.")
        self.backend_install_estimate_label = _body_label("Step progress is estimated.")
        for label in (
            self.backend_install_stage_label,
            self.backend_install_action_label,
            self.backend_install_elapsed_label,
            self.backend_install_message_label,
            self.backend_install_estimate_label,
        ):
            backend.addWidget(label)
        self._set_backend_progress_visible(False)

        install_availability = self.backend_service.install_availability()
        self.install_backend_button = QPushButton("Set Up Processing Engine")
        self.install_backend_button.setEnabled(install_availability.enabled)
        _apply_button_role(self.install_backend_button, "primary" if install_availability.enabled else "neutral")
        if install_availability.enabled:
            self.install_backend_button.clicked.connect(self.install_backend_internal_beta)
        self.open_diagnostics_button = QPushButton("Open Diagnostics")
        self.open_diagnostics_button.clicked.connect(self.open_processing_engine_diagnostics)
        _apply_button_role(self.open_diagnostics_button, "neutral")
        self.recheck_backend_button = QPushButton("Recheck")
        self.recheck_backend_button.clicked.connect(self.verifyEnvironmentRequested.emit)
        self.recheck_backend_button.clicked.connect(self.refresh_backend_summary)
        _apply_button_role(self.recheck_backend_button, "neutral")

        self.backend_primary_buttons = QHBoxLayout()
        self.backend_primary_buttons.setSpacing(ACTION_ROW_SPACING)
        self.backend_primary_buttons.addWidget(self.install_backend_button)
        self.backend_primary_buttons.addWidget(self.recheck_backend_button)
        self.backend_primary_buttons.addStretch(1)
        backend.addLayout(self.backend_primary_buttons)

        backend_troubleshooting_actions = QHBoxLayout()
        backend_troubleshooting_actions.addWidget(self.open_diagnostics_button)
        backend_troubleshooting_actions.addStretch(1)
        backend_detail_layout.addLayout(backend_troubleshooting_actions)

        self.backend_details = QTextEdit()
        self.backend_details.setReadOnly(True)
        self.backend_details.setMinimumHeight(TECHNICAL_DETAIL_HEIGHT)
        self.backend_details.setMaximumHeight(140)
        self.backend_details.setPlainText("Processing Engine diagnostics appear here after Recheck or Open Diagnostics.")
        backend_detail_layout.addWidget(self.backend_details)
        self.backend_technical_log_group, technical_layout = _collapsible_section(
            backend_detail_layout, "Technical Log", checked=False
        )
        self.backend_technical_log = QTextEdit()
        self.backend_technical_log.setReadOnly(True)
        technical_layout.addWidget(self.backend_technical_log)
        _wire_collapsible_group(self.backend_technical_log_group)
        self.set_processing_engine_state(None)
        self._load_fallback_crs_preference()
        self.content_layout.addStretch(1)

    def _load_fallback_crs_preference(self) -> None:
        policy = default_source_local_policy_store().read()
        self.fallback_crs_edit.setText(policy.fallback_crs)
        self.clear_fallback_crs_button.setEnabled(bool(policy.fallback_crs))

    def choose_fallback_crs(self) -> None:
        """Select an optional fallback through QGIS's native CRS dialog."""
        try:
            from qgis.core import QgsCoordinateReferenceSystem
            from qgis.gui import QgsProjectionSelectionDialog
            dialog = QgsProjectionSelectionDialog(self)
            current = self.fallback_crs_edit.text().strip()
            if current:
                dialog.setCrs(QgsCoordinateReferenceSystem(current))
            execute = getattr(dialog, "exec", None) or getattr(dialog, "exec_", None)
            if not callable(execute) or not execute():
                return
            crs = dialog.crs()
            value = crs.authid() or crs.toWkt()
            if not crs.isValid() or not value:
                QMessageBox.warning(self, "Fallback CRS", "Choose a valid coordinate reference system.")
                return
        except Exception as exc:  # noqa: BLE001 - settings must fail gracefully outside QGIS.
            QMessageBox.warning(self, "Fallback CRS", f"The QGIS CRS selector is unavailable: {exc}")
            return
        store = default_source_local_policy_store()
        store.write(policy_with_fallback_crs(store.read(), value))
        self._load_fallback_crs_preference()

    def clear_fallback_crs(self) -> None:
        """Remove only the optional fallback CRS preference."""
        store = default_source_local_policy_store()
        store.write(policy_with_fallback_crs(store.read(), ""))
        self._load_fallback_crs_preference()

    def set_workspace_session(self, session: WorkspaceSession) -> None:
        """Display persisted workspace session preferences."""
        self.remember_workspace_check.setChecked(session.remember_last_workspace)
        self.remember_dataset_check.setChecked(session.remember_last_dataset)
        self.remember_output_folder_check.setChecked(session.remember_last_output_folder)
        self.auto_save_workspace_check.setChecked(session.auto_save_enabled)
        self._maximum_recent_items = session.maximum_recent_items

    def recent_item_display_limit(self) -> int:
        """Return the internal recent-workspace display bound, never a job limit."""
        return self._maximum_recent_items

    def current_processing_engine_state(self) -> object:
        """Return the lightweight cached/quick engine verification projection."""
        return self.backend_service.processing_engine_state(quick=True)

    def set_processing_engine_state(self, engine: object | None) -> None:
        """Project engine state without requiring callers to know page widgets."""
        if engine is None:
            status = "CHECKING"
            ready = False
            repair = False
            message = "Processing Engine status is being checked."
        else:
            status = str(getattr(getattr(engine, "status", None), "value", getattr(engine, "engine_status", "FAILED")))
            ready = bool(getattr(engine, "ready_for_processing", getattr(engine, "processing_available", False)))
            repair = bool(getattr(engine, "repair_needed", getattr(engine, "repair_required", False)))
            message = str(getattr(engine, "message", "Processing Engine status unavailable."))
        display = {
            "READY": "Ready", "CHECKING": "Checking", "SETUP_REQUIRED": "Setup required",
            "REPAIR_REQUIRED": "Needs repair", "INCOMPATIBLE": "Update required", "FAILED": "Unavailable",
        }.get(status, status.title())
        _set_status_badge(self.backend_status_label, status, f"Processing Engine: {display}")
        if ready:
            summary = "Processing Engine is configured for this PyForestScan version."
        elif repair:
            summary = "The Processing Engine needs attention. Open Diagnostics for details, then choose Repair / Reload."
        elif status in {"FAILED", "INCOMPATIBLE"}:
            summary = message
        elif status == "CHECKING":
            summary = "Mission Control is ready while Processing Engine status is checked."
        else:
            summary = "Set up the Processing Engine to install everything required for LiDAR processing."
        self.manual_dependency_setup_label.setText(summary)
        if not self.backend_install_running:
            action_visible, action_label = processing_engine_setup_action(status)
            self.install_backend_button.setVisible(action_visible)
            self.install_backend_button.setText(action_label)
            self.install_backend_button.setProperty("contextHelp", semantic_action_help(action_label))
            self.install_backend_button.setEnabled(action_visible and self.backend_service.install_availability().enabled)
            _apply_button_role(self.install_backend_button, "secondary" if ready else "primary")
        self.open_diagnostics_button.setVisible(repair or status in {"FAILED", "INCOMPATIBLE"})
        self.recheck_backend_button.setVisible(status != "CHECKING")

    def workspace_session_preferences(self, session: WorkspaceSession) -> WorkspaceSession:
        """Return session with settings-page workspace preferences applied."""
        return WorkspaceSession(
            last_opened_workspace=session.last_opened_workspace,
            last_selected_dataset=session.last_selected_dataset,
            last_output_folder=session.last_output_folder,
            last_planner_settings=session.last_planner_settings,
            last_selected_products=session.last_selected_products,
            last_page=session.last_page,
            window_geometry=session.window_geometry,
            floating=session.floating,
            docked=session.docked,
            remember_last_workspace=self.remember_workspace_check.isChecked(),
            remember_last_dataset=self.remember_dataset_check.isChecked(),
            remember_last_output_folder=self.remember_output_folder_check.isChecked(),
            maximum_recent_items=self._maximum_recent_items,
            auto_save_enabled=self.auto_save_workspace_check.isChecked(),
        )

    def browse_default_output_folder(self) -> None:
        """Choose the default output folder for Mission Control runs."""
        path = QFileDialog.getExistingDirectory(self, "Choose default output folder")
        if path:
            self.default_output_folder.setText(path)
            self.emit_default_output_folder()

    def emit_default_output_folder(self) -> None:
        """Emit the configured default output folder."""
        value = self.default_output_folder.text().strip()
        self.defaultOutputFolderChanged.emit(Path(value) if value else None)

    def refresh_backend_summary(self) -> None:
        """Display backend path, install-plan readiness, and detected state."""
        state = self.backend_service.detect_backend()
        engine = self.backend_service.processing_engine_state(quick=True)
        self.set_processing_engine_state(engine)
        paths = self.backend_service.paths
        registry = self.backend_service.get_registry()
        manifest = self.backend_service.backend_manifest()
        version = self.backend_service.version_compatibility()
        compatibility = build_qgis_compatibility_report()
        availability = self.backend_service.install_availability()
        self.backend_location_label.setText(f"Storage Location: {paths.backend_root}")
        self.backend_environment_label.setText(f"Environment Location: {paths.environment_path}")
        self.backend_installed_version_label.setText(f"Installed Version: {'configured' if state.config_exists else 'Not installed'}")
        self.backend_plugin_version_label.setText(f"Plugin Version: {self.backend_service.plugin_version}")
        self.backend_manifest_version_label.setText(f"Backend recipe version: {manifest.backend_version if manifest else 'Unavailable'}")
        required_count = len(registry.required_dependencies())
        total_count = len(registry.dependencies)
        self.backend_dependency_label.setText(f"Verification: {required_count} required checks; {total_count - required_count} optional checks")
        self.zip_install_ready_label.setText("Plugin ZIP: ready for QGIS Plugin Manager installs")
        auto_ready = availability.reason if availability.enabled else f"not available; {availability.reason}"
        self.backend_auto_install_ready_label.setText(f"Backend installer: {auto_ready}")
        compat_text = version.message if version else "Backend recipe unavailable"
        self.qgis_compatibility_label.setText(f"QGIS compatibility: {compatibility.summary()}; backend {compat_text}")
        self.backend_install_readiness_label.setText(engine.message)
        if self.backend_install_running:
            return
        self.backend_details.setPlainText(format_system_summary(engine.status.value,self.backend_service.plugin_version,registry,engine.message))

    def verify_qgis_compatibility(self) -> None:
        """Display defensive QGIS compatibility details."""
        report = build_qgis_compatibility_report()
        self.qgis_compatibility_label.setText(f"QGIS compatibility: {report.summary()}")
        self.backend_details.setPlainText(format_qgis_compatibility_report(report))

    def preview_install_plan(self) -> None:
        """Display the dry-run backend installation plan."""
        plan = self.backend_service.preview_install_plan()
        availability = self.backend_service.install_availability()
        self.backend_install_readiness_label.setText(f"Processing Engine setup: {availability.reason}; {len(plan.required_package_names())} packages planned")
        self.backend_details.setPlainText(self.backend_service.format_install_plan(plan))

    def install_backend_internal_beta(self) -> None:
        """Run the managed engine installer on an enabled platform."""
        availability = self.backend_service.install_availability()
        if not availability.enabled:
            self.backend_details.setPlainText(f"Install Backend is not available for this platform.\n\n{availability.reason}")
            return
        message = (
            "This will set up all PyForestScan processing components in your user-local PyForestScan folder. "
            "It will not modify QGIS or system Python.\n\n"
            f"Backend folder: {self.backend_service.paths.backend_root}\n"
            "The installer downloads Micromamba, creates the backend, verifies it, and writes settings only under that folder."
        )
        current = self.current_processing_engine_state()
        action = processing_engine_setup_action(current.status.value)[1]
        reply = QMessageBox.question(
            self,
            action,
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            self.backend_details.setPlainText("Backend installation canceled before any installer action was started.")
            return
        self._start_backend_install_worker()

    def _start_backend_install_worker(self) -> None:
        """Start PBM installation on a background Qt worker thread."""
        if self.backend_install_running:
            self.backend_details.setPlainText("Installation is running. Please wait for this step to finish.")
            return
        self.backend_install_thread = QThread(self)
        self.backend_install_worker = _BackendInstallWorker(self.backend_service)
        self.backend_install_worker.moveToThread(self.backend_install_thread)
        self.backend_install_thread.started.connect(self.backend_install_worker.run)
        self.backend_install_worker.progressUpdated.connect(self._on_backend_install_progress)
        self.backend_install_worker.completed.connect(self._on_backend_install_complete)
        self.backend_install_worker.failed.connect(self._on_backend_install_failed)
        self.backend_install_worker.completed.connect(self.backend_install_thread.quit)
        self.backend_install_worker.failed.connect(self.backend_install_thread.quit)
        self.backend_install_thread.finished.connect(self.backend_install_worker.deleteLater)
        self.backend_install_thread.finished.connect(self.backend_install_thread.deleteLater)
        self.backend_install_thread.finished.connect(self._clear_backend_install_thread)
        self._set_backend_install_running(True)
        self.backend_install_thread.start()

    def _set_backend_install_running(self, running: bool) -> None:
        """Disable install/repair/update-style controls while PBM installation runs."""
        self.backend_install_running = running
        if running:
            self._set_backend_progress_visible(True)
            self.backend_install_started_at = time.monotonic()
            self.backend_install_timer.start()
            _set_status_badge(self.backend_status_label, "RUNNING", "Processing Engine: Setting up")
            self.backend_install_progress_bar.setValue(5)
            self.backend_install_stage_label.setText("Stage: Preparing")
            self.backend_install_action_label.setText("Current step: preparing files")
            self.backend_install_message_label.setText("Latest message: Installation is running. Please wait for this step to finish.")
            self.backend_install_estimate_label.setText("Step progress is estimated.")
            self.backend_details.setPlainText(
                "Processing Engine setup is running in the background.\n\n"
                "Installation is running. Please wait for this step to finish.\n"
                "Step progress is estimated. Open Technical Log for detailed setup information."
            )
        else:
            self.backend_install_timer.stop()
            self.backend_install_started_at = None
        availability = self.backend_service.install_availability()
        for button in self._backend_install_action_buttons():
            button.setEnabled(not running)
        if not running:
            state = self.backend_service.processing_engine_state(quick=True)
            action_visible, action_label = processing_engine_setup_action(state.status.value)
            self.install_backend_button.setVisible(action_visible)
            self.install_backend_button.setText(action_label)
            self.install_backend_button.setEnabled(availability.enabled and action_visible)

    def _set_backend_progress_visible(self, visible: bool) -> None:
        """Show PBM progress UI only while it is useful."""
        for widget in (
            self.backend_install_progress_bar,
            self.backend_install_stage_label,
            self.backend_install_action_label,
            self.backend_install_elapsed_label,
            self.backend_install_message_label,
            self.backend_install_estimate_label,
        ):
            widget.setVisible(visible)

    def _backend_install_action_buttons(self) -> tuple[QPushButton, ...]:
        """Return controls disabled while install is running."""
        return (
            self.install_backend_button,
            self.open_diagnostics_button,
        )

    def _on_backend_install_progress(self, update: object) -> None:
        """Update visible staged progress from worker-thread installer updates."""
        percentage = getattr(update, "percentage", None)
        if percentage is not None:
            self.backend_install_progress_bar.setValue(int(percentage))
        stage = getattr(getattr(update, "stage", None), "value", getattr(update, "stage", "Unknown"))
        current = getattr(update, "current_package", "") or "current step"
        message = getattr(update, "message", "") or "Working..."
        estimate = getattr(update, "estimated_remaining_step", "") or "Step progress is estimated."
        self.backend_install_stage_label.setText(f"Stage: {stage}")
        self.backend_install_action_label.setText(f"Current step: {current}")
        self.backend_install_message_label.setText(f"Latest message: {message}")
        self.backend_install_estimate_label.setText(estimate)
        self._refresh_backend_install_elapsed()

    def _on_backend_install_complete(self, result: object) -> None:
        """Render final PBM install state after background worker completion."""
        self._set_backend_install_running(False)
        self._set_backend_progress_visible(True)
        self.refresh_backend_summary()
        status_value = getattr(getattr(result, "status", None), "value", str(getattr(result, "status", "Unknown")))
        success = bool(getattr(result, "ready_for_processing", False))
        if success:
            final_state = "Ready"
            self.backend_install_progress_bar.setValue(100)
        elif status_value == "Repair Required":
            final_state = "Repair Required"
        else:
            final_state = "Install Failed"
        _set_status_badge(self.backend_status_label, status_value, f"Processing Engine: {final_state}")
        self.backend_install_stage_label.setText(f"Stage: {final_state}")
        self.backend_install_message_label.setText(f"Latest message: {getattr(result, 'message', '')}")
        self.backend_details.setPlainText(
            "Processing Engine Setup Result\n\n"
            f"Final state: {final_state}\n"
            f"Ready for processing: {success}\n"
            f"Status: {status_value}\n"
            f"Log path: {getattr(result, 'log_path', None) or self.backend_service.paths.install_log}\n"
            f"Message: {getattr(result, 'message', '')}\n\n"
            "Open Technical Log for detailed setup and runtime information."
        )
        self._refresh_backend_technical_log()
        notice = "Processing Engine is ready." if success else "Processing Engine setup needs review."
        self.processingEngineStateChanged.emit(self.backend_service.processing_engine_state(quick=True))
        self.backendStateChanged.emit(status_value, notice)

    def _on_backend_install_failed(self, message: str) -> None:
        """Display unexpected installer worker failure."""
        self._set_backend_install_running(False)
        self._set_backend_progress_visible(True)
        _set_status_badge(self.backend_status_label, "FAILED", "Processing Engine: Setup failed")
        self.backend_install_stage_label.setText("Stage: Install Failed")
        self.backend_install_message_label.setText(f"Latest message: {message}")
        self.backend_details.setPlainText(
            "Processing Engine Setup Result\n\n"
            "Final state: Install Failed\n"
            f"Message: {message}\n\n"
            "Open Technical Log for detailed setup and runtime information."
        )
        self._refresh_backend_technical_log()
        self.processingEngineStateChanged.emit(self.backend_service.processing_engine_state(quick=True))
        self.backendStateChanged.emit("Failed", "Backend installation failed. Use View Logs for details.")

    def _refresh_backend_install_elapsed(self) -> None:
        """Update elapsed install time without implying exact step duration."""
        if self.backend_install_started_at is None:
            return
        elapsed = max(0, int(time.monotonic() - self.backend_install_started_at))
        minutes, seconds = divmod(elapsed, 60)
        self.backend_install_elapsed_label.setText(f"Elapsed time: {minutes:02d}:{seconds:02d}")

    def _refresh_backend_technical_log(self) -> None:
        """Load recent install logs into the hidden advanced log panel."""
        logs = self.backend_service.get_logs().get("install", ())
        self.backend_technical_log.setPlainText("\n".join(logs[-60:]) if logs else "No install log entries yet.")

    def _clear_backend_install_thread(self) -> None:
        """Clear backend install worker references after Qt cleanup."""
        self.backend_install_thread = None
        self.backend_install_worker = None

    def install_backend_experimental(self) -> None:
        """Backward-compatible wrapper for older tests and docs."""
        self.install_backend_internal_beta()


    def repair_backend_preview(self) -> None:
        """Compatibility wrapper: repair uses the same setup transaction."""
        self.install_backend_internal_beta()

    def open_processing_engine_diagnostics(self) -> None:
        """Show the one consolidated Processing Engine diagnostic report."""
        self.show_backend_advanced()
        self._refresh_backend_technical_log()

    def show_backend_advanced(self) -> None:
        """Display advanced PBM architecture details."""
        from ..core.build_identity import inspect_plugin_installation

        identity = inspect_plugin_installation()
        manifest = self.backend_service.backend_manifest()
        version = self.backend_service.version_compatibility()
        engine = self.backend_service.processing_engine_state(quick=True)
        token = getattr(engine, "runtime_token", None)
        required = [item.display_name for item in self.backend_service.registry.required_dependencies()]
        latest_attempt_path = self.backend_service.paths.backend_root / "diagnostics" / "latest_processing_attempt.json"
        try:
            latest_attempt = json.loads(latest_attempt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            latest_attempt = {}
        installation_label = {
            "PLUGIN_VALID": "Current",
            "PLUGIN_MIXED_INSTALL": "Mixed",
            "PLUGIN_CORRUPT": "Corrupt",
            "PLUGIN_UNKNOWN": "Unknown",
        }.get(identity.status, identity.status)
        lines = [
            "Processing Engine Diagnostics",
            "",
            "PLUGIN",
            f"Version: {identity.version}",
            f"Commit: {identity.git_commit}",
            f"Package build ID: {identity.build_id}",
            f"Package identity: {identity.package_identity}",
            f"Installed location: {identity.plugin_root}",
            f"Plugin installation: {installation_label}",
            identity.message,
            "",
            "PROCESSING ENGINE",
            f"Status: {getattr(getattr(engine, 'status', None), 'value', 'Unknown')}",
            f"Engine ID: {getattr(token, 'engine_id', 'Unavailable')}",
            f"Runtime generation: {getattr(token, 'runtime_generation_id', 'Unavailable')}",
            f"Plugin contract fingerprint: {getattr(token, 'plugin_build_id', 'Unavailable')}",
            f"Executable: {getattr(token, 'executable', self.backend_service.paths.python_executable)}",
            f"Installer availability: {'enabled' if self.backend_service.backend_install_enabled() else 'off'}",
            f"Manifest backend version: {manifest.backend_version if manifest else 'Unavailable'}",
            f"Manifest environment version: {manifest.environment_version if manifest else 'Unavailable'}",
            f"Version compatibility: {version.message if version else 'Unavailable'}",
            "",
            "CURRENT SESSION",
            f"Session identity: {self.backend_service.paths.backend_root / 'diagnostics' / 'plugin_session_identity.json'}",
            "",
            "LATEST PROCESSING ATTEMPT",
            f"Attempt ID: {latest_attempt.get('attempt_id', 'No attempt in this session')}",
            f"Clicked at: {latest_attempt.get('clicked_at', 'Unavailable')}",
            f"Package build ID: {latest_attempt.get('plugin_build_id', 'Unavailable')}",
            f"Outcome: {latest_attempt.get('outcome', 'Unavailable')}",
            f"Trace: {latest_attempt.get('attempt_path', 'Unavailable')}",
            "",
            "SETUP LOGS",
            f"- Install: {self.backend_service.paths.install_log}",
            f"- Download: {self.backend_service.paths.download_log}",
            f"- Verify: {self.backend_service.paths.verify_log}",
            f"- Repair: {self.backend_service.paths.repair_log}",
            "",
            "REQUIRED NOW",
            *[f"- {name}" for name in required],
        ]
        if token:
            compatible = bool(getattr(engine, "ready_for_processing", False))
            lines.extend((
                "", "ENGINE COMPATIBILITY", f"Status: {'Compatible' if compatible else 'Needs attention'}",
                "Compatibility authority: ProcessingEngineService runtime token validation.",
            ))
        if version and version.warnings:
            lines.extend(("", "SETUP INTEGRITY NOTES"))
            lines.extend(f"- {warning}" for warning in version.warnings)
        self.backend_details.setPlainText("\n".join(lines))


    def show_manual_setup_instructions(self) -> None:
        """Display current dependency setup guidance for clean ZIP installs."""
        self.backend_details.setPlainText(
            "Manual Setup Instructions\n\n"
            "ZIP installation installs only the QGIS plugin. PBM backend installation creates an isolated, user-local PyForestScan backend and does not install packages into QGIS Python, system Python, PATH, shell profiles, or QGIS folders.\n\n"
            "Current release status:\n"
            "- Plugin ZIP: ready for QGIS Plugin Manager installs.\n"
            "- Backend installer: Windows and Linux x86_64; Apple Silicon experimental.\n"
            "- Manual setup: not required after PBM backend installation verifies successfully; QGIS-Python-only tools still show their own requirements.\n\n"
            "Next steps:\n"
            "1. Install the ZIP through QGIS Plugin Manager.\n"
            "2. Open Mission Control and run Environment Check.\n"
            "3. Open Tools & Setup and click Set Up Processing Engine.\n"
            "4. Setup publishes Ready automatically when all checks pass.\n\n"
            "Reference docs: docs/INSTALLATION_STRATEGY.md, docs/releases/CLEAN_MACHINE_SMOKE_TEST.md, and docs/releases/PBM_INTERNAL_BETA_SMOKE_TEST.md."
        )

    def open_backend_folder(self) -> None:
        """Open the backend folder if it already exists, otherwise show the planned path."""
        folder = self.backend_service.open_backend_folder_path()
        if folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            self.backend_details.setPlainText(
                f"Backend folder does not exist yet: {folder}\n\n"
                "Backend installation will create only this user-local directory after confirmation. "
                "Use Preview Install to review the backend layout before installing."
            )

    def view_backend_logs(self) -> None:
        """Display recent PBM log lines if logs exist."""
        logs = self.backend_service.get_logs()
        lines = []
        for operation, entries in logs.items():
            lines.append(f"[{operation}]")
            lines.extend(entries or ("No log entries.",))
            lines.append("")
        self.backend_details.setPlainText("\n".join(lines).strip())

def register_context_help(widget: QWidget, text: str, owner: MissionPage) -> None:
    """Register explanation-only hover/focus behavior for one control."""
    resolved = str(text).strip()
    if not resolved:
        return
    widget.setProperty("resolvedContextHelp", resolved)
    if not widget.toolTip():
        widget.setToolTip(resolved)
    if not widget.accessibleName():
        label = widget.text() if isinstance(widget, QAbstractButton) else widget.objectName()
        widget.setAccessibleName(str(label or type(widget).__name__).replace("_", " ").strip())
    # Every numeric and choice control must let the page scroll unless the user
    # deliberately changes it by keyboard or pointer.  Registering this here
    # covers dynamically rebuilt scientific settings as well as initial UI.
    if isinstance(widget, (QComboBox, QAbstractSpinBox)):
        widget.setProperty("ignoreWheelValueChange", True)
    widget.installEventFilter(owner)


def _take_layout_widget(layout: object, widget: QWidget) -> None:
    """Detach one known widget while preserving Qt ownership for responsive placement."""
    if not hasattr(layout, "count"):
        return
    for index in range(layout.count() - 1, -1, -1):
        item = layout.itemAt(index)
        if item.widget() is widget:
            layout.takeAt(index)
            return


def _default_context_help(widget: QWidget) -> str:
    """Return structural help only; interactive controls require explicit semantics."""
    if isinstance(widget, QGroupBox) and widget.isCheckable():
        return f"Expand or collapse {widget.title()} settings."
    if isinstance(widget, QPushButton):
        return semantic_action_help(widget.text())
    return ""


def _processing_lifecycle_stage(job: JobRecord) -> str:
    """Return the common user-facing processing lifecycle stage."""
    if job.status in {JobStatus.PENDING, JobStatus.VALIDATING}:
        return "Preparing"
    if job.status is JobStatus.RUNNING:
        return "Generating Outputs" if job.results else "Running"
    if job.status is JobStatus.COMPLETED:
        return "Complete"
    if job.status is JobStatus.FAILED:
        return "Failed"
    if job.status is JobStatus.CANCELLING:
        return "Cancelling"
    if job.status is JobStatus.CANCELLED:
        return "Cancelled"
    return status_display_word(job.status.value)


def _find_backend_dependency(result: object, name: str):
    """Return one backend dependency from a verification result by name."""
    for dependency in result.registry.dependencies:
        if dependency.name == name:
            return dependency
    return None


def _format_preflight_report(report: BatchPreflightReport) -> str:
    """Format a batch preflight report for Mission Control."""
    lines = [
        "Ready to run: " + ("YES" if report.ready else "NO"),
        f"Input: {len(report.files_to_process)} LiDAR source(s)",
        f"Output: {report.batch_folder}",
        f"Estimated output storage: {_format_storage(report.estimated_output_bytes)}",
        f"Free disk space: {_format_storage(report.free_disk_bytes)}",
        "",
        "Blockers:",
    ]
    lines.extend(f"- {item}" for item in report.blockers)
    if not report.blockers:
        lines.append("- None")
    lines.append("")
    lines.append("Warnings:")
    lines.extend(f"- {item}" for item in report.warnings)
    if not report.warnings:
        lines.append("- None")
    return "\n".join(lines)


def _format_storage(value: int) -> str:
    """Return a compact storage label."""
    amount = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TB"


def _next_home_action(environment: str, dataset: str | None, batch_status: str) -> str:
    """Return a concise next action for the Home dashboard."""
    if environment == "Unknown":
        return "open Environment and refresh dependency checks."
    if environment == "NOT READY":
        return "review Environment warnings before processing."
    if "Running" in batch_status:
        return "monitor the Batch page until the current run finishes."
    if not dataset:
        return "start a single dataset workflow or open Batch for multiple files."
    return "build a Product Plan or run selected products from Processing."


def _processing_footprint_text(footprint: ProcessingFootprint) -> str:
    """Return concise user-facing processing footprint text."""
    product_lines = ", ".join(footprint.selected_products) or "none"
    warnings = "\n".join(f"Warning: {item}" for item in footprint.warnings)
    details = [
        f"Processing footprint: {product_lines}",
        f"Output folder: {footprint.output_folder or 'Unknown'}",
        f"Raster dimensions: {footprint.display_dimensions}",
        f"Raster bands: {footprint.total_bands}",
        f"Estimated output storage: {footprint.display_storage} ({footprint.confidence} confidence)",
        footprint.caveat,
    ]
    if warnings:
        details.append(warnings)
    return "\n".join(details)


def _apply_button_role(button: QPushButton, role: str | None = None) -> QPushButton:
    """Apply design-system button role metadata and native action icons."""
    requested = (role or button_role_for_label(button.text())).strip().lower()
    if requested not in {"primary", "secondary", "neutral", "danger"}:
        requested = button_role_for_label(button.text())
    if requested not in {"primary", "secondary", "neutral", "danger"}:
        requested = "secondary"
    button.setProperty("buttonRole", requested)
    _apply_action_icon(button)
    style = button.style()
    if style is not None:
        style.unpolish(button)
        style.polish(button)
    return button


def _apply_action_icon(button: QPushButton) -> None:
    """Prefer QGIS theme icons, then Qt standard icons for important actions."""
    intent = action_icon_intent(button.text())
    if not intent:
        return
    icon = _qgis_theme_icon(intent) or _qt_standard_icon(button, intent)
    if icon is not None and not icon.isNull():
        button.setIcon(icon)
        button.setIconSize(QSize(16, 16))


def _qgis_theme_icon(intent: str):
    """Return a QGIS theme icon when QGIS exposes one for the action intent."""
    candidates = {
        "cancel": ("/mActionCancel.svg", "/mIconClose.svg"),
        "clear": ("/mActionDeleteSelected.svg", "/mIconClearText.svg"),
        "folder": ("/mActionFileOpen.svg", "/mIconFolder.svg"),
        "forward": ("/mActionArrowRight.svg", "/mIconForward.svg"),
        "help": ("/mActionHelpContents.svg", "/mIconHelp.svg"),
        "install": ("/mActionInstallPlugin.svg", "/mIconPlugin.svg"),
        "load": ("/mActionAddRasterLayer.svg", "/mActionAddLayer.svg"),
        "log": ("/mActionOpenTable.svg", "/mIconTableLayer.svg"),
        "open": ("/mActionFileOpen.svg",),
        "preview": ("/mActionShowAllLayers.svg", "/mActionIdentify.svg"),
        "refresh": ("/mActionRefresh.svg",),
        "repair": ("/mActionToggleEditing.svg", "/mIconWarning.svg"),
        "run": ("/mActionStart.svg", "/mIconRun.svg"),
        "save": ("/mActionFileSave.svg",),
        "search": ("/mActionZoomIn.svg", "/mActionIdentify.svg"),
        "select": ("/mActionSelect.svg",),
        "settings": ("/mActionOptions.svg", "/mIconProperties.svg"),
        "verify": ("/mIconSuccess.svg", "/mActionIdentify.svg"),
        "inspect": ("/mActionIdentify.svg",),
    }.get(intent, ())
    try:
        from qgis.core import QgsApplication

        for name in candidates:
            icon = QgsApplication.getThemeIcon(name)
            if icon is not None and not icon.isNull():
                return icon
    except Exception:  # noqa: BLE001 - tests and non-QGIS contexts fall back to Qt icons.
        return None
    return None


def _qt_standard_icon(button: QPushButton, intent: str):
    """Return a Qt standard icon for action intents not covered by QGIS."""
    style = button.style()
    if style is None:
        return None
    pixmap_name = {
        "cancel": "SP_DialogCancelButton",
        "clear": "SP_DialogDiscardButton",
        "folder": "SP_DirOpenIcon",
        "forward": "SP_ArrowForward",
        "help": "SP_DialogHelpButton",
        "install": "SP_DriveHDIcon",
        "load": "SP_FileDialogNewFolder",
        "log": "SP_FileIcon",
        "open": "SP_DialogOpenButton",
        "preview": "SP_FileDialogContentsView",
        "refresh": "SP_BrowserReload",
        "repair": "SP_MessageBoxWarning",
        "run": "SP_MediaPlay",
        "save": "SP_DialogSaveButton",
        "search": "SP_FileDialogContentsView",
        "select": "SP_DialogApplyButton",
        "settings": "SP_FileDialogDetailedView",
        "verify": "SP_DialogApplyButton",
        "inspect": "SP_MessageBoxInformation",
    }.get(intent)
    pixmap = getattr(QStyle, pixmap_name, None) if pixmap_name else None
    return style.standardIcon(pixmap) if pixmap is not None else None


def _set_status_badge(label: QLabel, status: str, text: str | None = None) -> QLabel:
    """Apply design-system status badge metadata for stylesheet selectors."""
    badge = status_badge_label(status)
    label.setObjectName("statusBadge")
    label.setProperty("tone", status_badge_tone(badge))
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    label.setText(text or f"Status: {badge}")
    style = label.style()
    if style is not None:
        style.unpolish(label)
        style.polish(label)
    return label


class CompactCollapsibleSection(QWidget):
    """A native compact disclosure row whose hidden body contributes no height."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._checked = bool(checked)
        self.setProperty("compactCollapsible", True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header_button = QToolButton(self)
        self.header_button.setObjectName("compactCollapsibleHeader")
        self.header_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header_button.setCheckable(True)
        self.header_button.setChecked(self._checked)
        self.header_button.setAccessibleName(f"{title}, expandable section")
        self.header_button.setProperty("contextHelp", _collapsible_help_text(title))
        self.header_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header_button.setFixedHeight(28)
        self.header_button.clicked.connect(self.setChecked)
        outer.addWidget(self.header_button)
        self._content_widget = QWidget(self)
        self._content_widget.setObjectName("collapsibleContent")
        self._content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        outer.addWidget(self._content_widget)
        self._sync_header()

    def title(self) -> str:
        return self._title

    def isChecked(self) -> bool:  # noqa: N802 - compatibility with prior QGroupBox contract
        return self._checked

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt-style compatibility API
        checked = bool(checked)
        changed = checked != self._checked
        self._checked = checked
        self.header_button.blockSignals(True)
        self.header_button.setChecked(checked)
        self.header_button.blockSignals(False)
        self._sync_header()
        if changed:
            self.toggled.emit(checked)

    def _sync_header(self) -> None:
        self.header_button.setArrowType(Qt.DownArrow if self._checked else Qt.RightArrow)
        self.header_button.setText(self._title)


def _collapsible_help_text(title: str) -> str:
    if title == "Advanced Scientific Settings":
        return "Adjust optional scientific parameters for the products you selected."
    if title in {"Details", "Technical Details", "Technical Log"}:
        return "Show additional technical information about the current operation."
    return f"Show or hide {title.lower()}."


def _collapsible_section(parent: QVBoxLayout, title: str, checked: bool = False) -> tuple[CompactCollapsibleSection, QVBoxLayout]:
    """Add a compact disclosure section without overwriting child visibility."""
    group = CompactCollapsibleSection(title, checked)
    content = group._content_widget
    layout = QVBoxLayout(content)
    layout.setContentsMargins(SPACING_XS, SPACING_XS, SPACING_XS, SPACING_XS)
    layout.setSpacing(SECTION_SPACING)
    parent.addWidget(group)
    return group, layout


def _wire_collapsible_group(group: CompactCollapsibleSection) -> None:
    """Connect and apply visibility for a checkable section's content widgets."""
    _set_collapsible_content_visible(group, group.isChecked())
    group.toggled.connect(lambda checked: _set_collapsible_content_visible(group, checked))


def _set_collapsible_content_visible(group: CompactCollapsibleSection, visible: bool) -> None:
    content = getattr(group, "_content_widget", None)
    if isinstance(content, QWidget):
        content.setVisible(visible)
        if visible:
            _fit_collapsible_to_visible_content(group)
        else:
            content.setMaximumHeight(0)
            _refresh_layout_geometry(group)


def _fit_collapsible_to_visible_content(group: CompactCollapsibleSection) -> None:
    """Cap an expanded body at the live layout hint from currently visible rows."""
    content = getattr(group, "_content_widget", None)
    if not isinstance(content, QWidget):
        return
    layout = content.layout()
    if layout is not None:
        content.setMaximumHeight(16777215)
        layout.invalidate()
        layout.activate()
        content.setMaximumHeight(max(0, layout.sizeHint().height()))
    _refresh_layout_geometry(group)


def _refresh_layout_geometry(widget: QWidget) -> None:
    """Recalculate expandable content without caching a collapsed height."""
    layout = widget.layout()
    if layout is not None:
        layout.invalidate()
        layout.activate()
    widget.updateGeometry()
    parent = widget.parentWidget()
    if parent is not None:
        parent.updateGeometry()


def _set_layout_visible(layout: object, visible: bool) -> None:
    if not hasattr(layout, "count"):
        return
    for index in range(layout.count()):
        item = layout.itemAt(index)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.setVisible(visible)
        if child_layout is not None:
            _set_layout_visible(child_layout, visible)

def _set_form_field_visible(form: QFormLayout, field: QWidget, visible: bool) -> None:
    """Toggle a complete form row so hidden parameters contribute no geometry."""
    set_row_visible = getattr(form, "setRowVisible", None)
    if callable(set_row_visible):
        set_row_visible(field, visible)
        return
    label = form.labelForField(field)
    if label is not None:
        label.setVisible(visible)
    field.setVisible(visible)


def _size_list_to_content(widget: QListWidget, *, row_height: int = 30, visible_rows: int = COMPACT_VISIBLE_ROWS) -> None:
    """Keep empty lists tiny and cap populated lists with internal scrolling."""
    count = widget.count()
    height = 34 if count == 0 else min(count, visible_rows) * row_height + 6
    widget.setMinimumHeight(height)
    widget.setMaximumHeight(height)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


def _size_text_edit_to_content(widget: QTextEdit, *, visible_lines: int = 6) -> None:
    """Resize concise reports to content and cap detailed reports."""
    lines = max(1, widget.toPlainText().count('\n') + 1)
    height = min(lines, visible_lines) * 19 + 14
    widget.setMinimumHeight(height)
    widget.setMaximumHeight(height)
    widget.updateGeometry()
    parent = widget.parentWidget()
    while parent is not None and not isinstance(parent, CompactCollapsibleSection):
        parent = parent.parentWidget()
    if isinstance(parent, CompactCollapsibleSection):
        parent._content_widget.setMaximumHeight(16777215)
        _refresh_layout_geometry(parent)
        if parent.isChecked():
            QTimer.singleShot(0, lambda group=parent: _fit_collapsible_to_visible_content(group))


def _compact_output_summary(paths: tuple[Path, ...]) -> str:
    """Prefer product names over an unbounded raw filename list."""
    labels = {
        "chm": "CHM", "dtm": "DTM", "pad": "PAD", "pai": "PAI", "fhd": "FHD",
        "canopy_cover": "Canopy Cover", "rumple": "Rumple", "point_density": "Point Density",
        "voxel_statistic": "Voxel Statistic",
    }
    products: list[str] = []
    secondary: list[str] = []
    for path in paths:
        stem = path.stem.lower()
        if stem == "rumple_summary":
            secondary.append("Rumple summary")
            continue
        label = labels.get(stem, stem.replace("_", " ").title())
        if label not in products:
            products.append(label)
    line = f"{len(paths)} outputs ready"
    if products:
        line += "\n" + " · ".join(products)
    if secondary:
        line += "\n+ " + " · ".join(secondary)
    return line


def _height_spin(value: float) -> QDoubleSpinBox:
    """Return a compact non-negative height control in metres."""
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 10000.0)
    spin.setDecimals(2)
    spin.setSuffix(" m")
    spin.setValue(value)
    return spin


def _automatic_height_spin() -> QDoubleSpinBox:
    """Return a height control whose zero value means automatic/full canopy."""
    spin = _height_spin(0.0)
    spin.setSpecialValueText("Automatic")
    return spin


def _readable_list() -> QListWidget:
    """Return a list widget tuned for wrapped Advisor recommendation summaries."""
    widget = QListWidget()
    widget.setObjectName("advisorList")
    widget.setWordWrap(True)
    widget.setSpacing(6)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    return widget


def _add_advisor_item(widget: QListWidget, text: str, height: int = 58) -> None:
    """Add a wrapped Advisor list item with enough vertical space to read."""
    item = QListWidgetItem(text)
    item.setSizeHint(QSize(0, height))
    widget.addItem(item)


def _compact_duration(seconds: float) -> str:
    """Format live processing durations without exposing raw seconds."""
    value = max(0, int(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _body_label(text: str) -> QLabel:
    """Return a readable wrapped Advisor body label."""
    label = QLabel(text)
    label.setObjectName("advisorBodyText")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    return label


def _details_label(text: str) -> QLabel:
    """Return a visually quieter wrapped label for long Advisor detail text."""
    label = _body_label(text)
    label.setObjectName("advisorDetailsText")
    return label


def _advisor_metric_card(title: str, value: str) -> QLabel:
    """Return a large metric card label for the Advisor health summary."""
    label = QLabel(f"<b>{escape(title)}</b><br>{escape(value)}")
    label.setObjectName("advisorMetric")
    label.setWordWrap(True)
    label.setMinimumHeight(SECONDARY_BUTTON_HEIGHT + SPACING_XL)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    return label


def _product_explanation_card(item: object) -> QFrame:
    """Create a readable product explanation card."""
    frame = QFrame()
    frame.setObjectName("advisorNestedCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(SPACING_MD, SPACING_SM, SPACING_MD, SPACING_MD)
    layout.setSpacing(SPACING_SM)
    title = QLabel(getattr(item, "label"))
    title.setObjectName("advisorCardTitle")
    title.setWordWrap(True)
    layout.addWidget(title)
    summary = _body_label(f"Measures: {getattr(item, 'measures')}")
    layout.addWidget(summary)
    details = _details_label(
        _html_lines(
            (
                f"Use when: {getattr(item, 'use_when')}",
                f"Caution: {getattr(item, 'be_cautious_when')}",
                f"QGIS QA: {getattr(item, 'qgis_inspection')}",
            )
        )
    )
    layout.addWidget(details)
    return frame



def _html_lines(lines: tuple[str, ...] | list[str]) -> str:
    """Format plain lines as simple wrapped rich text for QLabel."""
    return "<br>".join(escape(line) if line else "<br>" for line in lines)


def _stars(value: int) -> str:
    count = max(0, min(5, value))
    return "*" * count + "-" * (5 - count)


def _product_label(product_id: str) -> str:
    for item in PRODUCT_EXPLANATIONS:
        if item.product_id == product_id:
            return item.label
    return product_id


def _product_cards_text() -> str:
    sections = []
    for item in PRODUCT_EXPLANATIONS:
        sections.append(
            f"{item.label}\n"
            f"Measures: {item.measures}\n"
            f"Use when: {item.use_when}\n"
            f"Be cautious when: {item.be_cautious_when}\n"
            f"Inspect in QGIS: {item.qgis_inspection}"
        )
    return "\n\n".join(sections)


def _tool_instruction_text() -> str:
    return "\n\n".join(
        f"{item.tool_name}\nOpen: {item.how_to_open}\nUse for: {item.use_for}"
        for item in QGIS_TOOL_INSTRUCTIONS
    )


def _selected_layer(iface: object | None) -> object | None:
    if iface is None:
        return None
    layer_tree = getattr(iface, "layerTreeView", None)
    if not callable(layer_tree):
        return None
    try:
        view = layer_tree()
        current_layer = getattr(view, "currentLayer", None)
        return current_layer() if callable(current_layer) else None
    except Exception:  # noqa: BLE001 - UI helper must degrade to instructions.
        return None

def _status_icon(status: str) -> str:
    return status_display_word(status)


def _format_bounds(report: DatasetExplorerReport) -> str:
    if report.bounds is None:
        return "Unknown"
    return (
        f"X {report.bounds.min_x:g} to {report.bounds.max_x:g}; "
        f"Y {report.bounds.min_y:g} to {report.bounds.max_y:g}; "
        f"Z {report.bounds.min_z if report.bounds.min_z is not None else 'Unknown'} "
        f"to {report.bounds.max_z if report.bounds.max_z is not None else 'Unknown'}"
    )


def _pipeline_status_icon(status: str) -> str:
    return status_display_word(status)


def _friendly_result_label(path: Path) -> str:
    """Return a friendly label for generated raster outputs."""
    stem = path.stem.lower()
    if "canopy_cover" in stem:
        return "Canopy Cover Output"
    if stem == "pad" or "pad" in stem:
        return "PAD Output"
    if stem == "pai" or "pai" in stem:
        return "PAI Output"
    if stem == "fhd" or "fhd" in stem:
        return "FHD Output"
    if "rumple" in stem:
        return "Rumple Summary"
    if stem == "job_summary":
        return "Final Run Summary" if path.suffix.lower() == ".html" else "Job Summary"
    if stem == "batch_summary":
        return "Batch Summary"
    return "CHM Output"


def _is_friendly_result_path(path: Path) -> bool:
    """Return whether a generated result should be shown as a friendly link."""
    suffix = path.suffix.lower()
    stem = path.stem.lower()
    if suffix in {".tif", ".tiff"}:
        return True
    if suffix == ".csv" and "rumple" in stem:
        return True
    if suffix == ".html" and stem in {"job_summary", "batch_summary"}:
        return True
    if suffix in {".csv", ".json"} and stem == "batch_summary":
        return True
    return False
