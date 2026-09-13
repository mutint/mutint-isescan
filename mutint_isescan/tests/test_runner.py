"""What reaches ISEScan's command line, where its CSV lands, and whether it is here."""

import os
import shutil
import tempfile
from unittest import mock

from django.test import SimpleTestCase, override_settings

from mutint_isescan import runner
from mutint_isescan.tests import fake_isescan


class ArgvTestCase(SimpleTestCase):
    def test_the_argv_is_brefitos(self):
        self.assertEqual(
            ["/t/isescan.py", "--nthread", "4", "--seqfile", "/r/reference.fasta",
             "--output", "/r/out"],
            runner.build_argv("/t/isescan.py", "/r/reference.fasta", "/r/out", 4))

    def test_complete_only_adds_the_flag_last(self):
        argv = runner.build_argv("i", "s", "o", 1, remove_short_is=True)
        self.assertEqual("--removeShortIS", argv[-1])

    def test_the_csv_lands_where_isescan_puts_it(self):
        self.assertEqual("/out/12/reference.fasta.csv",
                         runner.csv_path("/out", "/store/components/mutint_isescan/12/reference.fasta"))


class EnvironmentTestCase(SimpleTestCase):
    def setUp(self):
        self.tools = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tools, True)

    def test_the_tools_bin_leads_the_path(self):
        with override_settings(MUTINT_TOOLS_DIR=self.tools):
            env = runner.tool_environment({"PATH": "/usr/bin"})
        self.assertEqual(os.path.join(self.tools, "bin") + os.pathsep + "/usr/bin", env["PATH"])

    def test_available_when_the_fake_is_installed(self):
        fake_isescan.install(self.tools)
        with override_settings(MUTINT_TOOLS_DIR=self.tools):
            self.assertEqual((True, ""), runner.available())

    def test_not_available_names_tools_txt(self):
        # PATH cleared too: tool_path falls back to it by design, and a developer's own
        # isescan.py would otherwise pass this test for them alone.
        with override_settings(MUTINT_TOOLS_DIR=self.tools), \
                mock.patch.dict(os.environ, {"PATH": ""}):
            ok, reason = runner.available()
        self.assertFalse(ok)
        self.assertIn("isescan.py", reason)
        self.assertIn("tools.txt", reason)

    def test_default_threads_is_capped_by_the_setting(self):
        with override_settings(MUTINT_ISESCAN_THREADS=1):
            self.assertEqual(1, runner.default_threads())

    def test_default_threads_leaves_two_cores(self):
        """`./mutint start` runs a pool of workers, so this is no longer the only thing on the
        machine: the web server, the cluster and another run may all be competing with it."""
        with mock.patch.object(runner.os, "cpu_count", return_value=8):
            self.assertEqual(6, runner.default_threads())

    def test_a_small_machine_still_gets_one(self):
        for cpus in (None, 1, 2, 3):
            with mock.patch.object(runner.os, "cpu_count", return_value=cpus):
                self.assertEqual(1, runner.default_threads())
