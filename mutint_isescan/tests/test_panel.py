"""The panel on the Import data page, the finalize round trip, and the run endpoints.

Assembled-project behaviour core's own tests cannot see: this plugin's registration reaching
core's page and core's finalize reaching this plugin's `run`.
"""

import json
import os
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from mutint_experiment.models import Project
from mutint_import import annotation, reference, reference_store

from mutint_isescan.models import STATUS_INSTALLED, STATUS_QUEUED, IsescanRun
from mutint_isescan.tests import fake_isescan
from mutint_isescan.tests.test_run import SYNTHETIC_GFF3


class PanelTestCase(TestCase):
    def setUp(self):
        annotation.clear_cache()
        self.addCleanup(annotation.clear_cache)
        self.user = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.user)
        self.store = tempfile.mkdtemp()
        self.tools = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        self.addCleanup(shutil.rmtree, self.tools, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store, MUTINT_TOOLS_DIR=self.tools)
        patcher.enable()
        self.addCleanup(patcher.disable)
        os.environ["FAKE_ISESCAN_CSV"] = fake_isescan.csv_row()
        self.addCleanup(os.environ.pop, "FAKE_ISESCAN_CSV", None)

        self.project = Project.objects.create(name="p", user=self.user)
        from mutint_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.user)

    def _establish(self):
        gff3_text, sequences = reference.normalize_reference(SYNTHETIC_GFF3)
        reference_store.establish_or_check(self.experiment, gff3_text, sequences,
                                           update_annotation=True)

    def _page(self, tab):
        return self.client.get("/import/?experiment_id=%s&tab=%s"
                               % (self.experiment.id, tab)).content.decode()

    def test_the_panel_is_on_both_reference_tabs(self):
        fake_isescan.install(self.tools)
        html = self._page("reference")
        self.assertIn('data-annotator="isescan"', html)
        self.assertIn('name="replace_existing"', html)
        self.assertIn("mutint_isescan/panel.js", html)
        self._establish()
        html = self._page("update_annotation")
        self.assertIn('data-annotator="isescan"', html)
        self.assertIn('id="annotate-run"', html)
        self.assertNotIn("is not installed", html)

    def test_without_the_tool_the_panel_says_so_and_offers_nothing(self):
        with mock.patch.dict(os.environ, {"PATH": ""}):
            html = self._page("reference")
        self.assertIn('data-annotator="isescan"', html)
        self.assertIn("tools.txt", html)
        self.assertIn('data-available="0"', html)
        self.assertIn('name="replace_existing"\n                      checked\n                      disabled', html)

    def test_the_run_annotators_button_queues_a_run(self):
        fake_isescan.install(self.tools)
        self._establish()
        response = self.client.post(
            "/import/annotate",
            data=json.dumps({"experiment_id": self.experiment.id,
                             "annotators": {"isescan": {"enabled": True,
                                                        "replace_existing": True,
                                                        "remove_short_is": False}}}),
            content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        row = response.json()["annotators"][0]
        self.assertEqual("isescan", row["name"])
        self.assertIsNone(row["error"])
        run = IsescanRun.objects.get()
        self.assertEqual(STATUS_INSTALLED, run.status, run.error)
        self.assertEqual(self.user, run.created_by)
        self.assertEqual({"replace_existing": True, "remove_short_is": False},
                         {k: v for k, v in run.options.items() if k != "threads"})

    def test_a_ticked_box_runs_after_an_annotation_update(self):
        fake_isescan.install(self.tools)
        self._establish()
        with open(SYNTHETIC_GFF3, "rb") as handle:
            payload = handle.read()
        created = self.client.post(
            "/import/uploads/",
            data=json.dumps({"experiment_id": self.experiment.id,
                             "import_type": "replace_annotation",
                             "files": [{"path": "better.gff3", "size": len(payload)}]}),
            content_type="application/json")
        upload_id = created.json()["upload_id"]
        self.client.post("/import/uploads/%s/chunk" % upload_id,
                         {"path": "better.gff3", "offset": "0",
                          "chunk": SimpleUploadedFile("chunk", payload)})
        response = self.client.post(
            "/import/uploads/%s/finalize" % upload_id,
            data=json.dumps({"annotators": {"isescan": {"enabled": True,
                                                        "replace_existing": "true"}}}),
            content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["annotators"]
        self.assertEqual(1, len(rows))
        self.assertIn("ISEScan run", rows[0]["message"])
        self.assertEqual(STATUS_INSTALLED, IsescanRun.objects.get().status)

    def test_the_run_list_and_deleting_a_finished_run(self):
        self._establish()
        run = IsescanRun.objects.create(experiment=self.experiment, status=STATUS_QUEUED)
        rows = self.client.get("/isescan/runs?experiment_id=%s" % self.experiment.id).json()
        self.assertEqual([run.pk], [r["id"] for r in rows["runs"]])
        self.assertFalse(rows["runs"][0]["finished"])

        refused = self.client.post("/isescan/runs/%d/delete" % run.pk)
        self.assertEqual(refused.status_code, 409)

        run.status = STATUS_INSTALLED
        run.save(update_fields=["status"])
        os.makedirs(run.directory(), exist_ok=True)
        deleted = self.client.post("/isescan/runs/%d/delete" % run.pk)
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(IsescanRun.objects.filter(pk=run.pk).exists())
        self.assertFalse(os.path.exists(run.directory()))

    def test_a_reader_cannot_delete_a_run(self):
        self._establish()
        run = IsescanRun.objects.create(experiment=self.experiment, status=STATUS_INSTALLED)
        stranger = User.objects.create(username="s", email="s@e.com", is_active=True)
        self.client.force_login(stranger)
        self.assertEqual(self.client.post("/isescan/runs/%d/delete" % run.pk).status_code, 403)
        # 404, not 403: core's `get_experiment` answers "no such experiment" for one the
        # reader cannot see, so the endpoint cannot be used to discover which ids exist.
        self.assertEqual(
            self.client.get("/isescan/runs?experiment_id=%s" % self.experiment.id).status_code,
            404)
