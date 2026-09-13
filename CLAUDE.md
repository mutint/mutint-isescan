# CLAUDE.md — mutint-isescan

Guidance for Claude Code working in this repository.

It is a **submodule of `mutint`**. Edit it **here**, in the suite-root checkout, never in
`mutint/mutint-isescan` — that copy is on a detached HEAD and a commit made there is reachable
only by SHA inside that one clone. See the suite `CLAUDE.md`.

---

## What this is

A panel on the Import data page's reference tabs that runs ISEScan on the stored reference
and merges the insertion sequences it predicts into the annotation as `mobile_element`
features -- what breseq's manual recommends doing with `CONVERT-REFERENCE -s`, so an IS
insertion is called as one MOB rather than two junctions. It is the first **reference
annotator**: the first consumer of core's `annotator_registry`, and the case that registry
was built for.

The merge is a Python port of breseq's `cReferenceSequences::ReadISEScan`, applied to core's
own reference model, and the result is installed through core's `install_annotation`, which
re-annotates every mutation. breseq is not a requirement of this plugin.

---

## The pieces

| file | what |
|---|---|
| `merge.py` | pure: the `ReadISEScan` port -- the CSV, the strand inference, the replace rule |
| `runner.py` | pure: the argv, where the CSV lands, the PATH, whether the tool is here |
| `annotator.py` | what the registry calls: `clean`, `run` (queues a job), the panel's context, the run rows |
| `tasks.py` | the `@task`: copy the reference, run ISEScan, merge, install under the import lock |
| `models.py` | `IsescanRun`, and the receiver that owns its directory |
| `views.py` | the run list the panel polls, deleting a finished run, and serving a run's files |
| `templates/isescan/panel.html`, `static/mutint_isescan/panel.js` | the panel body and its run table |

`merge.py` and `runner.py` are pure on purpose, in the shape mutint-breseq's `runner.py` and
`pairing.py` are: the rules most likely to be changed by accident are what an ISEScan row
becomes and where its output is looked for, and both are testable without the tool.

---

## Things that are load-bearing

### The tool comes from `tools.txt`, and the plugin never assumes it is there

`tools.txt` names `isescan`, which bioconda builds for every platform MutInt runs on --
osx-arm64 since `1.7.3 h9e3228c_1`, and the line was commented out until that build existed,
because the entry script installs every component's tools in one micromamba solve and a spec
with no build for the host fails the whole solve. The package depends on hmmer, blast and
FragGeneScan, which ISEScan calls by bare name, so `runner.tool_environment` puts
`env/tools/bin` first on the run's PATH.

Absence is still handled rather than assumed away: `runner.available()` asks
`tools.tool_path`, which looks in the managed directory and then on PATH, and the panel
disables its enable box and says the tool is not installed when the answer is no. Nothing
else in the plugin knows whether the tool is present.

**There is deliberately no per-platform machinery** -- no platform prefix on `tools.txt`
lines, no entry-script change. A line that is present on one platform and absent on another
is a second thing to keep true.

### The merge is breseq's, line for line, with one stated departure

