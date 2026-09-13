"""Running ISEScan on a stored reference, and installing what it found.

Shaped like `mutint_breseq.tasks.run_breseq`: a primary key for an argument, the queue id
from the task context, the flag polled while the tool runs, a failure recorded on the row and
then re-raised so the queue's record says a worker tried. Two things are this task's own:

**The reference is copied into the run's directory first.** ISEScan names its output
directory after the directory the sequence file sits in, so a copy in a directory of the
run's own makes the CSV path knowable before the run starts (`runner.csv_path`), and it means
a reference renamed or re-annotated in the hour ISEScan takes cannot change under the run.

**Installing holds the import lock, waiting.** What the merge produces is a new annotation
for the same sequence, written through core's `install_annotation`, which re-annotates every
mutation -- an import-class write. `import_lock.hold_waiting` polls rather than refusing,
because nobody is waiting on a worker's response and what is at stake is the run just done;
the cancellation flag is its `check`, so a wait can be given up on.
"""

import logging
import os
import shutil
import subprocess
import time

from django.conf import settings
from django.tasks import task
from django.utils import timezone

from mutint_common import store
from mutint_common.tools import ToolMissing
from mutint_import import import_lock, reference, reference_store
from mutint_import.annotate.gff3 import load_gff3, render_breseq_gff3
from mutint_import.annotation import install_annotation
from mutint_jobs import jobs, logs, processes

from mutint_isescan import merge, runner
from mutint_isescan.models import (
    OUTPUT_DIR,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_INSTALLED,
    STATUS_RUNNING,
    STATUS_UNCHANGED,
    IsescanRun,
)

logger = logging.getLogger("mutint_isescan.tasks")

#: A bacterial genome is minutes; the budget guards a wedged tool, not a slow one.
DEFAULT_TIMEOUT_SECONDS = 6 * 60 * 60

REFERENCE_COPY = "reference.fasta"

#: ISEScan's scratch, large and reproducible, deleted after a run whatever the outcome.
SCRATCH_DIRS = ("proteome", "hmm")


