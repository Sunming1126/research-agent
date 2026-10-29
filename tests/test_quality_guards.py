"""Budget validity and strict evidence routing, including adversarial boundary cases."""
import pytest
from types import SimpleNamespace

from research_agent.rag.deep_probe import probe_pages
from research_agent.core.citations import citation_pairs
from research_agent.workflows.report_context import cited_evidence


@pytest.mark.parametrize('kwargs', [{'max_pages': True}, {'max_pages': 1.5},
    {'max_seconds': float('nan')}, {'max_seconds':float('inf')}, {'model_reserve':float('inf')},
    {'pages_per_document':0}, {'initial_page_limit':0}])
def test_invalid_limits_cannot_disable_resource_bounds(kwargs):
    args={'initial_page_limit':4,**kwargs}
    with pytest.raises(ValueError):
        probe_pages(SimpleNamespace(materials=[]), ['real query'], **args)


def test_crystal_orientation_is_not_used_as_paper_evidence():
    assert citation_pairs('沿[111]晶向对照 [1,2, p.9]') == {(1,9),(2,9)}


def test_grouped_page_never_authorizes_another_page_or_another_paper():
    rows=[{'evidence_id':'ev-1','ref':1,'page':3}, {'evidence_id':'ev-2','ref':2,'page':4},
          {'evidence_id':'ev-3','ref':3,'page':4}]
    assert [e['evidence_id'] for e in cited_evidence('论据 [1,2,p.4]',rows)] == ['ev-2']
