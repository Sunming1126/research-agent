"""官方 API 容器实测；保存 HTTP、原生产物、真实 SDK 追踪，不生成替代答卷。"""
import json
import os
import threading
import time
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

import image_045_spec_audit_client as audit


def main():
    audit.TEAM = os.environ['RESEARCH_WORKSPACE_TEAM']
    audit.PAPER_LIBRARY = Path('/data/papers')
    server = ThreadingHTTPServer(('127.0.0.1', 18090), audit.Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rows = []
    try:
        for attempt in range(100):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2) as response:
                    audit.save(audit.ROOT / 'health.json', json.load(response))
                break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError('service did not become healthy')
        for number, specimen in enumerate(json.loads((audit.ROOT / 'requests.json').read_text(encoding='utf-8')), 1):
            payload = specimen['payload']
            directory = audit.ROOT / 'cases' / specimen['label']
            audit.save(directory / 'input.json', payload)
            trace_id = f'{0x48000000 + number:032x}'
            headers = {'Content-Type': 'application/json', 'traceparent': f'00-{trace_id}-1234567890abcdef-01',
                       'X-Research-Task-ID': 'live-' + specimen['label'],
                       'Idempotency-Key': 'live-' + specimen['label'], 'X-Challenge-Protocol': 'research-assistant.v1'}
            started, status, raw, response_headers, transport = time.monotonic(), None, b'', {}, None
            request = urllib.request.Request('http://127.0.0.1:8080/solve',
                json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers)
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    status, raw, response_headers = response.status, response.read(), dict(response.headers.items())
            except urllib.error.HTTPError as exc:
                status, raw, response_headers = exc.code, exc.read(), dict(exc.headers.items())
            except (OSError, TimeoutError) as exc:
                transport = type(exc).__name__
            seconds = round(time.monotonic() - started, 3)
            if transport:
                time.sleep(2)  # 只等取诊断，不将180秒后的产物算作及时答卷。
            (directory / 'response.raw.txt').write_text(audit.redact(raw.decode('utf-8', errors='replace')), encoding='utf-8')
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = None
            audit.save(directory / 'response.json', body)
            audit.save(directory / 'response.headers.json', response_headers)
            row = audit.check(specimen, body, raw, status)
            if specimen.get('require_nonempty'):
                nonempty = bool(isinstance(body, dict) and body.get('papers'))
                row['checks']['相关题必须有真实匹配论文'] = nonempty
                if not nonempty:
                    row['failures'].append('相关题必须有真实匹配论文')
                    row['format_and_files_passed'] = False
            with audit.LOCK:
                spans = list(audit.SPANS)
            selected = [span for span in spans if span['trace_id'] == trace_id]
            sessions = [span for span in selected if span['name'] == 'agent.session']
            metadata = json.loads(response_headers.get('x-agenthack-metadata', response_headers.get('X-AgentHack-Metadata', '{}')))
            row.update(label=specimen['label'], case_id=payload['case_id'], function=payload['function'],
                       http_status=status, seconds=seconds, transport_error=transport,
                       llm_calls=metadata.get('llm_calls'), total_tokens=metadata.get('total_tokens'),
                       local_session_trace_received=bool(sessions), sdk_metadata=metadata)
            rows.append(row)
            audit.save(directory / 'checks.json', row)
            audit.save(audit.ROOT / 'summary.json', {'rows': rows, 'uses_official_model_api': True,
                'inputs_are_official_hidden_questions': False, 'independent_spec_validation': True})
            audit.save(audit.ROOT / 'otlp-spans.json', spans)
            print(f"{specimen['label']}: HTTP {status}; format/files={'PASS' if row['format_and_files_passed'] else 'FAIL'}; calls={row['llm_calls']}; {seconds}s", flush=True)
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
