"""Record user-provided official failure and reject adoption; no Agent changes."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / '协同开发/research-agent-d-contract-20261106'
ROUND = REPO / 'team_share/rounds/2026-11-06-d30'
CONTROL = ROUND / 'control/2026-11-07-baseline-preservation'
DELIVERY = ROOT / '交付物/2026-11-06-d30-2e6f51a643ac'
REF = '2e6f51a643acf66920612f83555a1c3a59d9d8b8'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


CONTROL.mkdir(parents=True, exist_ok=True)
# Retain the previous handoff state and every raw experiment byte.
previous = json.loads((ROUND / 'archive-sha256.json').read_text(encoding='utf-8'))
for relative, entry in previous.items():
    assert digest(ROUND / relative) == entry['sha256'], relative
for name in ('manifest.json', '提交说明.md', '交付材料校验.json'):
    target = CONTROL / 'prior-handoff' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(DELIVERY / name, target)

baseline_path = ROOT / '协同开发/research-agent-friend-development/docs/testing/2026-11-04-controlled-canary/latest-f8116b95.json'
baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
shutil.copyfile(baseline_path, CONTROL / 'official-80p9-reference.json')
scores = {'A-03': 5, 'B-01': 12.8, 'B-02': 1.5, 'B-03': 14.3,
          'C-03': 24.3, 'C-05': 25, 'D-01': 40, 'D-02': 0,
          'D-05': 39.3, 'E-01': 10.1, 'E-04': 5.6}
usage = {
    'A-03': (1, 513, 12.1, 4.2), 'B-01': (13, 76697, 427, 22.9),
    'B-02': (11, 62159, 446, 16.7), 'B-03': (13, 75587, 400, 17.7),
    'C-03': (4, 30905, 141, 4.0), 'C-05': (2, 16579, 111, 0.0),
    'D-01': (6, 42234, 463, 166), 'D-02': (5, 50419, 326, 167),
    'D-05': (5, 41405, 517, 216), 'E-01': (4, 29853, 144, 3.9),
    'E-04': (8, 78874, 439, 4.9),
}
assert sum(row[1] for row in usage.values()) == 505225
assert sum(row[0] for row in usage.values()) == 72
result = {
    'recorded_at_china': '2026-11-07',
    'origin': 'User-provided competition screenshots and liaison feedback; not a rerun',
    'completed_at_china_visible': '2026-11-06 21:29',
    'eval_id': None, 'platform_upload_id': None,
    'artifact_binding': {
        'user_confirmed_prepared_version': True,
        'prepared_source_commit': REF,
        'prepared_tar_sha256': '98f3fc456473eb51531af72e01c8135b83dbc69d6991c16e319d6a92020bb892',
        'platform_digest_verified': False,
        'note': 'User confirmed uploading our prepared version. Platform digest/eval ID have not been supplied.',
    },
    'official_total_visible': None,
    'precision_note': 'Case scores are transcribed displayed rounded values; no exact total or exact positive deltas inferred.',
    'tokens': 505225, 'calls': 72, 'wall_seconds': 5533,
    'cases': {case: {'displayed_score': score, 'protocol_status': '协议有效',
                     'calls': usage[case][0], 'tokens': usage[case][1],
                     'model_seconds': usage[case][2], 'processing_seconds': usage[case][3]}
              for case, score in scores.items()},
    'D02_confirmed_feedback': {
        'solve_http_status': 200, 'schema_passed': True,
        'model_calls': 5, 'all_model_calls_successful': True, 'tokens': 50419,
        'run_assigned_case_invoked': True, 'execute_authorized_experiment_invoked': True,
        'experiment_status': 'failed', 'completed_conditions': 0, 'reported_conditions': 2,
        'condition_names': None, 'first_error': None, 'stderr': None,
        'raw_user_report': '/solve 返回 HTTP 200，协议校验通过。run_assigned_case 和 execute_authorized_experiment 都被正常调用。但实验最终返回 status: failed，两个条件均未完成。',
    },
    'adoption_decision': 'rejected',
    'reasons': ['D-02 fell from 31.055555555555554 to 0', 'E-04 fell from 9 to displayed 5.6',
                'B-02 remained 1.5; accuracy benefit not demonstrated',
                '80.9 source identity and matching input/environment fingerprints remain unbound'],
    'causal_conclusion': None,
    'new_target_model_calls_in_this_audit': 0,
}
save(CONTROL / 'negative-official-feedback.json', result)
save(ROOT / '.session-audit/d02-official-feedback-20261107.json', result)

image_names = ['codex-clipboard-c5c1287c-ff29-450e-893f-f2ccc2078eff.png',
               'codex-clipboard-3cd28368-5fe1-4bc1-9883-b7d73170fff9.png',
               'codex-clipboard-6ee00473-43a5-47bd-b40f-5b1d77811af2.png']
for name in image_names:
    target = CONTROL / 'user-provided-screenshots' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path('C:/Users/lenovo/AppData/Local/Temp') / name, target)

git = ['git', '-c', f'safe.directory={REPO}', '-C', str(REPO)]
changed = subprocess.check_output(git + ['diff', '--name-only', '046324b', REF, '--', 'src'], text=True).splitlines()
save(CONTROL / 'candidate-boundary-audit.json', {
    'comparison_ref': '046324bae9425aa0a1aeff49b9d8128831d18479',
    'comparison_is_proven_80p9': False, 'candidate': REF,
    'changed_runtime_files': changed, 'changed_runtime_files_count': len(changed),
    'single_module_gate': False, 'shared_D_also_affects_D01': True,
    'actual_80p9_diff': None,
})

notice = ('> 2026-11-07：当前采用结论为拒绝。本包仅保留为历史候选，暂不继续提交。'
          '用户反馈所上传版本D-02=0、E-04约5.6，未守住80.9参考；'
          '80.9原始tar与源码身份仍缺绑定。原工程检查不撤销，但不能作为得分无退化证明。'
          '后续仅从已确证80.9源码派生单模块候选；详见control/2026-11-07-baseline-preservation。\n\n')
readme = ROUND / 'README.md'
content = readme.read_text(encoding='utf-8')
if not content.startswith('> 2026-11-07'):
    content = notice + content
content = content.replace('## 当前可用交付', '## 历史交付（当前不采用）').replace('已准备可直接提交的镜像tar', '当时准备的镜像tar')
readme.write_text(content, encoding='utf-8')
instructions = DELIVERY / '提交说明.md'
content = instructions.read_text(encoding='utf-8')
if not content.startswith('> 2026-11-07'):
    content = notice + content
content = content.replace('直接向比赛平台上传本目录的.tar文件。',
                          '当前请勿继续提交本目录的.tar文件；下述为原交付操作记录。')
instructions.write_text(content, encoding='utf-8')
manifest_path = DELIVERY / 'manifest.json'
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
assert digest(DELIVERY / 'research-assistant-d30-2e6f51a643ac.tar') == manifest['sha256']
manifest['adoption_gate_20261107'] = {
    'eligible': False, 'decision': 'rejected', 'baseline_binding_verified': False,
    'single_module_verified': False, 'score_preservation_verified': False,
    'negative_feedback': 'D-02=0, E-04 displayed 5.6; see control/2026-11-07-baseline-preservation',
    'original_image_and_evidence_retained': True,
}
save(manifest_path, manifest)
for name in ('manifest.json', '提交说明.md'):
    shutil.copyfile(DELIVERY / name, ROUND / 'submission' / name)
policy_path = ROOT / '.session-audit/baseline-preservation-policy-20261106.json'
policy = json.loads(policy_path.read_text(encoding='utf-8'))
policy['candidate_status'] = 'rejected for adoption after user-provided negative official feedback'
policy['submission_status'] = 'blocked'
policy['negative_result_record'] = 'd02-official-feedback-20261107.json'
policy['approved_candidates'] = []
save(policy_path, policy)
for relative in ('AGENTS.md', '.session-audit/baseline-preservation-policy-20261106.json',
                 '.session-audit/baseline-preservation-guard-checks-20261106.json',
                 '镜像测试/baseline_submission_guard_20261106.py',
                 '.session-audit/record_d02_negative_result_20261107.py'):
    shutil.copyfile(ROOT / relative, CONTROL / Path(relative).name)
# Historical raw records must remain byte-identical.
for relative, entry in previous.items():
    if relative.startswith('raw/'):
        assert digest(ROUND / relative) == entry['sha256'], relative
save(ROUND / 'archive-sha256.json', {
    path.relative_to(ROUND).as_posix(): {'sha256': digest(path), 'bytes': path.stat().st_size}
    for path in sorted(ROUND.rglob('*')) if path.is_file() and path.name != 'archive-sha256.json'
})
save(DELIVERY / '交付材料校验.json', {
    path.name: {'sha256': digest(path), 'bytes': path.stat().st_size}
    for path in DELIVERY.iterdir() if path.is_file() and path.name != '交付材料校验.json'
})
print(json.dumps({'decision': 'rejected', 'agent_runtime_edits': 0,
                  'tar_sha256_unchanged': manifest['sha256'], 'historical_raw_preserved': True,
                  'record': str(CONTROL / 'negative-official-feedback.json')}, ensure_ascii=False))
