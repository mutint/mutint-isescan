"""One ISEScan run on an experiment's reference, and the directory it owns.

Shaped like `mutint_breseq.models.BreseqRun`: a row per run, a status the page reads, the
queue's result id so the page can ask the queue what it thinks, and a `post_delete` receiver
that removes the run's directory under `components/mutint_isescan/<pk>/` -- core reaps
nothing there, so this row is the whole lifecycle. Because `experiment` cascades, deleting an
experiment reaches the files too.
"""

import logging
import os
import shutil

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from mutint_common import store

logger = logging.getLogger("mutint_isescan.models")

COMPONENT = "mutint_isescan"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_INSTALLED = "installed"
STATUS_UNCHANGED = "unchanged"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

STATUS_CHOICES = [
    (STATUS_QUEUED, "Queued"),
    (STATUS_RUNNING, "Running"),
    (STATUS_INSTALLED, "Installed"),
    (STATUS_UNCHANGED, "Unchanged"),
    (STATUS_FAILED, "Failed"),
    (STATUS_CANCELLED, "Cancelled"),
]

#: `unchanged` is a finished state and not a failure: ISEScan ran and found nothing, so the
#: annotation was left as it was -- brefito's behaviour, and said so on the row.
FINISHED_STATUSES = (STATUS_INSTALLED, STATUS_UNCHANGED, STATUS_FAILED, STATUS_CANCELLED)

MAX_LOG_CHARS = 20000

#: Where ISEScan writes, under the run's directory; the FASTA copy sits beside it and goes.
OUTPUT_DIR = "out"


class IsescanRun(models.Model):
    experiment = models.ForeignKey("mutint_experiment.Experiment", on_delete=models.CASCADE,
                                   related_name="isescan_runs")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    # What the panel asked for: `replace_existing` and `remove_short_is`, as `annotator.clean`
    # returned them, and the thread count the task chose.
    options = models.JSONField(default=dict)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    task_result_id = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # What the merge did, null until it has. `removed` counts repeat features taken out
    # before ISEScan's were added; `added` counts the elements that went in.
    removed = models.IntegerField(null=True, blank=True)
    added = models.IntegerField(null=True, blank=True)

    error = models.TextField(blank=True)
    # The tail of the job's log, for the row that outlives the `Job`.
    log = models.TextField(blank=True)
    # The digest of the GFF3 this run installed, so the panel can say whether the current
    # annotation is still this run's.
    annotation_sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return "ISEScan run %s (%s)" % (self.pk, self.status)

    @property
    def is_finished(self):
        return self.status in FINISHED_STATUSES

    def directory(self):
        return store.component_dir(COMPONENT, self.pk)

    def output_dir(self):
        return os.path.join(self.directory(), OUTPUT_DIR)

    def output_files(self):
        """What ISEScan left, as paths relative to `output_dir()`, sorted.

        Everything under `out/` after the scratch is cleaned: the CSV the merge read, the
        same table as TSV and two aligned-text spellings, ISEScan's own GFF3 of the
        prediction, the per-family summary, and the elements' and transposase ORFs' sequences.
        Served by `views.run_file`; a name not in this list is refused there.
        """
        root = self.output_dir()
        found = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                found.append(os.path.relpath(os.path.join(dirpath, name), root))
        return sorted(found)

    def truncated_log(self, text):
        if len(text) <= MAX_LOG_CHARS:
            return text
        return "…" + text[-MAX_LOG_CHARS:]


@receiver(post_delete, sender=IsescanRun)
def _remove_run_directory(sender, instance, **kwargs):
    try:
        shutil.rmtree(store.component_dir(COMPONENT, instance.pk), ignore_errors=True)
    except (ValueError, TypeError):
        logger.debug("no directory to remove for an unsaved IsescanRun")
