"""模型写作和审查只接收已核验事实所需的最小字段。"""

import unittest

from research_agent.workflows.tasks import (correct_paper_ids, model_evidence_rows,
                                            remove_unsupported_authors)


class ModelEvidenceTests(unittest.TestCase):
    def test_compact_rows_keep_identity_but_remove_raw_quote(self):
        rows = [{"evidence_id": "ev-1", "claim": "论文提出方法。", "filename": "A.pdf",
                 "page": 2, "ref": 1, "chunk_id": "c1", "quote": "The paper presents a method."}]
        result = model_evidence_rows(rows)
        self.assertEqual(result, [{"evidence_id": "ev-1", "claim": "论文提出方法。",
                                   "filename": "A.pdf", "page": 2, "ref": 1}])

    def test_unsupported_author_attribution_is_removed(self):
        evidence = [{"claim": "Tianyu Wu 提出混合马尔可夫模型。"}]
        draft = "Weiss等人提出模型[1, p.1]；Wu等人给出实验[1, p.2]。"
        self.assertEqual(remove_unsupported_authors(draft, evidence),
                         "该论文提出模型[1, p.1]；Wu等人给出实验[1, p.2]。")

    def test_single_digit_paper_id_typo_is_corrected(self):
        names = ["2512.18442.pdf", "2512.20887.pdf"]
        self.assertEqual(correct_paper_ids("2912.18442 与 2512.20887", names),
                         "2512.18442 与 2512.20887")


if __name__ == "__main__":
    unittest.main()
