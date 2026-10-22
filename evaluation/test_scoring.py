"""本地诊断评分的公式与完整轮次约束。"""

import unittest

from evaluation.scoring import score_chat, score_retrieval, score_survey, score_idea, score_metric, aggregate


class ScoringTests(unittest.TestCase):
    def test_chat_and_retrieval(self):
        self.assertEqual(score_chat({"answer": "回答", "clarifying_question": "研究对象？"}), 5)
        self.assertEqual(score_chat({"answer": "回答", "clarifying_question": ""}), 3)
        self.assertEqual(score_retrieval(["A.pdf", "a.PDF", "X.pdf"], ["a.pdf", "B.pdf"]), 6.75)

    def test_survey_and_idea(self):
        self.assertEqual(score_survey(6, 10, 1, 4, 5), {"facts": 6.0, "citations": 8.0, "total": 14.0})
        self.assertEqual(score_idea({"motivation", "route", "experiment", "benchmark", "metrics", "bottleneck", "complement", "feasibility"}), 15)

    def test_metric_tolerance(self):
        self.assertEqual(score_metric(100, 100, .01), 4)
        self.assertAlmostEqual(score_metric(100, 110, .01), .44)
        self.assertEqual(score_metric(None, 2, .01), 0)

    def test_full_round_requires_five_per_category(self):
        sample = {letter: [float({"A": 5, "B": 15, "C": 25, "D": 40, "E": 15}[letter])] for letter in "ABCDE"}
        self.assertEqual(aggregate(sample, mode="diagnostic")["total"], 100)
        with self.assertRaises(ValueError):
            aggregate(sample, mode="official_shape")


if __name__ == "__main__":
    unittest.main()
