# Annotating IS elements

breseq calls an insertion of a mobile element as one **MOB** mutation only when the reference
genome's annotation names the element's family, and breseq's manual recommends predicting those
families on the reference with [ISEScan](https://github.com/xiezhq/ISEScan) and merging them
in with `CONVERT-REFERENCE -s`. The **mutint-isescan** component does that from the browser.

## Where it is

On an experiment's **Import data** page, the **Reference Sequence** tab (before there is a
reference) and the **Update Annotation** tab (after) carry a panel headed **Run ISEScan IS
elements**. Tick the box and ISEScan runs after the reference is set or its annotation is
updated. On the Update Annotation tab, **Run annotators** runs it against the reference the
experiment already has, with nothing uploaded.

Two options:

| option | default | what it does |
|---|---|---|
| **Replace the existing repeat annotation** | on | every `repeat_region` and `mobile_element` already annotated is removed before ISEScan's are added, which is what breseq's own merge does. Deselect to add ISEScan's beside a curated annotation. |
| **Complete IS elements only** | off | ISEScan's `--removeShortIS`. Otherwise a partial element is annotated too, marked pseudo, so it is drawn and named but does not vote for its family's consensus sequence. |

## What happens

The run is queued on the background worker, because ISEScan takes minutes on a bacterial
genome, and the panel's run list shows it: queued, running, and then one of three outcomes.

- **Installed** — the elements were merged into the annotation and every mutation in the
  experiment was re-annotated against it. The row says how many repeat features were removed
  and how many elements added. The elements appear in the genome browser's gene track and in
  the MOB machinery from then on.
- **Nothing found** — ISEScan predicted no elements, and the annotation was left exactly as
  it was.
- **Failed** — the row says why, and the job's log has ISEScan's own output.

A run can be stopped from the **Jobs** page. Each element is named by ISEScan's cluster
(`IS3_25`) and, once stored, by its family (`IS3`), with the cluster kept in the feature's
note. An element whose strand ISEScan left blank is placed on the strand of a gene inside it,
and on the + strand with a note in the log when no gene decides it.

## What it needs

ISEScan itself, which `tools.txt` provisions from bioconda like every other tool. **Until the
package for Apple Silicon is published there, that line is commented out and the panel says
the tool is not installed**; the box is disabled and nothing else changes. An `isescan.py`
on the `PATH` is used if it is there. The run's threads default to every core, capped by
`MUTINT_ISESCAN_THREADS` when a deployment sets one; `MUTINT_ISESCAN_TIMEOUT_SECONDS` (six
hours) is the most a run may take.
