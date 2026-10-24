"""准备手工检查0.4.5的直接题目和独立输出目录；不会启动容器或调用模型。"""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', help='本次新建目录，必须位于项目output下')
    parser.add_argument('--env-file', default='.env.local')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_dir = Path(args.run_dir).resolve()
    try:
        run_dir.relative_to((root / 'output').resolve())
    except ValueError:
        parser.error('run_dir必须位于本项目output目录内')
    if run_dir.exists():
        parser.error('本次目录已经存在，请更换时间戳，避免覆盖原始结果')
    env_path = Path(args.env_file).resolve()
    if not env_path.is_file():
        parser.error('未找到.env.local，请先在该文件填写官方模型配置；不要打印密钥')
    env = {}
    for line in env_path.read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip()
    url = env.get('PLATFORM_LLM_URL', '').rstrip('/')
    model = env.get('PLATFORM_LLM_MODEL') or env.get('AGENT_MODEL_NAME')
    secret = env.get('PLATFORM_API_KEY', '')
    if url != 'http://183.230.58.3:3000/v1' or model != 'deepseek_v4':
        parser.error('.env.local的地址或模型不是本次指定的官方网关/deepseek_v4')
    if not secret or any(value in secret.upper() for value in ('REPLACE_', 'YOUR_API', 'PLACEHOLDER')):
        parser.error('.env.local未填写实际官方Key')
    fields = {'case_id', 'function', 'title', 'input', 'materials', 'constraints',
              'expected_output', 'output_schema_ref'}
    requests = {}
    for case_id in ('A-01', 'B-01', 'C-01', 'E-01'):
        source = root / 'team_share/requests' / (case_id + '.json')
        original = json.loads(source.read_text(encoding='utf-8-sig'))
        question = original if 'case_id' in original else original['input']
        if set(question) != fields:
            parser.error('请求不是直接八字段题目：' + str(source))
        for material in question['materials']:
            if not (root / 'team_share/papers' / material).is_file():
                parser.error('样例缺少授权PDF：' + material)
        requests[case_id] = question
    for case_id, materials in (
        ('B-97', ['本地论文文件夹内的 PDF']),
        ('B-98', ['本地论文库中的 PDF']),
        ('B-99', ['__deliberately_missing_local_test__.pdf']),
    ):
        requests[case_id] = dict(requests['B-01'], case_id=case_id, materials=materials)
    run_dir.mkdir(parents=True)
    for folder in ('requests', 'http', 'artifacts'):
        (run_dir / folder).mkdir()
    for case_id, question in requests.items():
        (run_dir / 'requests' / (case_id + '.json')).write_text(
            json.dumps(question, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # 只复制模型必需配置，避免旧测试环境变量覆盖默认材料目录或运行模式。
    gateway = run_dir / 'gateway.env'
    gateway.write_text('PLATFORM_LLM_URL=' + url + '\nPLATFORM_LLM_MODEL=' + model
                       + '\nAGENT_MODEL_NAME=' + model + '\nPLATFORM_API_KEY=' + secret + '\n',
                       encoding='utf-8')
    try:
        os.chmod(str(gateway), 0o600)
    except OSError:
        pass
    (run_dir / 'run-info.json').write_text(json.dumps({
        'image': 'research-assistant:0.4.5-official-20261024',
        'expected_image_id': 'sha256:e6bd975f5baac116398b73cfa8a750e3c6534487179b4e040d865831ba9a08fa',
        'team': 'local-045', 'source': 'Repository local samples; not the official evaluation questions',
        'requests': sorted(requests), 'model': model, 'gateway': url,
        'local_telemetry_export_disabled': True,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('已准备直接八字段请求：' + ', '.join(sorted(requests)))
    print('输出目录：' + str(run_dir))
    print('模型：' + model + '；密钥已写入本次gateway.env，未打印。')
    print('B-97为已配置别名，B-98为描述变化反例，B-99为缺失文件反例；均非正式B题原文。')


if __name__ == '__main__':
    main()
