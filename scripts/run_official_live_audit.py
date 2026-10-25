"""对指定镜像使用真实 PDF/官方API；平台式只读派题，动态目录标识，1CPU/1GiB。"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEAM = 'workspace-live-audit'
PDFS = ['2004.04906-DPR.pdf', '2005.11401-RAG.pdf', '2007.01282-FiD.pdf',
        '2112.04426-RETRO.pdf', '2310.11511-Self-RAG.pdf']


def docker(*args):
    p = subprocess.run(['docker', *args], capture_output=True, text=True, encoding='utf-8', cwd=ROOT)
    if p.returncode:
        raise RuntimeError(re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', p.stderr[-2500:]))
    return p.stdout


def question(case_id, text, materials):
    fn = {'A': 'Chat', 'B': 'Literature_Retrieval', 'C': 'Survey', 'D': 'Reproduction', 'E': 'Idea'}[case_id[0]]
    return dict(case_id=case_id, function=fn, title='真实论文与官方API接入检查', input=text,
        materials=materials, constraints=['只使用本题授权材料，遵守官方输出规范，不编造论文或实验结果。'],
        expected_output=fn, output_schema_ref=fn)


def specimens():
    retrieval = '检索与开放域问答的稠密检索、检索增强生成、多段证据融合直接相关的论文，逐篇说明具体匹配理由。'
    return [
        dict(label='A-05-chat', payload=question('A-05', '解释RAG中的检索噪声如何影响回答可靠性，提出一个最关键的研究范围澄清问题。', [])),
        dict(label='B-01-canonical', require_nonempty=True, payload=question('B-01', retrieval, ['本地论文文件夹内的 PDF'])),
        dict(label='B-02-whole-library-description', require_nonempty=True, payload=question('B-02', retrieval, ['本地论文库中的 PDF'])),
        dict(label='B-03-library-directory', require_nonempty=True, payload=question('B-03', retrieval, ['data/论文库'])),
        dict(label='C-01-five-real-papers', payload=question('C-01', '比较五篇指定论文各自的检索、证据融合和生成控制方法、结果与局限，区分论文贡献和相关工作。中文综述正文800到4500字符，引用不超过40次；## 参考文献列出五篇实际PDF，事实和页码可核查，另存Markdown与JSON完全一致。', PDFS)),
        dict(label='C-03-five-real-papers', payload=question('C-03', '综述指定五篇论文在开放域问答中应对检索噪声的方法、实验条件和局限。不跨论文转移事实，引用可核查。中文正文800到4500字符，引用不超过40次，末尾## 参考文献包含五篇实际PDF。', PDFS)),
        dict(label='D-01-no-platform-session', payload=question('D-01', '本题缺少正式实验会话时如实报告materials_missing或failed；核心指标恰好三项：name=Accuracy，unit=%；name=Macro-F1，unit=分数；name=Runtime，unit=s。', [])),
        dict(label='D-02-no-platform-session', payload=question('D-02', '只执行当前题正式实验，不能编造观察。核心指标三项：name=Recall@5，unit=%；name=MRR@10，unit=分数；name=Runtime，unit=s。', [])),
        dict(label='D-05-no-platform-session', payload=question('D-05', '无正式会话则如实报告；核心指标三项：name=Jain，unit=分数；name=Throughput，unit=请求/s；name=Runtime，unit=s。', [])),
        dict(label='E-01-two-real-papers', payload=question('E-01', '基于RAG与Self-RAG两篇指定论文，针对题目给出的待验证观察“检索相关而引用仍无依据”，提出可验证研究方案。区分论文证据、模拟观察和新假设，说明模块输入输出、数据基线、实验消融、量化指标、资源、失败边界和可证伪方式。中文正文1800到3000字符，末尾## 参考文献，另存Markdown与JSON一致。', [PDFS[1], PDFS[4]])),
        dict(label='E-02-two-real-papers', payload=question('E-02', '依据指定DPR和FiD论文，针对多段检索证据冲突这一待验证观察，提出可验证方案。区分已有贡献和新假设，说明方法、数据、基线、消融、指标、瓶颈与可行性。中文正文1800到3000字符，事实可追溯且参考文献列出指定PDF。', [PDFS[0], PDFS[2]])),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--categories', default='B')
    parser.add_argument('--case-ids', default='')
    parser.add_argument('--library-size', type=int, default=5)
    parser.add_argument('--b-fault-fixtures', action='store_true')
    parser.add_argument('--d-materials', action='store_true')
    args = parser.parse_args()
    audit = ROOT / 'output' / args.run_id
    fixtures = ROOT / 'build' / args.run_id
    if audit.exists() or fixtures.exists():
        raise RuntimeError('Choose a new run-id; existing evidence will not be overwritten')
    audit.mkdir(parents=True)
    output = audit / 'agent-output'
    (output / 'data' / TEAM).mkdir(parents=True)
    data = fixtures / 'data'
    library = data / 'papers'
    library.mkdir(parents=True)
    for name in PDFS:
        shutil.copyfile(ROOT / 'paper_library/pdfs' / name, library / name)
    if args.library_size > 5:
        seen = {hashlib.sha256(path.read_bytes()).hexdigest() for path in library.glob('*.pdf')}
        for source in sorted((ROOT / 'paper_library/pdfs-300').glob('*.pdf')):
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest in seen or (library / source.name).exists():
                continue
            shutil.copyfile(source, library / source.name)
            seen.add(digest)
            if len(seen) >= args.library_size:
                break
        if len(seen) != args.library_size:
            raise RuntimeError('Not enough distinct real PDFs for requested library size')
    all_cases = specimens()
    if args.d_materials:
        source = ROOT/'科研助手赛题材料/research-experiments-20261020-source-v1'
        for specimen in all_cases:
            case = specimen['payload']
            if case['case_id'].startswith('D-'):
                destination = data/'experiments'/case['case_id']
                shutil.copytree(source/case['case_id'],destination)
                protocol = json.loads((destination/'protocol.json').read_text(encoding='utf-8'))
                case['input'] = '按当前公开协议执行，无正式实验会话则如实报告。核心指标：' + '；'.join('name='+m['name']+',unit='+m['unit'] for m in protocol['metrics'])
                case['materials'] = ['data/experiments/'+case['case_id']]
    if args.b_fault_fixtures:
        # 保留真实论文，只加入旧版会在模型前抛错的候选库文件。
        (library / '000-broken.pdf').write_bytes(b'not a PDF')
        (library / '000-index.json').write_bytes(b'{bad json')
        (library / '000-notes.txt').write_bytes(b'\xff\xfe\x80')
        (library / 'duplicate').mkdir()
        shutil.copyfile(library / PDFS[0], library / 'duplicate' / PDFS[0])
        all_cases[2]['payload']['materials'] = ['本地论文文件夹下的 PDF']
        all_cases[3]['payload']['materials'] = ['data/papers/*.pdf']
    rows, ids = [], []
    assigned = fixtures / 'assigned'
    assigned.mkdir()
    for specimen in all_cases:
        case = specimen['payload']
        path = assigned / (case['case_id'] + '.json')
        path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        ids.append(case['case_id'])
        rows.append(dict(case_id=case['case_id'], function=case['function'], title=case['title'],
            question_file=f"data/{TEAM}/assigned/{case['case_id']}.json", question_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            **({'difficulty': {'D-01': 'easy', 'D-02': 'medium', 'D-05': 'hard'}[case['case_id']]} if case['case_id'].startswith('D-') else {})))
    dispatch = dict(schema_version=3, rules_version='research-assistant-objective-v3', team=TEAM,
        cases_per_category=dict(zip('ABCDE', [1,3,2,3,2])), case_ids=ids, cases=rows)
    dispatch_path = fixtures / 'dispatch.json'
    dispatch_path.write_text(json.dumps(dispatch, ensure_ascii=False, indent=2), encoding='utf-8')
    selected = [row for row in all_cases if row['payload']['case_id'][0] in args.categories]
    if args.case_ids:
        selected = [row for row in selected if row['payload']['case_id'] in args.case_ids.split(',')]
    (audit / 'requests.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding='utf-8')
    inspection = json.loads(docker('image', 'inspect', args.image))[0]
    env = dict(line.split('=',1) for line in (ROOT / '.env.local').read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.startswith('#'))
    assert env.get('PLATFORM_API_KEY') and env.get('PLATFORM_LLM_URL') == 'http://183.230.58.3:3000/v1'
    provenance = dict(image=args.image, image_id=inspection['Id'], real_pdf_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in library.glob('*.pdf')},
        model=env.get('PLATFORM_LLM_MODEL') or env.get('AGENT_MODEL_NAME'), official_api=True,
        platform_workspace_simulated=TEAM, official_hidden_questions=False, cpu=1, memory_mib=1024,
        b_fault_fixtures=args.b_fault_fixtures)
    name = 'research-' + args.run_id
    started = False
    try:
        docker('run', '-d', '--name', name, '--cpus', '1', '--memory', '1024m', '--env-file', str(ROOT / '.env.local'),
            '--mount', f'type=bind,source={ROOT / "scripts"},target=/checks,readonly',
            '--mount', f'type=bind,source={data},target=/data,readonly',
            '--mount', f'type=bind,source={audit},target=/audit', '--mount', f'type=bind,source={output},target=/output',
            '--mount', f'type=bind,source={assigned},target=/output/data/{TEAM}/assigned,readonly',
            '--mount', f'type=bind,source={dispatch_path},target=/output/data/{TEAM}/dispatch.json,readonly',
            '-e', f'RESEARCH_WORKSPACE_TEAM={TEAM}', '-e', 'TEAM_ID=telemetry-identity-test',
            '-e', 'RESEARCH_RUNTIME=sdk', '-e', 'AGENTHACK_TELEMETRY_REQUIRED=true',
            '-e', 'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:18090/v1/traces', args.image)
        started = True
        p = subprocess.run(['docker','exec',name,'python','/checks/live_platform_audit_client.py'],cwd=ROOT)
        provenance['client_exit_code'] = p.returncode
    finally:
        if started:
            state = json.loads(docker('inspect',name))[0]['State']
            provenance['oom_killed'] = state['OOMKilled']
            logs = subprocess.run(['docker','logs',name],capture_output=True,text=True,encoding='utf-8')
            (audit/'container.log').write_text(re.sub(r'sk-[A-Za-z0-9_-]+','[REDACTED]',logs.stdout+logs.stderr),encoding='utf-8')
            docker('rm','-f',name)
        (audit/'provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding='utf-8')
        secret=env['PLATFORM_API_KEY'].encode()
        assert not any(secret in path.read_bytes() for path in audit.rglob('*') if path.is_file()), 'Secret leaked in audit'
    print('Audit saved: '+str(audit),flush=True)


if __name__ == '__main__':
    main()
