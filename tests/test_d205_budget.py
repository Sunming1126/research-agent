from test_d_formal_20261102 import setup,case
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.session import CaseSession


def test_d_source_generation_after_90000_tokens_keeps_finite_output_limit(tmp_path):
    runner,fake=setup(tmp_path,'D-02')
    with CaseSession('D-02',1780).activate():
        llm=ResearchAgentEngine(runner.settings).llm_factory()
        llm.total_tokens=90000
        def completion(stage,prompt,max_tokens):
            assert max_tokens==24000
            return fake.completion(stage,prompt,max_tokens)
        llm.completion=completion
        runner.llm=llm
        assert runner.prepare_generated_implementation(case('D-02')).is_file()
    assert fake.calls==1 and llm.budget==float('inf')
