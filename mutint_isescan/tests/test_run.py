"""The whole path: queue a run, run the fake, merge, install, re-annotate.

Under the suite's immediate task backend `jobs.enqueue` runs the task inline, so one call to
`annotator.run` exercises everything from the panel's contract to the stored GFF3.
"""

import json
import os
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from mutint_common import store
from mutint_import import annotation, gd_import, reference, reference_store
from mutint_jobs import jobs as jobs_api
from mutint_sample.models import ReferenceSequences

from mutint_isescan import annotator, tasks
from mutint_isescan.models import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_INSTALLED,
    STATUS_QUEUED,
    STATUS_UNCHANGED,
    IsescanRun,
)
from mutint_isescan.tests import fake_isescan

FIXTURES = os.path.join(os.path.dirname(annotation.__file__), "annotate", "tests", "fixtures")
SYNTHETIC_GFF3 = os.path.join(FIXTURES, "synthetic.gff3")
SYNTHETIC_GD = os.path.join(FIXTURES, "synthetic.gd")

DATABASE_BACKEND = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}


def _uploaded_as(path, name):
    with open(path, "rb") as handle:
        return SimpleUploadedFile(name, handle.read())


class RunFixture(TestCase):
    """An experiment with the synthetic reference and its mutations, and the fake tool.

    No tests of its own: the two cases below share it, and a subclass that inherited the
    tests as well would rerun every one under the other backend.
    """

    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        self.user = User.objects.create(username="tester", email="t@e.com", is_active=True,
                                        is_staff=True)
        self.store = tempfile.mkdtemp()
        self.tools = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        self.addCleanup(shutil.rmtree, self.tools, True)
        fake_isescan.install(self.tools)
        patcher = override_settings(MUTINT_STORE_DIR=self.store, MUTINT_TOOLS_DIR=self.tools)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.argv_record = os.path.join(self.tools, "argv.jsonl")
        for name, value in (("FAKE_ISESCAN_ARGV", self.argv_record),
                            ("FAKE_ISESCAN_CSV", fake_isescan.csv_row())):
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)
        for name in ("FAKE_ISESCAN_NO_CSV", "FAKE_ISESCAN_FAIL", "FAKE_ISESCAN_SLEEP"):
            self.addCleanup(os.environ.pop, name, None)

        context = gd_import._prepare_experiment("syn project", "syn exp", "tester", False)
        self.experiment = context["experiment"]
        gff3_text, sequences = reference.normalize_reference(SYNTHETIC_GFF3)
        reference_store.establish_or_check(self.experiment, gff3_text, sequences,
                                           update_annotation=True)
        gd_import.import_gd_files([_uploaded_as(SYNTHETIC_GD, "1-1-1-1.gd")],
                                  project_name="syn project", experiment_name="syn exp",
                                  owner_name="tester")

    def _stored_gff3(self):
        with open(store.experiment_reference_path(self.experiment.id, store.REFERENCE_GFF3)) as h:
            return h.read()

    def _run(self, **options):
        opts = {"replace_existing": True, "remove_short_is": False}
        opts.update(options)
        return annotator.run(self.experiment, opts, self.user)

    def _recorded(self):
        with open(self.argv_record) as handle:
            return [json.loads(line) for line in handle if line.strip()]


