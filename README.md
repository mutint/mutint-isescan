# mutint-isescan

Run [ISEScan](https://github.com/xiezhq/ISEScan) on an experiment's reference genome, from
inside [MutInt](https://github.com/mutint/mutint-core), and annotate the insertion sequences
it predicts as `mobile_element` features — what breseq's manual recommends doing with
`CONVERT-REFERENCE -s`, so an IS insertion is called as one MOB rather than two junctions.

It is the first **reference annotator**: a component that registers a panel on the Import
data page's reference tabs and a step that runs on the reference after it lands. The merge is
a Python port of breseq's `ReadISEScan`, applied to core's own reference model, and the result
is installed through core's `install_annotation`, which re-annotates every mutation. breseq
is not a requirement.

**It needs a worker** — a run is minutes to an hour — so it is enqueued on `django.tasks`;
`./mutint start` runs one for you.

**The tool is provisioned by `tools.txt`** from bioconda, like every other external tool, so
nothing is installed by hand. Where it is somehow absent, the panel says so and offers
nothing; an `isescan.py` on `PATH` is used if it is there.

## Installing

```bash
git submodule add ../mutint-isescan mutint-isescan
```

MIT licensed. See [mutint-core](https://github.com/mutint/mutint-core) for the platform.
