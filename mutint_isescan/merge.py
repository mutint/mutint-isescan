"""Merging ISEScan's predictions into a reference's features: breseq's `ReadISEScan`, ported.

Pure: no Django, no files beyond the CSV. It operates on core's own reference model
(`mutint_import.annotate.model`, the Biopython-fed port of breseq's `cReferenceSequences`),
so what it produces is what `render_breseq_gff3` writes and `load_gff3` reads back -- the
canonical GFF3 the store keeps and igv.js draws. Nothing here knows what an experiment is.

**It is `cReferenceSequences::ReadISEScan` line for line** (breseq `reference_sequence.cpp`
1101-1303), which is what `breseq CONVERT-REFERENCE -s <csv>` runs, so a reference annotated
here and one annotated with breseq itself carry the same features. The rules, in order:

- With `replace_existing`, every existing `repeat_region` and `mobile_element` is removed
  first (1103-1125). breseq offers no add-only form; the panel's checkbox is the one thing
  added, because a reference whose annotation already names its IS elements from a curated
  GenBank may want ISEScan's beside them rather than instead.
- Each row becomes a `mobile_element` named by ISEScan's `cluster` (`IS3_25`), with the
  product `Complete <family> family IS element` for `type == c` and `Partial …` plus
  `flag_pseudo()` otherwise (1180-1193). breseq names the *cluster* and its GFF3 loader trims
  the copy suffix on reload, so the family reads `IS3` afterwards -- core's `load_gff3` does
  the same through `trim_repeat_name`, which is what the MOB machinery wants; the cluster
  survives in the `Note`.
- Strand `+`/`-` as given. **Empty strand is inferred** (1200-1256, added to breseq in 2024)
  from a gene wholly inside the element, with 50 bp of slack either side: hypothetical
  products are ignored, a feature spanning both strands is ignored, and two contained genes
  on different strands mean no answer. Anything else in the column is `+`.
- breseq keeps an element whose strand could not be inferred at strand **0** (its refusal is
  commented out). Core's locations are 1 or -1 throughout and `render_breseq_gff3` writes
  `-` for anything not 1, so 0 would silently become minus; **such an element is appended at
  +1 and named in `MergeResult.warnings`** instead, which is the one place this departs.
- A `seqID` the reference does not have is skipped and named in `unknown_seq_ids`. breseq
  would create an empty contig for it, which is never right for a stored reference.
- Names and products go through `make_safe`, breseq's `make_feature_strings_safe` (1294),
  and every contig's feature lists are rebuilt afterwards (1297).
"""

import csv
from collections import namedtuple

from mutint_import.annotate.model import Feature, FeatureLocation, make_safe

#: One row of ISEScan's CSV as this module reads it. `complete` is `type == 'c'`.
IsElement = namedtuple('IsElement', 'seq_id family cluster start_1 end_1 strand complete')

#: What `merge_isescan` did. `removed` and `added` count features; `unknown_seq_ids` names
#: contigs the CSV mentioned and the reference lacks; `warnings` are sentences for a person.
MergeResult = namedtuple('MergeResult', 'removed added unknown_seq_ids warnings')

#: The columns breseq reads, by header name. ISEScan writes more; these are the ones used.
REQUIRED_COLUMNS = ('seqID', 'family', 'cluster', 'isBegin', 'isEnd', 'strand', 'type')

#: Slack either side of an element when looking for a gene inside it. reference_sequence.cpp:1229
FLANK = 50

#: A gene whose product says this is ignored when inferring a strand. 1218-1220
HYPOTHETICAL = 'hypothetical'

MOBILE_ELEMENT = 'mobile_element'


def read_isescan_csv(path):
    """`[IsElement, ...]` from ISEScan's CSV, by header name.

    A missing required column is a `ValueError` naming it, as breseq's ASSERT does. A file
    with a header and no rows -- what ISEScan writes when its HMM hits yield no element -- is
    an empty list, which callers treat as "nothing found" rather than as a failure.
    """
    with open(path, newline='') as handle:
        reader = csv.DictReader(handle)
        header = [name.strip() for name in (reader.fieldnames or [])]
        for column in REQUIRED_COLUMNS:
            if column not in header:
                raise ValueError('Could not find column "%s" in %s.' % (column, path))
        elements = []
        for row in reader:
            if not any((value or '').strip() for value in row.values()):
                continue
            elements.append(IsElement(
                seq_id=(row.get('seqID') or '').strip(),
                family=(row.get('family') or '').strip(),
                cluster=(row.get('cluster') or '').strip(),
                start_1=int(row['isBegin']),
                end_1=int(row['isEnd']),
                strand=(row.get('strand') or '').strip(),
                complete=(row.get('type') or '').strip() == 'c'))
    return elements


def infer_strand(contig, start_1, end_1):
    """The strand of a gene wholly inside `[start_1 - FLANK, end_1 + FLANK]`, or 0.

    reference_sequence.cpp:1207-1256. Hypothetical products are skipped; a feature whose
    sublocations sit on both strands is skipped; two contained genes on different strands mean
    no answer (0), and so does finding none.
    """
    found = 0
    for feature in contig.features:
        if HYPOTHETICAL in (feature.product or '').lower():
            continue
        contained = True
        feature_strand = 0
        for location in feature.locations:
            if not (location.start_1 >= start_1 - FLANK and location.end_1 <= end_1 + FLANK):
                contained = False
                break
            if feature_strand == 0:
                feature_strand = location.strand
            elif feature_strand != location.strand:
                contained = False
                break
        if not contained or feature_strand == 0:
            continue
        if found == 0:
            found = feature_strand
        elif found != feature_strand:
            return 0
    return found


def merge_isescan(references, elements, replace_existing=True):
    """Add `elements` to `references` as `mobile_element` features. Returns a `MergeResult`.

    `references` is a `LoadedReferenceSequences` and is changed in place; render it with
    `render_breseq_gff3` afterwards. See the module docstring for the rules.
    """
    removed = 0
    if replace_existing:
        for contig in references.sequences.values():
            kept = [feature for feature in contig.features if not feature.is_repeat()]
            removed += len(contig.features) - len(kept)
            contig.features = kept
            contig.update_feature_lists()

    added = 0
    unknown = []
    warnings = []
    for element in elements:
        if element.seq_id not in references:
            if element.seq_id not in unknown:
                unknown.append(element.seq_id)
            continue
        contig = references[element.seq_id]

        feature = Feature(MOBILE_ELEMENT, name=make_safe(element.cluster))
        if element.complete:
            feature.product = make_safe('Complete %s family IS element' % element.family)
        else:
            feature.product = make_safe('Partial %s family IS element' % element.family)
            feature.flag_pseudo()

        if element.strand == '+':
            strand = 1
        elif element.strand == '-':
            strand = -1
        elif element.strand == '':
            strand = infer_strand(contig, element.start_1, element.end_1)
            if strand == 0:
                strand = 1
                warnings.append(
                    'The strand of %s at %s:%d-%d could not be inferred from a gene inside '
                    'it; it was annotated on the + strand.'
                    % (element.cluster, element.seq_id, element.start_1, element.end_1))
        else:
            strand = 1

        feature.locations.append(
            FeatureLocation(element.start_1, element.end_1, strand, feature=feature))
        contig.features.append(feature)
        added += 1

    for contig in references.sequences.values():
        contig.update_feature_lists()
    return MergeResult(removed, added, unknown, warnings)
