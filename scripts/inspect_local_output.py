"""独立检查已捕获的HTTP答卷与落盘文件，不导入Agent自己的校验器、不调用模型。"""
import argparse
import json
import re
from pathlib import Path


FIELDS = {
    'Chat': {'case_id', 'answer', 'clarifying_question'},
    'Literature_Retrieval': {'case_id', 'papers'},
    'Survey': {'case_id', 'path', 'markdown'},
    'Idea': {'case_id', 'path', 'markdown'},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir')
    parser.add_argument('case_id')
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    request = json.loads((run_dir / 'requests' / (args.case_id + '.json')).read_text(encoding='utf-8-sig'))
    info = json.loads((run_dir / 'run-info.json').read_text(encoding='utf-8'))
    output = run_dir / 'artifacts'
    problems = []
    print('题号：' + args.case_id + '；类型：' + request['function'])
    transport = run_dir / 'http' / (args.case_id + '.transport.txt')
    if transport.is_file():
        lines = transport.read_text(encoding='utf-8').splitlines()
        print('HTTP状态/客户端耗时：' + ' / '.join(lines))
        if not lines or lines[0] != '200':
            problems.append('HTTP未返回200；保留原始响应及容器日志定位')
        if len(lines) > 1:
            try:
                if float(lines[1]) >= 180:
                    problems.append('客户端耗时达到180秒平台硬上限；本地190秒等待不代表平台接受')
            except ValueError:
                problems.append('无法解析客户端耗时')
    try:
        response = json.loads((run_dir / 'http' / (args.case_id + '.body.json')).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        response = None
        problems.append('原始HTTP响应为空或不是合法JSON：' + type(exc).__name__)
    if isinstance(response, dict):
        print('真正HTTP顶层字段：' + ', '.join(sorted(response)))
        if set(response) != FIELDS[request['function']]:
            problems.append('HTTP顶层字段不符合当前题型规范；不会自动解包掩盖问题')
        if response.get('case_id') != args.case_id:
            problems.append('HTTP答卷case_id缺失或不一致')
        if 'metadata' in response and 'output' in response:
            print('SDK状态/错误：' + str(response.get('status')) + ' / '
                  + str(response.get('output', {}).get('error')))
    elif response is not None:
        problems.append('HTTP答卷不是JSON对象')

    result_file = output / 'data' / info['team'] / (args.case_id + '.json')
    print('要求的结果文件：' + str(result_file))
    if result_file.is_file():
        try:
            disk = json.loads(result_file.read_text(encoding='utf-8-sig'))
            if disk != response:
                problems.append('落盘JSON与最外层HTTP答卷不同')
            else:
                print('JSON回读：与HTTP答卷一致')
        except ValueError:
            problems.append('落盘结果不是合法JSON')
    else:
        problems.append('没有生成题目结果JSON')

    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())

    def relative_file(relative):
        if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
            return None
        if any(part in ('', '.', '..') for part in relative.split('/')) or relative.startswith('/'):
            return None
        target = output.joinpath(*relative.split('/'))
        if output not in target.resolve().parents:
            return None
        return target

    if isinstance(response, dict) and set(response) == FIELDS[request['function']]:
        kind = request['function']
        if kind == 'Chat':
            if not nonempty(response['answer']) or not nonempty(response['clarifying_question']):
                problems.append('Chat回答或澄清问题为空')
        elif kind == 'Literature_Retrieval':
            papers = response['papers']
            if not isinstance(papers, list):
                problems.append('papers不是数组')
            else:
                print('返回论文数：' + str(len(papers)))
                known = {path.name for path in (Path(__file__).resolve().parents[1] / 'team_share/papers').glob('*.pdf')}
                explicit = {name for name in request['materials'] if name.lower().endswith('.pdf') and '/' not in name}
                seen = set()
                for paper in papers:
                    if not isinstance(paper, dict) or set(paper) != {'filename', 'reason'}:
                        problems.append('某条论文字段不符合filename/reason规范')
                        continue
                    name = paper['filename']
                    if not nonempty(name) or name not in known or not nonempty(paper['reason']):
                        problems.append('论文不存在或匹配理由为空：' + str(name))
                    elif name.casefold() in seen:
                        problems.append('重复论文：' + name)
                    elif explicit and name not in explicit:
                        problems.append('返回了本题清单外的论文：' + name)
                    if isinstance(name, str):
                        seen.add(name.casefold())
        elif kind in ('Survey', 'Idea'):
            suffix = '综述' if kind == 'Survey' else '灵感Idea报告'
            expected = 'data/expert/' + args.case_id + '-' + suffix + '-' + info['team'] + '.md'
            if response['path'] != expected:
                problems.append('报告路径或本队命名不符；本地预期为：' + expected)
            markdown = response['markdown']
            target = relative_file(response['path'])
            if not isinstance(markdown, str):
                problems.append('markdown不是字符串')
            else:
                if target is None or not target.is_file():
                    problems.append('Markdown文件不存在或路径不安全')
                elif target.read_text(encoding='utf-8') != markdown:
                    problems.append('Markdown文件与JSON正文不一致')
                else:
                    print('Markdown回读：逐字一致')
                if markdown.count('## 参考文献') != 1:
                    problems.append('缺少唯一的## 参考文献章节')
                body, _, references = markdown.partition('## 参考文献')
                citations = []
                for match in re.finditer(r'\[(\d{1,2}(?:\s*,\s*\d{1,2})*)(?:,\s*p\.\s*\d+)?\]', body):
                    citations.extend(re.split(r'\s*,\s*', match.group(1)))
                print('正文字符数（含空格/标点/Markdown）：' + str(len(body)))
                print('正文引用实例数：' + str(len(citations)))
                if len(re.sub(r'\s', '', body)) < 800:
                    problems.append('正文不足800个非空白字符')
                limit = 4500 if kind == 'Survey' else 6000
                if len(body) > limit:
                    problems.append('正文超过' + str(limit) + '字符')
                if kind == 'Survey' and len(citations) > 40:
                    problems.append('Survey引用超过40个实例')
                entries = re.findall(r'^\[(\d+)\]\s+(.+\.pdf)\s*$', references, re.M)
                if len({name for _, name in entries}) < (5 if kind == 'Survey' else 1):
                    problems.append('参考文献中的完整PDF文件名不足')
                if set(citations) != {index for index, _ in entries}:
                    problems.append('正文引用编号与参考文献不一致')
                explicit = {Path(name).name for name in request['materials'] if name.lower().endswith('.pdf')}
                if explicit and not {name for _, name in entries}.issubset(explicit):
                    problems.append('参考文献不在本地请求授权文件清单内')

    diagnostics = list((output / 'data' / info['team'] / 'work' / args.case_id).glob('*/error.json'))
    for path in sorted(diagnostics, key=lambda item: item.stat().st_mtime)[-1:]:
        value = json.loads(path.read_text(encoding='utf-8'))
        print('最近引擎错误：' + str(value.get('error_type')) + ' / ' + str(value.get('message')))
        print('错误文件：' + str(path))
    if problems:
        print('\n检查未通过：')
        for problem in problems:
            print('- ' + problem)
        return 1
    print('\n本地格式及落盘检查通过；不代表官方评分、事实核验或远端追踪验收通过。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
