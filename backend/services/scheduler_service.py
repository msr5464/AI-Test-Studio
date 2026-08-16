"""
Scheduler Service
=================
Runs daily sync jobs for TestRail and Confluence using APScheduler.
Schedule settings (enabled flag + time) are managed via the admin Settings UI
and persisted in storage/app_settings.json.

Jobs run in background daemon threads — the scheduler will not prevent the
process from exiting.  Call reconfigure() after any settings update that may
affect the schedule.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SchedulerService:
    """
    Wraps APScheduler BackgroundScheduler to run daily sync jobs.

    Usage in app.py:
        scheduler = SchedulerService(settings_service, app)
        app.config['SCHEDULER_SERVICE'] = scheduler
    """

    def __init__(self, settings_service: Any, app: Any = None):
        from apscheduler.schedulers.background import BackgroundScheduler

        self._settings = settings_service
        self._app = app
        self._scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
        self._configure_jobs()
        self._scheduler.start()
        logger.info("SchedulerService started")

    # ── Job runners ──────────────────────────────────────────────────────────

    def _run_testrail_sync(self) -> None:
        logger.info("Scheduled TestRail sync: starting")
        try:
            from backend.services.testrail_sync_service import TestRailSyncService
            rag_service = self._app.config.get('RAG_SERVICE') if self._app else None

            def _job():
                svc = TestRailSyncService()
                status = svc.get_sync_status()
                if status.get('is_syncing', False):
                    logger.info("Scheduled TestRail sync: skipped (already in progress)")
                    return
                svc.sync_from_testrail(rag_service=rag_service)
                logger.info("Scheduled TestRail sync: completed")

            if self._app:
                with self._app.app_context():
                    _job()
            else:
                _job()
        except Exception as e:
            logger.error(f"Scheduled TestRail sync: failed — {e}", exc_info=True)

    def _run_confluence_sync(self) -> None:
        logger.info("Scheduled Confluence sync: starting")
        try:
            from backend.services.confluence_sync_service import ConfluenceSyncService
            rag_service = self._app.config.get('RAG_SERVICE') if self._app else None

            def _job():
                svc = ConfluenceSyncService()
                status = svc.get_sync_status()
                if status.get('is_syncing', False):
                    logger.info("Scheduled Confluence sync: skipped (already in progress)")
                    return
                svc.sync_from_confluence(rag_service=rag_service)
                logger.info("Scheduled Confluence sync: completed")

            if self._app:
                with self._app.app_context():
                    _job()
            else:
                _job()
        except Exception as e:
            logger.error(f"Scheduled Confluence sync: failed — {e}", exc_info=True)

    # ── Job configuration ─────────────────────────────────────────────────────

    def _configure_jobs(self) -> None:
        """Re-read schedule settings and register/remove cron jobs."""
        from apscheduler.triggers.cron import CronTrigger

        for job_id in ('testrail_daily', 'confluence_daily'):
            if self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)

        # TestRail
        tr_enabled = self._settings.get('testrail_schedule_enabled', False)
        if isinstance(tr_enabled, str):
            tr_enabled = tr_enabled.lower() in ('true', '1', 'yes')
        tr_time = str(self._settings.get('testrail_schedule_time', '02:00') or '02:00')
        if tr_enabled:
            try:
                hour, minute = tr_time.split(':')
                self._scheduler.add_job(
                    self._run_testrail_sync,
                    trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone="UTC"),
                    id='testrail_daily',
                    replace_existing=True,
                )
                logger.info(f"TestRail sync scheduled daily at {tr_time} UTC")
            except Exception as e:
                logger.error(f"Failed to schedule TestRail sync: {e}")

        # Confluence
        cf_enabled = self._settings.get('confluence_schedule_enabled', False)
        if isinstance(cf_enabled, str):
            cf_enabled = cf_enabled.lower() in ('true', '1', 'yes')
        cf_time = str(self._settings.get('confluence_schedule_time', '03:00') or '03:00')
        if cf_enabled:
            try:
                hour, minute = cf_time.split(':')
                self._scheduler.add_job(
                    self._run_confluence_sync,
                    trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone="UTC"),
                    id='confluence_daily',
                    replace_existing=True,
                )
                logger.info(f"Confluence sync scheduled daily at {cf_time} UTC")
            except Exception as e:
                logger.error(f"Failed to schedule Confluence sync: {e}")

    def reconfigure(self) -> None:
        """Re-read schedule settings and update running jobs. Call after settings save."""
        self._configure_jobs()

    def get_schedule_info(self) -> Dict[str, Any]:
        """Return next-run information for each job (for UI display)."""
        result = {}
        for job_id, name in (('testrail_daily', 'testrail'), ('confluence_daily', 'confluence')):
            job = self._scheduler.get_job(job_id)
            result[name] = {
                'scheduled': job is not None,
                'next_run': job.next_run_time.isoformat() if job and job.next_run_time else None,
            }
        return result

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
