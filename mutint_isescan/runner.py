"""What reaches ISEScan's command line, where its output lands, and whether it is here.

Pure, in the shape `mutint_breseq.runner` is: the rules most likely to be changed by accident
are the argv and the path the CSV appears at, and both are testable without the tool. It runs
nothing -- `mutint_jobs.processes.run_tool` does.

**Where the CSV lands is ISEScan's rule, transcribed.** `isescan.py` takes `org` as the
basename of the *directory* the sequence file sits in and writes
`<output>/<org>/<basename(seqfile)>.csv` (`isescan.py` line 20, `pred.py` `outputIS…`), which
is why the task copies the reference into a directory of its own before running: the path is
then known before the run starts. Two "nothing found" shapes both mean unchanged: no HMM hit
means prediction never runs and **no CSV exists**; hits that yield no element mean a
**header-only CSV**.

**ISEScan shells out by bare name** -- hmmer, blastn, FragGeneScan -- so, like breseq, it needs
`env/tools/bin` on its PATH and an absolute path to `isescan.py` is not enough.
`tool_environment` is mutint-breseq's, copied; lifting it into `mutint_common.tools` is the
follow-up now that there are two producers.

**`available()` says only whether `isescan.py` can be found**, through `tools.tool_path`,
which looks in the managed tools directory and then on PATH. There is deliberately no
platform reasoning here: the tool is absent until its package is provisioned, whatever the
machine, and the sentence names `tools.txt`.
"""

import os

from django.conf import settings

from mutint_common import tools
from mutint_common.tools import ToolMissing

#: The executable bioconda installs. A script, so it is run directly.
ISESCAN = 'isescan.py'

REMOVE_SHORT_IS_FLAG = '--removeShortIS'


def isescan_path():
    """Where `isescan.py` is, or raise `ToolMissing` naming what installs it."""
    return tools.require(ISESCAN)


def available():
    """`(True, '')` when ISEScan can be run here, else `(False, <why>)`."""
    if tools.tool_path(ISESCAN):
        return True, ''
    try:
        tools.require(ISESCAN)
    except ToolMissing as missing:
        return False, str(missing)
    return False, '%s is not installed.' % ISESCAN


def build_argv(isescan, seqfile, output_dir, threads, remove_short_is=False):
    """ISEScan's command line: brefito's, plus `--removeShortIS` when asked."""
    argv = [isescan, '--nthread', str(threads), '--seqfile', seqfile, '--output', output_dir]
    if remove_short_is:
        argv.append(REMOVE_SHORT_IS_FLAG)
    return argv


def csv_path(output_dir, seqfile):
    """Where ISEScan writes the CSV for `seqfile` under `output_dir`."""
    seqfile = os.path.abspath(seqfile)
    org = os.path.basename(os.path.dirname(seqfile))
    return os.path.join(output_dir, org, os.path.basename(seqfile) + '.csv')


def tool_environment(env=None):
    """`env` with the managed tools directory first on PATH, for the tools ISEScan calls."""
    env = dict(os.environ if env is None else env)
    directory = tools.tools_dir()
    if not directory:
        return env
    bin_dir = os.path.join(directory, 'bin')
    existing = env.get('PATH', '')
    env['PATH'] = bin_dir + (os.pathsep + existing if existing else '')
    return env


def default_threads():
    """Every core, capped by `MUTINT_ISESCAN_THREADS` when a deployment sets one."""
    threads = os.cpu_count() or 1
    cap = getattr(settings, 'MUTINT_ISESCAN_THREADS', None)
    if cap:
        threads = min(threads, int(cap))
    return max(1, threads)
