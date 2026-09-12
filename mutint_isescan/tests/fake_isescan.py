"""A stand-in for `isescan.py`: a real executable on disk, not a `subprocess.run` patch.

The two things most likely to be wrong are the argv and where the CSV lands, and a patch would
assert against the call rather than against a process that has to start and a file that has
to be found. It records its argv and `PATH` to `$FAKE_ISESCAN_ARGV`, and writes
`$FAKE_ISESCAN_CSV` -- CSV text; empty means header-only -- to ISEScan's own path,
`<output>/<basename(dirname(seqfile))>/<basename(seqfile)>.csv`, plus a `proteome/` scratch
directory so the cleanup has something to remove. `FAKE_ISESCAN_NO_CSV` writes nothing at
all; `FAKE_ISESCAN_FAIL=<n>` exits with that status; `FAKE_ISESCAN_SLEEP` sleeps for ten
minutes so a cancellation has something to stop.
"""

import os
import stat
import sys
import textwrap

SCRIPT = textwrap.dedent('''\
    #!{python}
    import json, os, sys, time
    argv = sys.argv[1:]
    record = os.environ.get("FAKE_ISESCAN_ARGV")
    if record:
        with open(record, "a") as handle:
            handle.write(json.dumps({{"argv": argv, "path": os.environ.get("PATH", "")}}) + "\\n")
    if os.environ.get("FAKE_ISESCAN_SLEEP"):
        time.sleep(600)
    if os.environ.get("FAKE_ISESCAN_FAIL"):
        sys.stderr.write("fake isescan failing as asked\\n")
        sys.exit(int(os.environ["FAKE_ISESCAN_FAIL"]))
    seqfile = argv[argv.index("--seqfile") + 1]
    output = argv[argv.index("--output") + 1]
    org = os.path.basename(os.path.dirname(os.path.abspath(seqfile)))
    target = os.path.join(output, org)
    os.makedirs(os.path.join(target, "proteome"), exist_ok=True)
    with open(os.path.join(target, "proteome", "scratch.faa"), "w") as handle:
        handle.write(">x\\nMKV\\n")
    if os.environ.get("FAKE_ISESCAN_NO_CSV"):
        print("no IS element found")
        sys.exit(0)
    text = os.environ.get("FAKE_ISESCAN_CSV", "")
    header = ("seqID,family,cluster,isBegin,isEnd,isLen,ncopy4is,start1,end1,start2,end2,"
              "score,irId,irLen,nGaps,orfBegin,orfEnd,strand,orfLen,E-value,E-value4copy,"
              "type,ov,tir\\n")
    with open(os.path.join(target, os.path.basename(seqfile) + ".csv"), "w") as handle:
        handle.write(header + text)
    print("fake isescan wrote its csv")
''')


def install(tools_dir):
    """Write the fake as `<tools_dir>/bin/isescan.py` and return its path."""
    bin_dir = os.path.join(tools_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    path = os.path.join(bin_dir, "isescan.py")
    with open(path, "w") as handle:
        handle.write(SCRIPT.format(python=sys.executable))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def csv_row(seq_id="SYN001", family="IS3", cluster="IS3_1", start=4201, end=4400,
            strand="+", complete=True):
    """One CSV row in the column order the fake's header declares."""
    return ",".join(str(v) for v in (
        seq_id, family, cluster, start, end, end - start + 1, 1, start, end, "", "",
        100.0, 1, 20, 0, start + 10, end - 10, strand, 300, "1e-50", "", "c" if complete else "p",
        "", "")) + "\n"
