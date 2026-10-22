"""模型摘录中混入改写句时，不能使可核验证据一同丢失。"""

import unittest
from types import SimpleNamespace

from research_agent.core.models import Chunk
from research_agent.workflows.tasks import TaskHandlers


class StubLLM:
    def json(self, stage, data, *, max_tokens):
        return {"claims": [
            {"claim": "错误改写", "chunk_id": "chunk-1", "quote": "这段文字不在任何授权原文片段中。"},
            {"claim": "有效事实", "chunk_id": "chunk-1", "quote": "This is an exact source sentence from the paper."},
        ]}


class ExtractionTests(unittest.TestCase):
    def test_discards_unverifiable_quote_and_keeps_valid_claim(self):
        handler = TaskHandlers(StubLLM(), "test_team")
        chunk = Chunk("chunk-1", "real.pdf", 1, "Abstract",
                      "This is an exact source sentence from the paper.", "text", "sha")
        claims = handler.extract(SimpleNamespace(input="问题"), [chunk])
        self.assertEqual([row.claim for row in claims], ["有效事实"])


if __name__ == "__main__":
    unittest.main()
