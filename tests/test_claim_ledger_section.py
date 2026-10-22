"""综述必须把已核验的关键事实显式带入正文。"""

import unittest

from research_agent.core.models import Evidence
from research_agent.workflows.tasks import render_claim_ledger_section


class ClaimLedgerSectionTests(unittest.TestCase):
    def test_each_verified_claim_has_filename_page_and_id(self):
        ledger = [Evidence("ev-1", "方法采用双编码器。", "A.pdf", 2, "chunk-a", "dual encoders"),
                  Evidence("ev-2", "在基准上报告结果。", "B.pdf", 3, "chunk-b", "benchmark results")]
        section, used = render_claim_ledger_section(ledger, {"A.pdf": 1, "B.pdf": 2})
        self.assertIn("方法采用双编码器。 [1, p.2]", section)
        self.assertIn("在基准上报告结果。 [2, p.3]", section)
        self.assertEqual(used, ["ev-1", "ev-2"])


if __name__ == "__main__":
    unittest.main()
