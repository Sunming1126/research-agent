"""本地裁判必须绑定当前答案，不能用旧答案或不存在的证据刷分。"""
from evaluation.current_review import validate_judgment


def test_reject_stale_text_and_foreign_evidence():
    text = "本篇采用两个编码器分别编码问题和段落，并执行相似度检索。[1, p.2]"
    claims = [{"evidence_id": "ev-1"}]
    valid = {"id": "dpr-1", "coverage": "full", "quote": text, "reason": "当前原句及摘录支持", "evidence_ids": ["ev-1"]}
    stale = {**valid, "quote": "这是上一轮答案的摘录，不属于当前正文。"}
    foreign = {**valid, "evidence_ids": ["old-ev"]}
    accepted, rejected, _ = validate_judgment("C", text, claims, {"items": [stale, foreign, valid]})
    assert set(accepted) == {"dpr-1"}
    assert len(rejected) == 2


def test_reference_list_alone_cannot_earn_fact_credit():
    quote = "这是一条仅存在于参考文献中的旧版说明。"
    accepted, _, _ = validate_judgment("C", "正文\n## 参考文献\n" + quote,
        [{"evidence_id": "ev-1"}], {"items": [{"id": "dpr-1", "quote": quote,
         "reason": "引用", "evidence_ids": ["ev-1"]}]})
    assert not accepted


def test_partial_fact_does_not_receive_full_credit():
    text = "本篇比较参数效率，但没有提供检索库的规模信息。"
    row = {"id": "retro-2", "coverage": "partial", "segment_id": "s1",
           "reason": "仅对部分要求支持", "evidence_ids": ["ev-1"]}
    accepted, rejected, _ = validate_judgment("C", text, [{"evidence_id": "ev-1"}], {"items": [row]})
    assert not accepted and rejected
