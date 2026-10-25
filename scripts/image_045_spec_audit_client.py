"""通过真实HTTP读取既有镜像输出；独立按文档检查，不导入Agent的校验器。"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

ROOT = Path('/audit')
OUTPUT = Path('/output')
TEAM = 'spec_audit_045'
PAPER_LIBRARY = Path('/data/论文库')
SPANS = []
LOCK = threading.Lock()
EXPECTED = {
    'Chat': {'case_id', 'answer', 'clarifying_question'},
    'Literature_Retrieval': {'case_id', 'papers'},
    'Survey': {'case_id', 'path', 'markdown'},
    'Reproduction': {'case_id', 'status', 'metrics', 'figures'},
    'Idea': {'case_id', 'path', 'markdown'},
}


def redact(text):
    return re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', text)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(json.dumps(value, ensure_ascii=False, indent=2)) + '\n', encoding='utf-8')


def attr_value(value):
    kind = value.WhichOneof('value')
    if kind == 'array_value':
        return [attr_value(item) for item in value.array_value.values]
    if kind == 'kvlist_value':
        return {item.key: attr_value(item.value) for item in value.kvlist_value.values}
    return getattr(value, kind) if kind else None


class Collector(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
        request = ExportTraceServiceRequest()
        request.ParseFromString(raw)
        rows = []
        for resource in request.resource_spans:
            for scope in resource.scope_spans:
                for span in scope.spans:
                    rows.append(dict(name=span.name, trace_id=span.trace_id.hex(), span_id=span.span_id.hex(),
                                     parent_span_id=span.parent_span_id.hex(),
                                     attributes={item.key: attr_value(item.value) for item in span.attributes}))
        with LOCK:
            SPANS.extend(rows)
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-protobuf')
        self.send_header('Content-Length', '0')
        self.end_headers()


def text(value):
    return isinstance(value, str) and bool(value.strip())


def safe_path(value):
    return (text(value) and not value.startswith('/') and not any(char in value for char in ('\\', ':', '\x00'))
            and not any(part in ('', '.', '..') for part in value.split('/')))


def citations(body):
    found = []
    for match in re.finditer(r'\[(\d{1,2}(?:\s*,\s*\d{1,2})*)(?:,\s*p\.\s*\d+)?\]', body):
        found.extend(re.split(r'\s*,\s*', match.group(1)))
    return found


def check(specimen, body, raw, status):
    """这里的规则直接来自research-agent-spec.md，不复用被测Agent函数。"""
    case = specimen['payload']
    fn, case_id = case['function'], case['case_id']
    failures, checks = [], {}

    def require(label, condition):
        checks[label] = bool(condition)
        if not condition:
            failures.append(label)

    require('HTTP 200', status == 200)
    require('JSON对象且无围栏/前缀', isinstance(body, dict))
    keys = set(body) if isinstance(body, dict) else set()
    require('顶层字段与题型完全一致', keys == EXPECTED[fn])
    require('case_id与输入一致', isinstance(body, dict) and body.get('case_id') == case_id)
    target = OUTPUT / 'data' / TEAM / (case_id + '.json')
    require('结果JSON实际存在', target.is_file())
    saved = None
    if target.is_file():
        try:
            saved = json.loads(target.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError):
            pass
    require('落盘JSON与HTTP答卷相同', saved is not None and saved == body)
    extra = {}
    if keys == EXPECTED[fn]:
        if fn == 'Chat':
            require('answer和clarifying_question非空', text(body['answer']) and text(body['clarifying_question']))
        elif fn == 'Literature_Retrieval':
            papers = body['papers']
            require('papers为数组', isinstance(papers, list))
            if isinstance(papers, list):
                require('paper只含filename/reason', all(isinstance(p, dict) and set(p) == {'filename', 'reason'} for p in papers))
                valid = all(isinstance(p, dict) and text(p.get('filename')) and text(p.get('reason'))
                            and p['filename'].lower().endswith('.pdf') and (PAPER_LIBRARY / p['filename']).is_file()
                            for p in papers)
                require('文件名真实且理由非空', valid)
                names = [p.get('filename', '').casefold() for p in papers if isinstance(p, dict)]
                require('论文去重', len(names) == len(set(names)))
                extra['papers_count'] = len(papers)
        elif fn in ('Survey', 'Idea'):
            kind = '综述' if fn == 'Survey' else '灵感Idea报告'
            expected_path = f'data/expert/{case_id}-{kind}-{TEAM}.md'
            require('报告相对路径及命名', safe_path(body['path']) and body['path'] == expected_path)
            markdown = body['markdown']
            require('markdown为非空字符串', text(markdown))
            if isinstance(markdown, str):
                require('唯一参考文献章节', markdown.count('## 参考文献') == 1)
                main, _, refs = markdown.partition('## 参考文献')
                extra.update(body_characters=len(main), citation_instances=len(citations(main)))
                require('正文至少800字符', len(re.sub(r'\s', '', main)) >= 800)
                require('正文不超过4500/6000上限', len(main) <= (4500 if fn == 'Survey' else 6000))
                if fn == 'Survey':
                    require('引用实例不超过40', len(citations(main)) <= 40)
                referenced = re.findall(r'^\[(\d+)\]\s+(.+\.pdf)\s*$', refs, re.M)
                require('足量真实PDF参考文献', len({name for _, name in referenced}) >= (5 if fn == 'Survey' else 1)
                        and all((PAPER_LIBRARY / name).is_file() for _, name in referenced))
                require('引用编号与参考文献匹配', set(citations(main)) == {number for number, _ in referenced})
                report_file = OUTPUT / expected_path
                require('Markdown实际存在且逐字一致', report_file.is_file() and report_file.read_text(encoding='utf-8') == markdown)
        elif fn == 'Reproduction':
            require('D状态合法', body['status'] in ('completed', 'failed', 'materials_missing'))
            metrics, figures = body['metrics'], body['figures']
            require('恰好三个核心指标', isinstance(metrics, list) and len(metrics) == 3)
            if isinstance(metrics, list):
                require('指标字段正确', all(isinstance(m, dict) and set(m) == {'name', 'unit', 'reference', 'observed', 'relative_error'} for m in metrics))
                pairs = re.findall(r'name\s*[=:]\s*(.+?)\s*[,，]\s*unit\s*[=:]\s*([^;；。\n]+)', case['input'], re.I)
                require('指标名称单位与输入一致', [(m.get('name'), m.get('unit')) for m in metrics] == [(n.strip(), u.strip()) for n, u in pairs])
                require('数值为有限数字或null', all(m.get(k) is None or type(m[k]) in (int, float) and math.isfinite(m[k]) for m in metrics for k in ('reference', 'observed', 'relative_error')))
                if body['status'] != 'completed':
                    require('未完成不声称实测值', all(m.get('observed') is None for m in metrics))
            require('figures为数组', isinstance(figures, list))
            if isinstance(figures, list):
                require('图表字段路径及文件存在', all(isinstance(f, dict) and set(f) == {'path', 'caption', 'x_label', 'y_label', 'legend'}
                        and all(text(v) for v in f.values()) and safe_path(f['path'])
                        and f['path'].startswith(f'data/figure/{TEAM}/') and (OUTPUT / f['path']).is_file() for f in figures))
            extra['experiment_status'] = body['status']
    # 用落盘的真实错误和审查信息解释失败；不把SDK错误对象假装成题型答卷。
    work = OUTPUT / 'data' / TEAM / 'work' / case_id
    diagnostic = []
    audits = []
    for path in work.glob('*/error.json'):
        value = json.loads(path.read_text(encoding='utf-8'))
        diagnostic.append({k: value.get(k) for k in ('error_type', 'message', 'usage')})
    for path in work.glob('*/evidence.json'):
        value = json.loads(path.read_text(encoding='utf-8'))
        audits.append(value.get('audit', {}))
    return dict(checks=checks, failures=failures, format_and_files_passed=not failures,
                expected_fields=sorted(EXPECTED[fn]), actual_fields=sorted(keys),
                diagnostics=diagnostic, report_audits=audits, **extra)


def render(rows):
    lines = ['# 0.4.5 镜像五类题输出与官方规范逐项对照', '',
             '本次直接运行已交付镜像，使用现有真实PDF和官方deepseek_v4。独立检查脚本未调用Agent自己的校验器。', '',
             '输入为本地构造题；平台本轮原始题目未公开。D只验证缺少正式会话时的合法状态，不冒充完成实验。', '',
             '| 场景 | 类别 | HTTP | 格式和文件 | 模型调用 | 用时 | 主要差异 |',
             '|---|---|---:|---|---:|---:|---|']
    for row in rows:
        issue = '；'.join(row['failures'][:3]) or ('D返回materials_missing，未验证正式实验' if row['function'] == 'Reproduction' else '规定字段和文件检查通过；不代表内容满分')
        lines.append(f"| [{row['label']}](cases/{row['label']}/response.json) | {row['function']} | {row['http_status']} | {'通过' if row['format_and_files_passed'] else '不通过'} | {row.get('llm_calls', '未知')} | {row['seconds']}s | {issue} |")
    lines += ['', '## 完整证据', '',
              '- 每个场景的 `cases/<场景>/input.json` 是实际输入，`response.json` 是实际HTTP JSON。',
              '- `response.raw.txt` 保留响应正文，`response.headers.json` 保存非凭据响应头，`checks.json` 列出逐项结果及真实错误。',
              '- 原生落盘结果位于 `agent-output/data/spec_audit_045/`，报告位于 `agent-output/data/expert/`。',
              '- `otlp-spans.json` 为本地实际收到的SDK追踪，不能证明官方采集链路已经验收。',
              '- `summary.json` 汇总所有检查；`official-research-agent-spec.md` 是本次使用的规范快照。', '']
    for row in rows:
        lines += [f"## {row['label']}", '', f"规范顶层字段：`{', '.join(row['expected_fields'])}`。", '',
                  f"实际顶层字段：`{', '.join(row['actual_fields']) or '未获得JSON对象'}`。", '']
        if row['diagnostics']:
            lines += ['实际错误摘要：', '']
            lines.extend(f"- `{d.get('error_type')}`：{d.get('message')}" for d in row['diagnostics'])
            lines.append('')
        if row.get('body_characters') is not None:
            lines += [f"正文 {row['body_characters']} 字符，引用 {row['citation_instances']} 次。", '']
        if row['report_audits']:
            lines += ['模型审查状态：`' + str(row['report_audits'][-1].get('ok')) + '`；模式：`' + str(row['report_audits'][-1].get('mode')) + '`。', '']
    (ROOT / 'comparison.md').write_text(redact('\n'.join(lines)), encoding='utf-8')


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 18090), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rows = []
    try:
        for attempt in range(50):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2) as stream:
                    health = json.load(stream)
                break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError('Image service is not healthy')
        save(ROOT / 'health.json', health)
        cases = json.loads((ROOT / 'requests.json').read_text(encoding='utf-8'))
        for number, specimen in enumerate(cases, 1):
            payload = specimen['payload']
            directory = ROOT / 'cases' / specimen['label']
            save(directory / 'input.json', payload)
            trace_id = f'{0x45000000 + number:032x}'
            headers = {'Content-Type': 'application/json', 'traceparent': f'00-{trace_id}-1234567890abcdef-01',
                       'x-task-id': 'audit-' + specimen['label'], 'x-session-id': 'image-045-spec-audit'}
            request = urllib.request.Request('http://127.0.0.1:8080/solve', json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers)
            raw, status, response_headers, transport = b'', None, {}, None
            started = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=180) as stream:
                    status, raw, response_headers = stream.status, stream.read(), dict(stream.headers.items())
            except urllib.error.HTTPError as exc:
                status, raw, response_headers = exc.code, exc.read(), dict(exc.headers.items())
            except (OSError, TimeoutError) as exc:
                transport = type(exc).__name__ + ': ' + str(exc)
            seconds = round(time.monotonic() - started, 3)
            if transport:
                time.sleep(3)  # 只为收集最后导出；不把延迟后的文件算作180秒内响应。
            decoded = raw.decode('utf-8', errors='replace')
            (directory / 'response.raw.txt').write_text(redact(decoded), encoding='utf-8')
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = None
            save(directory / 'response.json', body)
            save(directory / 'response.headers.json', response_headers)
            row = check(specimen, body, raw, status)
            with LOCK:
                selected = [span for span in SPANS if span['trace_id'] == trace_id]
                all_spans = list(SPANS)
            sessions = [span for span in selected if span['name'] == 'agent.session']
            row.update(label=specimen['label'], case_id=payload['case_id'], function=payload['function'],
                       http_status=status, seconds=seconds, transport_error=transport,
                       scenario=specimen['scenario'], local_otlp_spans=len(selected),
                       local_session_trace_received=bool(sessions),
                       llm_calls=sessions[-1]['attributes'].get('llm_call_count') if sessions else None)
            rows.append(row)
            save(directory / 'checks.json', row)
            save(ROOT / 'summary.json', {'rows': rows, 'official_evaluation_reproduced': False,
                                        'independent_spec_validation': True, 'client_hard_timeout_seconds': 180})
            save(ROOT / 'otlp-spans.json', all_spans)
            render(rows)
            print(f"{specimen['label']}: HTTP {status}, format/files={'PASS' if row['format_and_files_passed'] else 'FAIL'}, calls={row['llm_calls']}, {seconds}s", flush=True)
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
