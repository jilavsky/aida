"""Shots for docs/guides/workflows.md.

Two workflows from the page's own YAML examples, saved into the scratch
AIDA_HOME against the standard workspace ``saxs-analysis``: ``daily-report``
(two steps, ``{folder}`` variable) and ``citation-watch`` (a repeating step).
Two schedules from the page's CLI examples; their last-run status is written
into the scratch database (one ok, one failed), nothing is ever fired.
"""

from __future__ import annotations

from ._common import Context, shot
from ._package import reset_docs_tmp, standard_settings


def _settings():
    from aida.config.settings import (
        ScheduleEntry,
        SchedulesConfig,
        WorkflowConfig,
        WorkflowStep,
        load_settings,
        save_schedules_config,
        save_workflow,
    )

    reset_docs_tmp()
    standard_settings()
    save_workflow(
        WorkflowConfig(
            name="daily-report",
            description="Reduce new USAXS data and write a summary report",
            workspace="saxs-analysis",
            vars={"folder": "/data/latest"},
            steps=[
                WorkflowStep(
                    prompt="Reduce and process all new files in {folder}.",
                    expect_files=["*.h5"],
                ),
                WorkflowStep(prompt="Write a one-page Markdown summary of what changed."),
            ],
        )
    )
    save_workflow(
        WorkflowConfig(
            name="citation-watch",
            description="Batch citation watch, one batch per pass",
            workspace="saxs-analysis",
            steps=[
                WorkflowStep(
                    prompt=(
                        "Read citation_state.json in the target folder (create it if missing: "
                        "13 batches, none done). Process the next unfinished batch of 25 "
                        "papers, add any candidates to candidates.json, mark the batch done in "
                        "citation_state.json, then stop. If every batch is now done, also "
                        "write citation_watch.done."
                    ),
                    repeat_until="citation_watch.done",
                    max_passes=20,
                ),
                WorkflowStep(
                    prompt=(
                        "Write the citation report from candidates.json, then delete "
                        "citation_watch.done."
                    )
                ),
            ],
        )
    )
    save_schedules_config(
        SchedulesConfig(
            schedules={
                "nightly-report": ScheduleEntry(
                    name="nightly-report", workflow="daily-report", at="07:00"
                ),
                "hourly-check": ScheduleEntry(
                    name="hourly-check",
                    workflow="daily-report",
                    every="4h",
                    vars={"folder": "/data/latest"},
                    yes_in_allowed=True,
                ),
            }
        )
    )
    return load_settings()


def _record_runs() -> None:
    from aida.persistence.store import ScheduleRunStore

    store = ScheduleRunStore()
    store.record_run(
        schedule_name="nightly-report",
        fired_at="2026-11-16T07:00:00",
        status="ok",
    )
    store.record_run(
        schedule_name="hourly-check",
        fired_at="2026-11-16T08:00:00",
        status="failed",
        # The format aida.core.workflows uses for a failed expect_files check.
        error="step 0: expected file(s) not found: *.h5",
    )
    store.close()


@shot("workflows-01-dialog")
def dialog(ctx: Context):
    from aida.ui.qt.workflow_management_dialog import WorkflowManagementDialog

    window = WorkflowManagementDialog(_settings())
    names = [window._workflow_list.item(i).text() for i in range(window._workflow_list.count())]
    window._workflow_list.setCurrentRow(
        next(i for i, n in enumerate(names) if n.startswith("daily-report"))
    )
    ctx.show(window, (720, 440))
    return window.grab()


@shot("workflows-02-repeating-step")
def repeating_step(ctx: Context):
    from aida.config.settings import load_workflow
    from aida.ui.qt.workflow_management_dialog import StepFormDialog

    _settings()
    workflow = load_workflow("citation-watch")
    form = StepFormDialog(step=workflow.steps[0])
    ctx.show(form, (560, 400))
    return form.grab()


@shot("workflows-03-schedules")
def schedules(ctx: Context):
    from aida.ui.qt.bridge import AsyncLoopThread
    from aida.ui.qt.schedule_management_dialog import ScheduleManagementDialog
    from aida.ui.qt.scheduler_bridge import SchedulerBridge

    settings = _settings()
    _record_runs()
    loop_thread = AsyncLoopThread()
    loop_thread.start()
    loop_thread.wait_until_ready()
    try:
        bridge = SchedulerBridge(loop_thread, poll_interval_seconds=3600)
        window = ScheduleManagementDialog(settings, bridge)
        names = [window._schedule_list.item(i).text() for i in range(window._schedule_list.count())]
        window._schedule_list.setCurrentRow(
            next(i for i, n in enumerate(names) if n.startswith("hourly-check"))
        )
        ctx.show(window, (760, 460))
        return window.grab()
    finally:
        loop_thread.stop()
