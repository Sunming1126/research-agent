"""模型长文审查失败后，保底综述仍须可提交且逐条可溯源。"""

import unittest

from research_agent.core.models import Evidence
from research_agent.workflows.tasks import deterministic_survey, validate_citations


class FallbackTests(unittest.TestCase):
    def test_five_papers_and_800_characters(self):
        ledger = [Evidence(f"ev-{index}",
                           "该研究从授权文献讨论检索与生成的具体方法，并报告其评测设置及可比较的局限。",
                           f"paper-{paper}.pdf", 1, f"chunk-{index}", "exact source sentence")
                  for index, paper in enumerate(range(1, 6), 1)]
        markdown, used = deterministic_survey("比较检索与生成方法", ledger)
        self.assertEqual(len(used), 5)
        self.assertIn("## 参考文献", markdown)
        body = markdown.split("## 参考文献")[0]
        self.assertGreaterEqual(len("".join(body.split())), 800)
        evidence = [{"evidence_id": row.evidence_id, "ref": index,
                     "page": row.page} for index, row in enumerate(ledger, 1)]
        validate_citations(body, evidence, used, minimum=5)


if __name__ == "__main__":
    unittest.main()
