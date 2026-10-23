"""Paired correction of a test-author mistake: Survey needs >=5 papers.

Original two-paper C-02 runs are retained, and are negative-input observations,
not product failures. This separate run never overwrites their raw evidence.
"""
import argparse
import hashlib
import json
import os
import time

import test_agent as T
from compare_images import BASE, IMAGES, RUN, metadata, quote_checks
from harness_bridge import HarnessBridge, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=IMAGES, required=True)
    args = parser.parse_args(); os.environ['TEST_HARNESS_REASONING'] = 'off'
    task = T.request_for('C-01')
    task['task_id'] = 'comparison-public-C-02-corrected'
    task['input'].update(case_id='C-02', title='检索方法、生成方法与评测基准的边界',
                         input='比较指定五篇 DPR、RAG、FiD、BEIR、Self-RAG：检索器、检索增强生成、评测基准分别解决什么问题，训练与推理阶段如何使用证据；说明评测与可核验的局限。正文至少800字，实际引用全部五篇，区分论文事实与推断。',
                         materials=['2004.04906-DPR.pdf', '2005.11401-RAG.pdf', '2007.01282-FiD.pdf',
                                    '2104.08663-BEIR.pdf', '2310.11511-Self-RAG.pdf'])
    path = BASE / 'corrected-C-02-input.json'
    if path.exists():
        T.require(json.loads(path.read_text(encoding='utf-8')) == task, 'Corrected frozen input changed')
    else:
        write_json(path, task)
    root = T.HERE / 'runs' / (RUN + '-' + args.version + '-c02-corrected')
    root.mkdir(parents=True, exist_ok=False); rec = T.Recorder(root)
    identity = metadata(rec, IMAGES[args.version])
    with HarnessBridge(root / 'harness-model-calls') as bridge, T.target(root / 'live-ABCE', bridge=bridge) as (base, output, name):
        def solve():
            started = time.monotonic(); code, body = T.http(base + '/solve', task)
            elapsed = time.monotonic() - started
            write_json(root / 'live-ABCE/C-02-response.json', {'http': code, 'seconds': elapsed, 'response': body})
            evidence = T.independent_contract(task, T.unpack_answer(body), output)
            T.require(elapsed <= 180, 'Case exceeded budget')
            T.require(bridge.records, 'No actual model call')
            return dict(evidence, seconds=elapsed, real_model_calls=len(bridge.records))
        rec.test('live.C-02.corrected', 'A-E-live', solve)
    # Same independent quote/page verifier as the primary suite. Missing other
    # cases in this single-case run are expected and are not counted as failures.
    quote_checks(rec)
    write_json(root / 'summary.json', {'target': IMAGES[args.version], 'id': identity,
               'correction_of_test_author_error': True, 'official_score': None,
               'input_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})


if __name__ == '__main__':
    main()
