"""The port of breseq's ReadISEScan, against core's reference model. Pure: no database."""

import os
import shutil
import tempfile

from django.test import SimpleTestCase

from mutint_import.annotate.gff3 import load_gff3, render_breseq_gff3

from mutint_isescan import merge
from mutint_isescan.merge import IsElement

HEADER = "##gff-version 3\n##sequence-region\tSYN001\t1\t3000\n"
ROWS = (
    "SYN001\t.\tCDS\t210\t590\t.\t+\t0\tID=tnpA;Name=tnpA;Note=IS3 transposase;transl_table=11\n"
    "SYN001\t.\tCDS\t810\t990\t.\t-\t0\tID=hyp;Name=hyp;Note=hypothetical protein;transl_table=11\n"
    "SYN001\t.\tCDS\t1210\t1390\t.\t-\t0\tID=res;Name=res;Note=resolvase;transl_table=11\n"
    "SYN001\t.\tCDS\t1410\t1590\t.\t+\t0\tID=int;Name=int;Note=integrase;transl_table=11\n"
    "SYN001\t.\trepeat_region\t2001\t2200\t.\t+\t0\tName=IS150;Note=repeat region\n"
    "SYN001\t.\trepeat_region\t2401\t2600\t.\t-\t0\tName=IS150;Note=repeat region\n"
)
FASTA = "##FASTA\n>SYN001\n" + ("ACGTTGCAAC" * 300) + "\n"


def element(start, end, strand="+", complete=True, cluster="IS3_1", family="IS3",
            seq_id="SYN001"):
    return IsElement(seq_id, family, cluster, start, end, strand, complete)


class MergeTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "ref.gff3")
        with open(self.path, "w") as handle:
            handle.write(HEADER + ROWS + FASTA)
        self.references = load_gff3(self.path)

    def _repeats(self, references=None):
        contig = (references or self.references)["SYN001"]
        return [(loc.feature.name, loc.start_1, loc.end_1, loc.strand,
                 loc.feature.pseudogene, loc.feature.product)
                for loc in contig.repeat_locations]

    def test_existing_repeats_are_replaced(self):
        result = merge.merge_isescan(self.references, [element(100, 600)])
        self.assertEqual((2, 1, [], []), tuple(result))
        self.assertEqual([("IS3_1", 100, 600, 1, False, "Complete IS3 family IS element")],
                         self._repeats())

    def test_existing_repeats_are_kept_when_asked(self):
        result = merge.merge_isescan(self.references, [element(100, 600)],
                                     replace_existing=False)
        self.assertEqual((0, 1), (result.removed, result.added))
        self.assertEqual(["IS3_1", "IS150", "IS150"],
                         [name for name, *_ in self._repeats()])

    def test_a_partial_element_is_pseudo(self):
        merge.merge_isescan(self.references, [element(100, 600, complete=False)])
        name, _s, _e, _strand, pseudo, product = self._repeats()[0]
        self.assertTrue(pseudo)
        self.assertEqual("Partial IS3 family IS element", product)

    def test_the_strand_is_taken_as_given(self):
        merge.merge_isescan(self.references, [element(100, 600, "+"),
                                              element(700, 1000, "-"),
                                              element(1100, 1700, "x")])
        self.assertEqual([1, -1, 1], [r[3] for r in self._repeats()])

    def test_an_empty_strand_is_inferred_from_a_gene_inside(self):
        # tnpA sits at 210-590 on +, wholly inside 200-600 (with 50 bp slack either side).
        result = merge.merge_isescan(self.references, [element(200, 600, "")])
        self.assertEqual(1, self._repeats()[0][3])
        self.assertEqual([], result.warnings)
        self.assertEqual(1, merge.infer_strand(self.references["SYN001"], 200, 600))

    def test_a_hypothetical_gene_does_not_vote(self):
        # hyp at 810-990 is the only gene inside 800-1000, and it is hypothetical.
        self.assertEqual(0, merge.infer_strand(self.references["SYN001"], 800, 1000))
        result = merge.merge_isescan(self.references, [element(800, 1000, "")])
        self.assertEqual(1, self._repeats()[0][3])
        self.assertEqual(1, len(result.warnings))
        self.assertIn("IS3_1", result.warnings[0])
        self.assertIn("+ strand", result.warnings[0])

    def test_conflicting_genes_mean_no_answer(self):
        # res (-) and int (+) are both inside 1200-1600.
        self.assertEqual(0, merge.infer_strand(self.references["SYN001"], 1200, 1600))
        result = merge.merge_isescan(self.references, [element(1200, 1600, "")])
        self.assertEqual(1, self._repeats()[0][3])
        self.assertEqual(1, len(result.warnings))

    def test_an_unknown_contig_is_skipped_and_named(self):
        result = merge.merge_isescan(self.references, [element(100, 600, seq_id="NOPE"),
                                                       element(100, 600)])
        self.assertEqual(["NOPE"], result.unknown_seq_ids)
        self.assertEqual(1, result.added)

    def test_names_and_products_are_made_safe(self):
        merge.merge_isescan(self.references, [element(100, 600, cluster="IS3|odd",
                                                      family="IS|3")])
        name, *_rest, product = self._repeats()[0]
        self.assertNotIn("|", name)
        self.assertNotIn("|", product)

    def test_the_merge_round_trips_through_the_stored_form(self):
        merge.merge_isescan(self.references, [element(100, 600),
                                              element(200, 600, "", complete=False,
                                                      cluster="IS3_2"),
                                              element(1100, 1700, "-", cluster="IS3_3")])
        rendered = os.path.join(self.tmp, "merged.gff3")
        with open(rendered, "w") as handle:
            handle.write(render_breseq_gff3(self.references))
        again = load_gff3(rendered)

        repeats = self._repeats(again)
        self.assertEqual(3, len(repeats))
        # The loader trims the copy suffix, so the family reads IS3 -- what the MOB machinery
        # wants -- and the cluster survives in the product.
        self.assertEqual({"IS3"}, {name for name, *_ in repeats})
        self.assertEqual([False, True, False], [r[4] for r in repeats])
        # The consensus is the complete copy alone: the pseudo copy does not vote.
        self.assertEqual(501, len(again.repeat_family_sequence("IS3", 1)))
        with open(rendered) as handle:
            text = handle.read()
        self.assertIn("Note=Complete IS3 family IS element", text)
        self.assertIn("Pseudo=true", text)
        self.assertNotIn("IS150", text)


class CsvTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, text):
        path = os.path.join(self.tmp, "ref.fasta.csv")
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_rows_are_read_by_header_name_in_any_column_order(self):
        path = self._write("type,strand,isEnd,isBegin,cluster,family,seqID,extra\n"
                           "c,+,600,100,IS3_1,IS3,SYN001,x\n"
                           "p,,1000,800,IS3_2,IS3,SYN001,y\n")
        elements = merge.read_isescan_csv(path)
        self.assertEqual([IsElement("SYN001", "IS3", "IS3_1", 100, 600, "+", True),
                          IsElement("SYN001", "IS3", "IS3_2", 800, 1000, "", False)],
                         elements)

    def test_a_header_only_file_is_nothing_found(self):
        path = self._write("seqID,family,cluster,isBegin,isEnd,strand,type\n")
        self.assertEqual([], merge.read_isescan_csv(path))

    def test_a_missing_column_is_refused_by_name(self):
        path = self._write("seqID,family,cluster,isBegin,isEnd,type\nSYN001,IS3,IS3_1,1,2,c\n")
        with self.assertRaises(ValueError) as caught:
            merge.read_isescan_csv(path)
        self.assertIn('"strand"', str(caught.exception))