class IsescanRunTestCase(RunFixture):
    """Under the immediate backend, so one `run()` is the whole path."""

    def test_a_run_installs_the_merged_annotation(self):
        before = ReferenceSequences.objects.get(experiment=self.experiment).gff3_sha256
        with mock.patch("mutint_import.annotation.reannotate_experiment",
                        wraps=annotation.reannotate_experiment) as reannotate:
            result = self._run()

        run = IsescanRun.objects.get()
        self.assertEqual(STATUS_INSTALLED, run.status, run.error)
        self.assertEqual((2, 1), (run.removed, run.added))
        self.assertIn("finished", result["message"])
        self.assertIn("2 repeat feature(s) removed, 1 IS element(s) added", result["message"])
        text = self._stored_gff3()
        self.assertIn("Complete IS3 family IS element", text)
        self.assertNotIn("IS150", text)
        after = ReferenceSequences.objects.get(experiment=self.experiment).gff3_sha256
        self.assertNotEqual(before, after)
        self.assertEqual(after, run.annotation_sha256)
        reannotate.assert_called_once()
        # The tools directory led the PATH the fake saw, and threads reached the argv.
        record = self._recorded()[0]
        self.assertTrue(record["path"].startswith(os.path.join(self.tools, "bin")))
        self.assertIn("--nthread", record["argv"])
        self.assertNotIn("--removeShortIS", record["argv"])

    def test_the_scratch_goes_and_the_csv_stays(self):
        self._run()
        run = IsescanRun.objects.get()
        kept = []
        for dirpath, dirnames, filenames in os.walk(run.directory()):
            kept.extend(filenames)
            self.assertNotIn("proteome", dirnames)
        self.assertIn("reference.fasta.csv", kept)
        self.assertNotIn("reference.fasta", kept)

    def test_complete_only_reaches_the_command_line(self):
        self._run(remove_short_is=True)
        self.assertIn("--removeShortIS", self._recorded()[0]["argv"])

    def test_keeping_existing_repeats_is_honoured(self):
        self._run(replace_existing=False)
        run = IsescanRun.objects.get()
        self.assertEqual((0, 1), (run.removed, run.added))
        self.assertIn("IS150", self._stored_gff3())

    def test_nothing_found_leaves_the_annotation_as_it_was(self):
        os.environ["FAKE_ISESCAN_CSV"] = ""
        before = ReferenceSequences.objects.get(experiment=self.experiment).gff3_sha256
        result = self._run()
        run = IsescanRun.objects.get()
        self.assertEqual(STATUS_UNCHANGED, run.status)
        self.assertIn("no IS elements", result["message"])
        self.assertEqual(before,
                         ReferenceSequences.objects.get(experiment=self.experiment).gff3_sha256)
        self.assertIn("IS150", self._stored_gff3())

    def test_no_csv_at_all_is_nothing_found_too(self):
        os.environ["FAKE_ISESCAN_NO_CSV"] = "1"
        self._run()
        self.assertEqual(STATUS_UNCHANGED, IsescanRun.objects.get().status)

    def test_a_nonzero_exit_is_recorded_and_said(self):
        os.environ["FAKE_ISESCAN_FAIL"] = "3"
        result = self._run()
        run = IsescanRun.objects.get()
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("status 3", run.error)
        self.assertIn("status 3", result["message"])
        self.assertIn("IS150", self._stored_gff3())

    def test_without_the_tool_the_run_declines_and_makes_no_row(self):
        shutil.rmtree(os.path.join(self.tools, "bin"))
        with mock.patch.dict(os.environ, {"PATH": ""}):
            result = self._run()
        self.assertIn("tools.txt", result["error"])
        self.assertEqual(0, IsescanRun.objects.count())

    def test_a_second_run_while_one_is_under_way_is_refused(self):
        IsescanRun.objects.create(experiment=self.experiment, status=STATUS_QUEUED)
        result = self._run()
        self.assertIn("already running", result["error"])
        self.assertEqual(1, IsescanRun.objects.count())

    def test_the_task_re_raises_after_recording_a_failure(self):
        os.environ["FAKE_ISESCAN_FAIL"] = "2"
        run = IsescanRun.objects.create(experiment=self.experiment, status=STATUS_QUEUED,
                                        options={"replace_existing": True})
        with self.assertRaises(RuntimeError):
            tasks.run_isescan.call(None, run.pk)
        run.refresh_from_db()
        self.assertEqual(STATUS_FAILED, run.status)


@override_settings(TASKS=DATABASE_BACKEND)
class CancelledRunTestCase(RunFixture):
    """Against the database backend, so a queued job exists to be cancelled."""

    def test_a_run_cancelled_before_it_starts_runs_nothing(self):
        result = self._run()
        self.assertIn("queued as job", result["message"])
        run = IsescanRun.objects.get()
        self.assertEqual(STATUS_QUEUED, run.status)
        job = jobs_api.for_user(self.user).get(task_result_id=run.task_result_id)
        self.assertTrue(job.cancellable)
        jobs_api.request_cancel(job, by=self.user)

        self.assertIsNone(tasks.run_isescan.call(None, run.pk))

        run.refresh_from_db()
        self.assertEqual(STATUS_CANCELLED, run.status)
        self.assertFalse(os.path.exists(self.argv_record), "isescan was run after the cancel")
        self.assertIn("IS150", self._stored_gff3())

    def test_a_cancel_while_the_tool_runs_stops_it(self):
        os.environ["FAKE_ISESCAN_SLEEP"] = "1"
        self._run()
        run = IsescanRun.objects.get()
        with mock.patch.object(tasks.jobs, "is_cancelled", side_effect=[False] + [True] * 50):
            self.assertIsNone(tasks.run_isescan.call(None, run.pk))
        run.refresh_from_db()
        self.assertEqual(STATUS_CANCELLED, run.status)
        self.assertIn("IS150", self._stored_gff3())
