"""Frozen comparable context/count boundaries; no model accuracy is inferred."""
import pytest

from research_agent.rag.evidence_selection import query_window
from research_agent.rag.paper_selection import requested_paper_count


@pytest.mark.parametrize('left', [400, 450, 500, 550, 600, 650])
def test_repeated_terms_do_not_hide_later_joint_evidence(left):
    chars = list(('neutral ' * 260)[:1800])
    for offset, value in [(10, ' alpha '), (left, ' alpha '), (left+900, ' beta ')]:
        chars[offset:offset+len(value)] = value
    text = ''.join(chars)
    excerpt = query_window(text, ['alpha beta'])
    assert excerpt in text and len(excerpt) <= 1100
    assert 'alpha' in excerpt and 'beta' in excerpt


def test_substring_of_unrelated_word_is_not_a_query_hit():
    text = 'neutral ' * 180 + 'paragraph fragment paragraphs ' * 10
    assert query_window(text, ['rag']) == text[:1100]


@pytest.mark.parametrize('text,expected', [
    ('Find exactly two papers.', 2), ('Return no more than 1 paper.', 1),
    ('Return at least 3 papers.', None), ('Each topic: find two papers.', None),
    ('Find one paper per topic.', None), ('Overall, return three papers; each topic is independent.', 3),
    ('Return exactly zero papers.', 0), ('最多零篇', 0),
    ('总共三篇，但最多两篇', 2), ('最多3篇，且不超过2篇', 2),
    ('Return at most five papers, no more than two papers.', 2), ('Find twenty-one papers.', 21),
    ('Select at least three papers and return at most five papers.', 5),
    ('Each topic requires two papers, in total return six papers.', 6),
    ('Find three papers or more.', None), ('The same paper need not cover all topics.', None),
])
def test_multilingual_global_limits(text, expected):
    assert requested_paper_count(text) == expected