`merge.merge_isescan` reproduces `reference_sequence.cpp` 1101-1303: remove every existing
repeat (the panel's box makes that optional, which breseq does not offer), one `mobile_element`
per row named by the cluster, `Complete`/`Partial <family> family IS element` with the partial
flagged pseudo, strand as given or inferred from a non-hypothetical gene wholly inside the
element with 50 bp of slack, unknown strings meaning `+`.

**The departure**: breseq keeps an element whose strand it could not infer at strand 0, its
refusal being commented out. Core's locations are 1 or -1 and `render_breseq_gff3` writes `-`
for anything not 1, so 0 would silently become minus. Such an element goes in at +1 and is
named in `MergeResult.warnings`, which the task writes to the job log.

**The family reads `IS3`, and the cluster is in the note.** breseq names the feature by
ISEScan's cluster (`IS3_25`); core's GFF3 loader applies `trim_repeat_name` to every repeat on
reload, as breseq's own does, so the family is what the MOB machinery sees. `test_merge`
asserts the render → `load_gff3` round trip names the family `IS3` and keeps the pseudo flag
on the partial -- the latter needing core's Pseudo-on-`mobile_element` change, which shipped
with this plugin.

### Where the CSV lands is ISEScan's rule, so the reference is copied first

`isescan.py` takes `org` as the basename of the *directory* the sequence file is in and writes
`<output>/<org>/<basename(seqfile)>.csv`. The task copies the stored `reference.fasta` into
the run's own directory before running, so the CSV path is known before the run starts
(`runner.csv_path`) -- and so a reference renamed or re-annotated during the run cannot
change under it. Two "nothing found" shapes both mean `unchanged`: no HMM hit means no CSV at
all; hits with no element mean a header-only CSV. `unchanged` is a finished state and not a
failure, brefito's behaviour rather than breseq's strip-and-add-nothing.

### `run` queues and returns; the task installs under a waiting lock

The registry's contract is a prompt answer with a message, so `annotator.run` creates the row,
enqueues `tasks.run_isescan` through `mutint_jobs` (so it is on `/jobs/` with a name, an owner
and a Cancel button) and returns "queued as job N" -- or, under the immediate backend, what
the finished run did. It declines rather than raises when the tool is absent or a run for the
experiment is still under way, since two would race on `install_annotation`.

The task installs through `import_lock.hold_waiting`, promoted from mutint-breseq's
`_wait_for_import_lock` for this: what the merge produces is a new annotation for the same
sequence and installing it re-annotates every mutation, an import-class write, and a worker
would rather wait for a web drop than throw its run away. The cancellation flag is the wait's
`check`.

### Cleanup keeps ISEScan's outputs, drops the scratch, and the row links what is kept

A run's directory keeps everything ISEScan wrote under `out/` -- the CSV the merge read, the
same table as TSV and two aligned-text spellings, ISEScan's own GFF3 of the prediction, the
per-family `.sum`, and the elements' and transposase ORFs' sequences (`is.fna`, `orf.faa`,
`orf.fna`); about 200 KB for a bacterial genome. The FASTA copy and ISEScan's `proteome/` and
`hmm/` directories go, whatever the outcome. **Every kept file is a link on the run's row**,
through `/isescan/runs/<pk>/files/<name>`: the prediction is the evidence behind an installed
annotation, and a reader who cannot see it has to take the annotation on trust. Read access is
the bar, as for the reference download; the name is checked against `output_files()` rather
than resolved, so no typed path reaches the filesystem; and every refusal is a 404, so the
endpoint cannot say which run ids exist. Every ISEScan output is text and is served as such,
since core's extension table knows nothing of `.faa`, `.sum` or `.raw`.

`IsescanRun`'s `post_delete` receiver removes the whole directory, and because `experiment`
cascades, deleting an experiment reaches it. `run_delete` refuses an unfinished run for the
reason mutint-breseq's does: the receiver would pull the directory out from under a live
ISEScan.

### The enable box is core's, and the panel's script disables it

Core draws the fieldset and the `.annotator-enable` box; the panel's body is this plugin's.
When the tool is not installed, `panel.js` walks up to its fieldset and disables that box, and
the template disables its own inputs. The run table polls `/isescan/runs` while a run is
unfinished and says *waiting for a worker* for a queued row the queue has not claimed --
mutint-breseq's rule, for the same reason.

---

## What it deliberately does not do

- **No nav entry, no import tab, no page of its own.** What this does is a step on the
  reference, and the reference tabs are where a person is already looking at it.
- **No import handler.** Nothing is dropped; the input is the stored reference.
- **No rebuilder.** It derives nothing from the mutations; `install_annotation` asks for every
  registered rebuild itself.
- **No export type, no example dataset.**
- **No add-only merge beyond the one checkbox.** breseq replaces; the checkbox is the whole
  of the departure, so a curated annotation can keep its own repeats beside ISEScan's.

---

## Tests

```bash
cd mutint && ./mutint test mutint_isescan
```

Until the plugin is a submodule of `mutint`, run them from mutint-core with a settings module
that adds the app, the way mutint-example's are run:

```bash
cd mutint-core
cat > /tmp/isescan_settings.py <<'PY'
from config.settings_local import *  # noqa: F401,F403
INSTALLED_APPS = INSTALLED_APPS + ["mutint_isescan"]
PY
DJANGO_SETTINGS_MODULE=isescan_settings PYTHONPATH=/tmp:../mutint-isescan ./mutint test mutint_isescan
```

**39 tests.** `tests/fake_isescan.py` is a **real executable on disk**, installed into a temp tools
directory, that records its argv and PATH and writes a CSV by ISEScan's own path rule -- the
two things most likely to be wrong, which a `subprocess.run` patch would assert against the
call rather than against a process that has to start. `test_merge` uses a GFF3 written for it
rather than core's fixture, so a gene sits inside each shape of element the strand inference
has to handle.

One gotcha, mutint-breseq's: `tools.tool_path` falls back to `PATH` by design, so a test
asserting "isescan.py is missing" must clear `PATH` as well as empty the tools directory.
