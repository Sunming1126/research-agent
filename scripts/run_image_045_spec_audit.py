"""只运行原始0.4.5镜像并归档输出；不构建、不修改被测业务代码。"""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'research-assistant:0.4.5-official-20261024'
EXPECTED_ID = 'sha256:e6bd975f5baac116398b73cfa8a750e3c6534487179b4e040d865831ba9a08fa'
NAME = 'research-045-five-type-spec-audit'
STAMP = '20261024'
AUDIT = ROOT / 'output' / ('image-045-five-type-spec-' + STAMP)
DATA = ROOT / 'build' / ('image-045-five-type-spec-' + STAMP) / 'data'


def docker(*args):
    completed = subprocess.run(['docker', *args], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    if completed.returncode:
        raise RuntimeError('docker ' + args[0] + ' failed: ' + re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', completed.stderr[-3000:]))
    return completed.stdout


def question(case_id, fn, text, materials):
    return dict(case_id=case_id, function=fn, title='本地真实论文输出规范检查', input=text, materials=materials,
                constraints=['只使用本题授权材料，事实和页码必须可回查，不编造实测结果；输出遵守research-agent-spec。'],
                expected_output=fn, output_schema_ref=fn)


def main():
    if (AUDIT / 'summary.json').exists():
        raise RuntimeError('Refusing to overwrite an earlier run; choose a new STAMP')
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / 'agent-output').mkdir(exist_ok=True)
    library = DATA / '论文库'
    library.mkdir(parents=True, exist_ok=True)
    names = ['2004.04906-DPR.pdf', '2005.11401-RAG.pdf', '2007.01282-FiD.pdf', '2112.04426-RETRO.pdf', '2310.11511-Self-RAG.pdf']
    hashes = {}
    for name in names:
        source = ROOT / 'paper_library/pdfs' / name
        shutil.copyfile(source, library / name)
        hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    public = ROOT / '科研助手赛题材料/research-experiments-20261020-source-v1/D-01'
    experiment = DATA / 'experiments/D-01'
    experiment.mkdir(parents=True, exist_ok=True)
    for name in ('method.md', 'protocol.json', 'sample.json'):
        shutil.copyfile(public / name, experiment / name)
    protocol = json.loads((public / 'protocol.json').read_text(encoding='utf-8'))
    metrics = '；'.join(f"name={row['name']}，unit={row['unit']}" for row in protocol['metrics']) + '。'
    cases = [
        dict(label='A-04-chat', scenario='五类主测试', payload=question('A-04', 'Chat', '解释检索增强生成中检索错误如何影响回答可靠性，并提出一个最关键的研究范围澄清问题。', [])),
        dict(label='B-01-retrieval', scenario='五类主测试', payload=question('B-01', 'Literature_Retrieval', '从授权论文库检索与开放域问答的稠密检索、检索增强生成和多段证据融合直接相关的论文，说明每篇的具体匹配理由。', ['本地论文文件夹内的 PDF'])),
        dict(label='C-01-survey', scenario='五类主测试', payload=question('C-01', 'Survey', '比较本题五篇指定论文在检索器、证据融合、生成控制、训练和推理条件上的方法、结果与局限，区分各论文自身贡献与其相关工作。写至少800字符的中文综述，正文不超过4500字符，引用实例不超过40，末尾## 参考文献列出至少5篇实际引用的指定PDF，JSON与另存Markdown逐字一致。', names)),
        dict(label='D-01-reproduction', scenario='五类主测试，缺正式会话的D合法状态', payload=question('D-01', 'Reproduction', '依据当前D-01公开方法、协议与样例核对实验条件，只有本轮真实正式实验成功才可completed；无法执行用materials_missing或failed，不虚构指标。核心指标恰好三项：' + metrics, ['data/experiments/D-01/method.md', 'data/experiments/D-01/protocol.json', 'data/experiments/D-01/sample.json'])),
        dict(label='E-01-idea', scenario='五类主测试', payload=question('E-01', 'Idea', '基于指定的RAG与Self-RAG两篇论文，针对“检索相关但回答仍出现无依据引用”这一题目给出的待验证观察，提出研究方案。区分论文事实、模拟观察与新假设，说明动机、模块输入输出、基线与数据选择、实验与消融、指标、资源预算、可行性、创新和可证伪判据。正文至少800字符，建议1800到3200字符，不超过6000字符，事实附可核查引用，末尾## 参考文献列出实际PDF；不使用通用模板扩写。', [names[1], names[4]])),
        dict(label='B-02-library-description', scenario='失败分支：同义整体库描述，不声称是正式B题输入', payload=question('B-02', 'Literature_Retrieval', '检索与检索增强生成相关的本地论文。', ['本地论文库中的 PDF'])),
        dict(label='B-03-missing-file', scenario='失败分支：授权材料缺失', payload=question('B-03', 'Literature_Retrieval', '检索指定论文。', ['missing-authorized-paper.pdf'])),
        dict(label='C-03-missing-file', scenario='失败分支：材料缺失，不声称复现本轮C-03根因', payload=question('C-03', 'Survey', '按指定论文完成至少800字符综述，列出五篇实际引用论文。', ['missing-authorized-paper.pdf'])),
        dict(label='E-02-missing-file', scenario='失败分支：材料缺失', payload=question('E-02', 'Idea', '依据指定论文提出研究设计，不能编造证据。', ['missing-authorized-paper.pdf'])),
    ]
    (AUDIT / 'requests.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    shutil.copyfile(ROOT / '科研助手赛题材料/research-agent-spec.md', AUDIT / 'official-research-agent-spec.md')
    image = json.loads(docker('image', 'inspect', IMAGE))[0]
    assert image['Id'] == EXPECTED_ID and image['Os'] == 'linux' and image['Architecture'] == 'amd64'
    env = dict(line.split('=', 1) for line in (ROOT / '.env.local').read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.lstrip().startswith('#'))
    assert env.get('PLATFORM_LLM_URL') == 'http://183.230.58.3:3000/v1'
    assert env.get('PLATFORM_LLM_MODEL') == 'deepseek_v4' and env.get('PLATFORM_API_KEY')
    provenance = dict(image=IMAGE, image_id=image['Id'], spec_sha256=hashlib.sha256((AUDIT / 'official-research-agent-spec.md').read_bytes()).hexdigest(),
                      material_hashes=hashes, official_model='deepseek_v4', official_gateway=env['PLATFORM_LLM_URL'],
                      secret_recorded=False, cpus=1, memory_mib=1024, production_code_modified=False,
                      inputs_are_official_hidden_questions=False)
    (AUDIT / 'provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    started = False
    try:
        docker('run', '-d', '--name', NAME, '--network', 'bridge', '--cpus', '1', '--memory', '1024m', '--env-file', str(ROOT / '.env.local'),
               '--mount', f'type=bind,source={ROOT / "scripts/image_045_spec_audit_client.py"},target=/checks/audit_client.py,readonly',
               '--mount', f'type=bind,source={DATA},target=/data,readonly',
               '--mount', f'type=bind,source={AUDIT},target=/audit',
               '--mount', f'type=bind,source={AUDIT / "agent-output"},target=/output',
               '-e', 'RESEARCH_RUNTIME=sdk', '-e', 'AGENTHACK_TELEMETRY_REQUIRED=true',
               '-e', 'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:18090/v1/traces',
               '-e', 'TEAM_ID=spec_audit_045', IMAGE)
        started = True
        print('Testing original 0.4.5 with five real PDFs and the official gateway.', flush=True)
        completed = subprocess.run(['docker', 'exec', NAME, 'python', '/checks/audit_client.py'], cwd=ROOT)
        provenance['client_exit_code'] = completed.returncode
    finally:
        if started:
            state = json.loads(docker('inspect', NAME))[0]['State']
            provenance['container_state'] = {key: state.get(key) for key in ('Running', 'ExitCode', 'OOMKilled', 'Error')}
            logs = subprocess.run(['docker', 'logs', NAME], capture_output=True, text=True, encoding='utf-8')
            (AUDIT / 'container.log').write_text(re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', logs.stdout + logs.stderr), encoding='utf-8')
            docker('rm', '-f', NAME)
        (AUDIT / 'provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        secret = env['PLATFORM_API_KEY'].encode()
        assert not any(secret in path.read_bytes() for path in AUDIT.rglob('*') if path.is_file()), 'Secret in audit artifacts'
    print('Audit artifacts: ' + str(AUDIT), flush=True)


if __name__ == '__main__':
    main()
