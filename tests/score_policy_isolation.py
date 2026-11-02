"""Project only explicit current-rule edits when checking old freeze snapshots.

The historical hashes stay untouched. All other functions/imports/AST nodes must
still equal the teammate baseline; this is not a blanket changed-file exemption.
"""
import ast
import subprocess


ALLOWED = {
    'src/research_agent/contracts/validation.py': (
        {'validate_report_limits'},
        {'report_rules': {'body_character_limit', 'counted_report_body', 'citation_marker_count'}},
    ),
    'src/research_agent/evidence/reporting.py': ({'report_requirements'}, {}),
    'src/research_agent/integrations/idea_prompt.py': (set(), {}),
    'src/research_agent/workflows/idea_workflow.py': (set(), {}),
}


def baseline_projection(name, content, root):
    functions, imports = ALLOWED[name]
    baseline = subprocess.check_output(['git', 'show', 'cd733b2:' + name], cwd=root).decode('utf-8')
    if name == 'src/research_agent/workflows/idea_workflow.py':
        added = 'from ..contracts.report_rules import body_character_limit\n'
        assert content.count(added) == 1
        assert content.count("'model_body_characters':body_character_limit('Idea')") == 1
        content = content.replace(added, '').replace(
            "'model_body_characters':body_character_limit('Idea')", "'model_body_characters':6000")
    if name == 'src/research_agent/integrations/idea_prompt.py':
        # Project only the two literal limit descriptions, keeping every other
        # quality requirement, audit instruction and helper function protected.
        changes = {
            '正文至少800、至多4500字符，参考文献节和标题行不计上限；建议1800～3200字符；':
            '正文至少800、至多6000字符，官方建议1800～3200；',
            '正文仍限4500字符，参考文献节和标题行不计上限。': '全篇仍限6000字符。',
        }
        for new, old in changes.items():
            assert content.count(new) == 1
            content = content.replace(new, old)

    def retained(text, current):
        tree = ast.parse(text)
        found = set()
        rows = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in functions:
                found.add(node.name)
                continue
            if isinstance(node, ast.ImportFrom) and node.module in imports:
                assert current and node.level == 1
                assert {a.name for a in node.names} == imports[node.module]
                assert all(a.asname is None for a in node.names)
                continue
            rows.append(ast.dump(node, include_attributes=False))
        assert found == functions
        return rows
    assert retained(content, True) == retained(baseline, False), name + ': unrelated source changed'
    return baseline
