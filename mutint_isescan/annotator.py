"""What the annotator registry calls: the panel's context, its options, and `run`.

`run` queues a job and returns at once -- ISEScan is minutes to an hour on a bacterial
genome, and the registry's contract is a prompt answer with a message. Everything that takes
time is `tasks.run_isescan`.
"""

import logging

from django.urls import reverse

from mutint_jobs import jobs as jobs_api

from mutint_isescan import runner
from mutint_isescan.models import (
    COMPONENT,
    FINISHED_STATUSES,
    STATUS_QUEUED,
    IsescanRun,
)

logger = logging.getLogger("mutint_isescan.annotator")

DEFAULTS = {"replace_existing": True, "remove_short_is": False}


def clean(options):
    """The panel's two checkboxes as booleans; anything else it posted is ignored."""
    return {
        "replace_existing": _as_bool(options.get("replace_existing", True)),
        "remove_short_is": _as_bool(options.get("remove_short_is", False)),
    }


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes")
    return bool(value)


def run(experiment, options, user):
    """Queue an ISEScan run against `experiment`'s stored reference.

    Declines -- with `error` on the row rather than an exception, since neither is a fault
    of this component -- when the tool is not installed here, and when a run for this
    experiment is still under way: two would race on `install_annotation`, and the second
    person can press the button again when the first has finished.
    """
    ok, reason = runner.available()
    if not ok:
        return {"error": reason}

    in_flight = IsescanRun.objects.filter(experiment=experiment).exclude(
        status__in=FINISHED_STATUSES).first()
    if in_flight is not None:
        return {"error": ("ISEScan is already running for this experiment (run %d); wait "
                          "for it to finish before starting another." % in_flight.pk)}

    from mutint_isescan import tasks

    stored = dict(options)
    stored["threads"] = runner.default_threads()
    isescan_run = IsescanRun.objects.create(
        experiment=experiment,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        options=stored,
        status=STATUS_QUEUED)
    job = jobs_api.enqueue(
        tasks.run_isescan, isescan_run.pk,
        user=user,
        label="ISEScan — %s" % experiment.name,
        component=COMPONENT,
        experiment=experiment,
        cancellable=True)
    isescan_run.task_result_id = job.task_result_id
    isescan_run.save(update_fields=["task_result_id"])
    # Read back: under an immediate task backend the run has already finished by now, and
    # the message should say so rather than promise a job that is over.
    isescan_run.refresh_from_db()
    if isescan_run.is_finished:
        message = "ISEScan run %d finished: %s." % (isescan_run.pk, _outcome(isescan_run))
    else:
        message = ("ISEScan queued as job %d; the annotation is installed when it finishes. "
                   "Watch it on the Jobs page." % job.pk)
    return {"message": message, "run_id": isescan_run.pk}


def _outcome(isescan_run):
    if isescan_run.error:
        return isescan_run.error
    if isescan_run.status == "unchanged":
        return "no IS elements were found, so the annotation was left as it was"
    if isescan_run.status == "installed":
        return ("%s repeat feature(s) removed, %s IS element(s) added"
                % (isescan_run.removed, isescan_run.added))
    return isescan_run.status


def panel_context(experiment, request):
    """What the panel template renders: availability, defaults, and this experiment's runs."""
    ok, reason = runner.available()
    return {
        "available": ok,
        "reason": reason,
        "defaults": dict(DEFAULTS),
        "runs": run_rows(experiment),
        "runs_url": reverse("isescan_runs") + "?experiment_id=%d" % experiment.id,
        "can_delete": request.user.is_authenticated,
    }


def run_rows(experiment):
    """The run list as JSON-safe rows, for the panel and for `views.runs`."""
    runs = list(IsescanRun.objects.filter(experiment=experiment))
    log_urls = jobs_api.log_urls(r.task_result_id for r in runs)
    rows = []
    for isescan_run in runs:
        rows.append({
            "id": isescan_run.pk,
            "status": isescan_run.status,
            "queue_status": _queue_status(isescan_run),
            "options": isescan_run.options,
            "created_at": isescan_run.created_at.isoformat(),
            "started_at": (isescan_run.started_at.isoformat()
                           if isescan_run.started_at else None),
            "finished_at": (isescan_run.finished_at.isoformat()
                            if isescan_run.finished_at else None),
            "removed": isescan_run.removed,
            "added": isescan_run.added,
            "error": isescan_run.error,
            "log": isescan_run.log,
            "log_url": log_urls.get(isescan_run.task_result_id, ""),
            "finished": isescan_run.is_finished,
            "delete_url": reverse("isescan_run_delete", kwargs={"pk": isescan_run.pk}),
        })
    return rows


def _queue_status(isescan_run):
    """What the queue thinks, or "" when there is nothing to ask -- mutint-breseq's rule:
    a row saying `queued` is either a job a worker has not reached or one no worker will
    ever reach, and the page says which."""
    if not isescan_run.task_result_id or isescan_run.is_finished:
        return ""
    from mutint_isescan import tasks

    try:
        return tasks.run_isescan.get_result(isescan_run.task_result_id).status.value
    except Exception:
        return ""
