"""Portable paths to the committed public fixtures; never platform hidden data."""
import os
from pathlib import Path


def experiment_root():
    root = Path(os.environ.get('LAYERED_PUBLIC_EXPERIMENT_ROOT',
        str(Path(__file__).resolve().parents[1] / '科研助手赛题材料/research-experiments-20261020-source-v1')))
    if not all((root / name / 'protocol.json').is_file() for name in ('D-01', 'D-02', 'D-05')):
        raise FileNotFoundError('Configure LAYERED_PUBLIC_EXPERIMENT_ROOT to the unmodified public fixture package')
    return root
