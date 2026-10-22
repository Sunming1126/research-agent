"""检索模型把否定理由写入 papers 时，不能提交已明确排除的论文。"""
from research_agent.workflows.tasks import explicitly_excluded_paper


def test_negative_approval_and_named_filename_exclusion():
    assert explicitly_excluded_paper({"filename": "unknown.pdf", "reason": "该文属于题目明确排除项。"}, [])
    assert explicitly_excluded_paper({"filename": "2112.04426-RETRO.pdf", "reason": "讨论检索增强"}, ["RETRO"])


def test_mentioning_an_excluded_baseline_does_not_exclude_valid_paper():
    assert not explicitly_excluded_paper({"filename": "new-system.pdf", "reason": "与 RETRO 比较，提出新的按需检索方法。"}, ["RETRO"])
    assert not explicitly_excluded_paper({"filename": "new-RETROSPECTIVE.pdf", "reason": "满足本题需求。"}, ["RETRO"])


def test_exclusion_variants_and_compound_method_names():
    assert explicitly_excluded_paper({"filename": "unknown.pdf", "reason": "仅做检索基准评测，按题目排除。"}, [])
    assert not explicitly_excluded_paper({"filename": "2310.11511-Self-RAG.pdf", "reason": "研究自我反思检索。"}, ["RAG"])
    assert explicitly_excluded_paper({"filename": "2005.11401-RAG.pdf", "reason": "研究检索增强。"}, ["RAG"])
