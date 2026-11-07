"""Enforce the user's baseline-first rule in external release coordination.

This module is outside every image build context. It never grants native-tool
permission and does not modify the Agent, SDK, dependencies or packaging logic.
"""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
POLICY=ROOT/'.session-audit/baseline-preservation-policy-20261106.json'
RELEASE_STAGES={'platform-canary','be-submit','be-release','be-platform',
                'academic-score-preflight','academic-score-final'}

def enforce(stage, candidate=None):
    if stage not in RELEASE_STAGES:
        return
    if not POLICY.is_file():
        raise RuntimeError('Baseline preservation policy is missing; release coordination stopped before execution')
    policy=json.loads(POLICY.read_text(encoding='utf-8'))
    baseline=policy['official_baseline']
    if not baseline.get('binding_verified') or not baseline.get('source_commit'):
        raise RuntimeError('80.9 baseline source identity is unbound; release coordination stopped before execution')
    if policy.get('submission_status')!='approved' or candidate not in policy.get('approved_candidates',[]):
        raise RuntimeError('Candidate has no baseline-preservation and single-module adoption evidence; release coordination stopped')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--stage',required=True);parser.add_argument('--candidate')
    args=parser.parse_args();enforce(args.stage,args.candidate)
