"""What the annotator registry calls: the panel's context, its options, and `run`.

`run` queues a job and returns at once -- ISEScan is minutes to an hour on a bacterial
genome, and the registry's contract is a prompt answer with a message. Everything that takes
time is `tasks.run_isescan`. It returns the job's id with that message, which is what puts the
run in the panel above the Import data page's tab strip, live and with a Cancel button, rather
than in a sentence pointing at another page.

`in_flight` is the one rule worth reading before changing anything here: what is still going
is what the *queue* says is still going, never what a row's own status column says.
"""

import logging
import os

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

#: Dotted path of the task, for `mutint_jobs.queue.status_of`, which imports the `@task` object
#: to reach whatever backend is configured. A literal rather than an import of `tasks`, which
#: would be a circular import at module load -- `tasks` imports this module's `_outcome`.
TASK_PATH = "mutint_isescan.tasks.run_isescan"

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

    blocking = in_flight(experiment)
    if blocking is not None:
        return {"error": ("ISEScan is already running for this experiment (run %d); wait "
                          "for it to finish before starting another." % blocking.pk)}

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
        cancellable=True,
        # What this job will do is rewrite the annotation, and the Import data page holds
        # every drop until it has. See `mutint_jobs.models.Job.annotates_reference`.
        annotates_reference=True)
    isescan_run.task_result_id = job.task_result_id
    isescan_run.save(update_fields=["task_result_id"])
    # Read back: under an immediate task backend the run has already finished by now, and
    # the message should say so rather than promise a job that is over.
    isescan_run.refresh_from_db()
    if isescan_run.is_finished:
        message = "ISEScan run %d finished: %s." % (isescan_run.pk, _outcome(isescan_run))
    else:
        message = ("ISEScan queued as job %d; the annotation is installed when it finishes."
                   % job.pk)
    # `job_id` is what turns that sentence into a link and puts the run in the panel above
    # the tab strip, where it can be watched and stopped without leaving the page. Core
    # reverses it; see `mutint_common.annotator_registry`.
    return {"message": message, "run_id": isescan_run.pk, "job_id": job.pk}


def in_flight(experiment):
    """The run that is genuinely still going for `experiment`, or None.

    **The status column is a record of what happened; the queue is the authority on what is
    happening.** A worker killed outright -- which `./mutint start`'s own shutdown does -- never
    gets to write `failed`, so the row says `running` for ever. Trusting it alone wedged an
    experiment permanently: `run` refused to start another ISEScan, and `views.run_delete`
    refused to delete the run that was refusing, so there was no way out from the page at all.

    So an unfinished-looking row is only a *candidate*, and `status_of` decides. Every way the
    queue can fail to answer -- a pruned result, a renamed task, a backend that cannot say --
    is `STATUS_UNKNOWN`, which counts as finished, and that direction is the right one: the
    cost of being wrong is one wasted ISEScan run, against an experiment nobody can use.

    There is a window between creating the row and writing its `task_result_id` in which a run
    has no queue id and so looks finished. It is not reachable: both callers -- the finalize
    path and Run annotators -- hold the global import lock across this.
    """
    from mutint_jobs import jobs as jobs_lib
    from mutint_jobs import queue

    candidates = (IsescanRun.objects.filter(experiment=experiment)
                  .exclude(status__in=FINISHED_STATUSES).order_by("-pk"))
    for candidate in candidates:
        # A row is created before its job is enqueued, so one with no queue id never reached
        # the queue -- an `enqueue` that raised -- and is a record of an attempt rather than
        # work under way. Spelled out rather than left to `status_of("")`, because this is the
        # state that used to refuse every future run against the experiment for ever.
        if not candidate.task_result_id:
            continue
        if not jobs_lib.finished(queue.status_of(candidate.task_result_id, TASK_PATH)):
            return candidate
    return None


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
            "files": _file_rows(isescan_run),
        })
    return rows


def _file_rows(isescan_run):
    """ISEScan's outputs as `{name, label, url}`, empty until the run has finished.

    The label is the part of the name ISEScan added -- `csv`, `sum`, `is.fna` -- since every
    file is named for the one sequence file it was run on and the prefix says nothing.
    """
    if not isescan_run.is_finished:
        return []
    rows = []
    for name in isescan_run.output_files():
        base = os.path.basename(name)
        label = base.split(".", 2)[2] if base.count(".") >= 2 else base
        rows.append({"name": name, "label": label,
                     "url": reverse("isescan_run_file", kwargs={"pk": isescan_run.pk,
                                                                "name": name})})
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