def _timeout():
    return getattr(settings, "MUTINT_ISESCAN_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)


def _queue_id(context, run):
    from_context = getattr(getattr(context, "task_result", None), "id", None)
    return str(from_context) if from_context else (run.task_result_id or "")


def _finish(run, status, error="", log=None, **fields):
    run.status = status
    run.error = error
    run.finished_at = timezone.now()
    if log is not None:
        run.log = log
    for name, value in fields.items():
        setattr(run, name, value)
    run.save()


def _tail(queue_id, run):
    text, _truncated = logs.read_tail(queue_id)
    return run.truncated_log(text)


def _clean_scratch(run_dir):
    for name in (REFERENCE_COPY,):
        try:
            os.unlink(os.path.join(run_dir, name))
        except OSError:
            pass
    for dirpath, dirnames, _filenames in os.walk(run_dir):
        for name in list(dirnames):
            if name in SCRATCH_DIRS:
                shutil.rmtree(os.path.join(dirpath, name), ignore_errors=True)
                dirnames.remove(name)


@task(takes_context=True)
def run_isescan(context, run_id):
    """Run ISEScan for one `IsescanRun`, merge its CSV, install the annotation."""
    run = IsescanRun.objects.filter(pk=run_id).select_related("experiment").first()
    if run is None:
        logger.info("isescan run %s is gone; nothing to do", run_id)
        return None

    queue_id = _queue_id(context, run)
    if jobs.is_cancelled(queue_id):
        _finish(run, STATUS_CANCELLED)
        return None

    experiment = run.experiment
    run.status = STATUS_RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    fasta = store.experiment_reference_path(experiment.id, store.REFERENCE_FASTA)
    gff3 = reference_store.annotation_reference_path(experiment.id)
    if not os.path.isfile(fasta) or not gff3:
        message = "%s has no stored reference genome to run ISEScan on." % experiment.name
        _finish(run, STATUS_FAILED, message)
        raise RuntimeError(message)

    try:
        isescan = runner.isescan_path()
    except ToolMissing as missing:
        _finish(run, STATUS_FAILED, str(missing))
        raise

    run_dir = store.ensure_dir(run.directory())
    seqfile = os.path.join(run_dir, REFERENCE_COPY)
    shutil.copyfile(fasta, seqfile)
    output_dir = os.path.join(run_dir, OUTPUT_DIR)
    options = run.options or {}
    argv = runner.build_argv(isescan, seqfile, output_dir,
                             threads=options.get("threads") or runner.default_threads(),
                             remove_short_is=bool(options.get("remove_short_is")))

    with logs.open_log(queue_id) as log:
        try:
            returncode = processes.run_tool(
                argv, log, env=runner.tool_environment(), timeout=_timeout(),
                is_cancelled=lambda: jobs.is_cancelled(queue_id), what="isescan")
        except processes.Cancelled:
            _finish(run, STATUS_CANCELLED, log=_tail(queue_id, run))
            _clean_scratch(run_dir)
            return None
        except subprocess.TimeoutExpired:
            message = "ISEScan did not finish within %d seconds and was stopped." % _timeout()
            _finish(run, STATUS_FAILED, message, log=_tail(queue_id, run))
            raise RuntimeError(message)
        except OSError as failed:
            message = "ISEScan could not be started: %s" % failed
            _finish(run, STATUS_FAILED, message, log=_tail(queue_id, run))
            raise

        output = _tail(queue_id, run)
        if returncode != 0:
            message = "ISEScan exited with status %d." % returncode
            _finish(run, STATUS_FAILED, message, log=output)
            raise RuntimeError(message)

        csv_path = runner.csv_path(output_dir, seqfile)
        elements = []
        if os.path.isfile(csv_path):
            try:
                elements = merge.read_isescan_csv(csv_path)
            except (ValueError, KeyError) as bad:
                message = "ISEScan's output could not be read: %s" % bad
                _finish(run, STATUS_FAILED, message, log=output)
                raise RuntimeError(message)
        if not elements:
            logs.write(log, "ISEScan found no IS elements; the annotation is left as it was.")
            _finish(run, STATUS_UNCHANGED, log=_tail(queue_id, run), removed=0, added=0)
            _clean_scratch(run_dir)
            return 0

        # The stored GFF3 read fresh -- not through the annotation cache -- because the
        # merge changes it in place and what is installed must start from what is stored now.
        references = load_gff3(gff3)
        result = merge.merge_isescan(references, elements,
                                     replace_existing=bool(options.get("replace_existing",
                                                                       True)))
        for sentence in result.warnings:
            logs.write(log, sentence)
        if result.unknown_seq_ids:
            logs.write(log, "Skipped elements on contigs the reference does not have: %s"
                       % ", ".join(result.unknown_seq_ids))
        logs.write(log, "Repeat features removed: %d; IS elements added: %d"
                   % (result.removed, result.added))

        gff3_text = render_breseq_gff3(references)
        sequences = reference.sequences_of(references)

        try:
            with import_lock.hold_waiting(
                    holder="isescan run %d (%s)" % (run.pk, import_lock.describe_holder()),
                    check=lambda: jobs.check_cancelled(
                        queue_id, "This run was cancelled while it waited to install.")):
                installed, _created = install_annotation(
                    experiment, gff3_text, sequences,
                    actor="isescan run %d" % run.pk)
        except jobs.JobCancelled:
            _finish(run, STATUS_CANCELLED, log=_tail(queue_id, run))
            _clean_scratch(run_dir)
            return None
        except Exception as failed:
            message = "The annotation could not be installed: %s" % failed
            _finish(run, STATUS_FAILED, message, log=_tail(queue_id, run))
            raise

        logs.write(log, "Annotation installed.")
        _finish(run, STATUS_INSTALLED, log=_tail(queue_id, run),
                removed=result.removed, added=result.added,
                annotation_sha256=installed.gff3_sha256)
    _clean_scratch(run_dir)
    return result.added
