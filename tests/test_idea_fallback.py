"""模型审查循环耗尽预算时仍能给出保守、可回查的 Idea。"""

import unittest

from research_agent.core.models import Evidence
from research_agent.workflows.tasks import deterministic_idea, validate_citations


class IdeaFallbackTests(unittest.TestCase):
    def test_idea_uses_verified_claims_and_is_long_enough(self):
        ledger = [Evidence("ev-1", "模型显式表示启动子随机状态。", "A.pdf", 2, "a", "promoter states"),
                  Evidence("ev-2", "模型将慢反馈纳入动力学。", "B.pdf", 3, "b", "slow feedback")]
        markdown, used = deterministic_idea("比较随机性与慢反馈的模拟观察", ledger)
        self.assertIn("## 参考文献", markdown)
        self.assertIn("[1, p.2]", markdown)
        self.assertIn("[2, p.3]", markdown)
        body = markdown.split("## 参考文献", 1)[0]
        evidence = [{"evidence_id": x.evidence_id, "ref": i+1, "page": x.page}
                    for i, x in enumerate(ledger)]
        validate_citations(body, evidence, used)


if __name__ == "__main__":
    unittest.main()
