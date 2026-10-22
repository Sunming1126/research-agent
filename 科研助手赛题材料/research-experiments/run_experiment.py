"""Public Agent helper: request this case once and save its actual outputs.

Available inside the platform at /data/experiments/run_experiment.py. Local
development can run the included frozen experiment directly; this helper needs
the short-lived platform session. It never reads hidden task configs or golds.
"""
import argparse
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import urllib.request


def run(case_id):
    if case_id != os.environ.get('RESEARCH_EXPERIMENT_CASE'):
        raise ValueError('This case is not active in the current platform session')
    url = os.environ['RESEARCH_EXPERIMENT_URL']
    if not url.startswith('unix:///'):
        raise ValueError('Platform experiment socket unavailable')
    class UnixConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect(url[len('unix://'):])
    connection = UnixConnection('localhost', timeout=110)
    try:
        connection.request('POST', '/run', body=json.dumps({'case_id': case_id}).encode(),
            headers={'Content-Type':'application/json',
                'Authorization':'Bearer ' + os.environ['RESEARCH_EXPERIMENT_TOKEN']})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError('Trusted experiment request failed; do not invent metrics')
        result = json.load(response)
    finally:
        connection.close()
    if result.get('status') != 'complete' or result.get('case_id') != case_id:
        raise RuntimeError('Experiment did not complete; do not invent metrics')
    submission = result['submission']
    team = os.environ['RESEARCH_WORKSPACE_TEAM']
    source_root = Path('/data/experiment-results')
    output = Path(os.environ.get('RESEARCH_RESULT_ROOT', '/output'))
    for figure in submission['figures']:
        relative = PurePosixPath(figure['path'])
        if '..' in relative.parts or relative.is_absolute() or not str(relative).startswith(f'data/figure/{team}/'):
            raise ValueError('Invalid experiment figure path')
        target = output / str(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / str(relative), target)
    target = output / 'data' / team / (case_id + '.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(submission, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return submission


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.case), ensure_ascii=False, allow_nan=False))
