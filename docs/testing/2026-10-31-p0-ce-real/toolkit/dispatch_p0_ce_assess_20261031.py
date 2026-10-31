"""Dispatch source assessment into the already verified original native chat."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / '镜像测试'))
from native_workspace_harness import native_rows, selection, workspace
from harness_window import focus

SID = 'session-9c903d0e-5884-44fe-bc7f-3719da00864b'
WID = 'c61ec5e8-9932-4ef5-9709-fed302ad7355'
meta = json.loads((ROOT / '.session-audit/p0-ce-real-study-20261031.json').read_text(encoding='utf-8'))
study = Path(meta['directory'])
assert not (study / 'assessment/native-proof.json').exists(), 'Assessment already launched; no duplicate'
for number in (1, 2, 3):
    assert json.loads((study / f'pair-{number}-result.json').read_text(encoding='utf-8'))['complete']
selection()
record = workspace()
assert SID in record['sessionIds']
focus()
rows, _ = native_rows(SID)
assert rows[-1]['type'] == 'turn/end', 'Original native task still running; do not dispatch'
before = max(r.get('seq', 0) for r in rows)
proof = {'study': meta['study'], 'session_id': SID, 'workspace_id': WID, 'before_seq': before,
         'created_unix_seconds': time.time(), 'new_conversations_created': 0,
         'registered_session_ids_before': sorted(record['sessionIds']),
         'ui_binding_sha256': hashlib.sha256((study / 'native-ui-before-call.png').read_bytes()).hexdigest()}
(ROOT / '.session-audit/p0-ce-assess-request-20261031.json').write_text(
    json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
command = ("& 'C:/Users/lenovo/AppData/Local/Programs/Python/Python314/python.exe' "
           "'C:/Users/lenovo/Desktop/Agent挑战赛/镜像测试/p0_ce_assess_20261031.py' --study " + meta['study'])
message = (
    '继续P0：三轮生成对照均已结束。请只在当前Agent挑战赛／科研助手镜像验收总控原生工具中执行一次：'
    + command + ' 。工作目录C:/Users/lenovo/Desktop/Agent挑战赛。'
    '用户已授权真实论文、报告片段出站并要求正确率优先，现执行预先冻结的独立来源摘录核查，'
    '仍使用既有magic/deepseek-v4.1-flash，不新建会话、不改连接、不输出凭据。'
    '协调器先核验当前新鲜原生工具调用，只审查三轮全部已生成报告，失败报告也记录；'
    '最多24次，每份最多3000输出Token，75秒，额度耗尽即停。模型看不到版本、轮次、分数或自审。'
    '每份抽查8至12条论文事实，原句和原文引文必须连续匹配；失败保留，不自动修正审查结果。'
    '这是同provider抽样诊断，不能当作全文正确率或官方成绩。'
    '请等同一进程结束，不重复启动、不改任何测试、源码、阈值或审批策略；'
    '原生执行权限如需审批应正常等待用户。完成后仅汇报实际完成数量、失败和Token。')
(study / 'native-assess-0-prompt.txt').write_text(message, encoding='utf-8')
result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-STA', '-File',
                         str(ROOT / '.session-audit/paste_p0_native_prompt_20261031.ps1'),
                         '-Stage', 'assess', '-Round', '0', '-Send'], capture_output=True, timeout=30)
assert result.returncode == 0, 'Native input failed; do not resend blindly'
receipt = json.loads(result.stdout.decode('utf-8-sig'))
assert receipt['submitted'] and receipt['input_matches']
(study / 'native-assess-0-dispatch.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
for _ in range(60):
    rows, _ = native_rows(SID)
    if any(r.get('seq', 0) > before and r.get('type') == 'user/message' and meta['study'] in json.dumps(r) for r in rows):
        assert sorted(workspace()['sessionIds']) == proof['registered_session_ids_before']
        print(json.dumps({'submitted': True, 'study': meta['study'], 'stage': 'assessment',
                          'before_seq': before, 'new_conversations_created': 0}))
        break
    time.sleep(.2)
else:
    raise RuntimeError('No native acknowledgement; do not resend blindly')
