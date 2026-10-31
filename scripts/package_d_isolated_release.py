"""Package only a verified D-only revision of the officially tested test1-2 base."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = 'research-assistant:0.4.13-test1-2-20261030'
VERSION = '0.4.13-test1-2-d1-20261030'
IMAGE = 'research-assistant:' + VERSION
ALLOWED = {'src/research_agent/workflows/' + name for name in (
    'layered_d_runner.py','source_generation.py','sample_validation.py',
    'd_generation_policy.py','d_public_review.py')}
AUDIT = ROOT/'output/d-isolated-20261030'


def docker(*args):
    return subprocess.check_output(['docker', *args],text=True,encoding='utf-8')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify():
    before = load(AUDIT/'before-hashes.json')
    changed = [name for name,digest in before.items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest]
    assert set(changed) <= ALLOWED, changed
    code = "import hashlib,json;from pathlib import Path;print(json.dumps({str(p.relative_to('/app')):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app').rglob('*.py')}))"
    a,b = [json.loads(docker('run','--rm','--network','none','--entrypoint','python',tag,'-c',code)) for tag in (BASE,IMAGE)]
    difference = sorted(name for name in a.keys() | b.keys() if a.get(name) != b.get(name))
    assert set(difference) == ALLOWED, difference
    for name,digest in b.items():
        if (ROOT/name).is_file():
            assert digest == hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), name
    assert not any('/d_algorithms/' in name for name in b)
    base,image = [json.loads(docker('image','inspect',tag))[0] for tag in (BASE,IMAGE)]
    assert base['Id']=='sha256:b043d4a8bcc49401e4661c17b6db22b83a45a84b5fab4896522e6d427c0aded7'
    assert base['Config'] == image['Config']
    assert image['RootFS']['Layers'][:len(base['RootFS']['Layers'])] == base['RootFS']['Layers']
    assert image['Architecture']=='amd64' and image['Os']=='linux' and image['Config']['User']=='agent'
    docker('run','--rm','--network','none','--entrypoint','python',IMAGE,'-m','pip','check')
    assert load(AUDIT/'c-replay-baseline.json') == load(AUDIT/'c-replay-final.json')
    tests = 0
    for name in ('core-final.xml','sdk-final.xml'):
        suites = ET.parse(AUDIT/name).getroot().findall('testsuite')
        assert all(all(int(s.get(key,0))==0 for key in ('failures','errors','skipped')) for s in suites)
        tests += sum(int(s.get('tests',0)) for s in suites)
    live=load(AUDIT/'live-v4/summary.json')
    assert {row['case_id'] for row in live} == {'D-01','D-02','D-05'}
    for row in live:
        assert row['image_id']==image['Id'] and row['status']=='passed_public_sample'
        assert row['source_matches_current_response'] and row['seconds'] < 135
        checks=row['public_selftest']; selected=checks['selected_attempt']
        assert next(item for item in checks['attempts'] if item['attempt']==selected)['candidate_present']
    worker = load(AUDIT/'worker/final-validation.json')
    assert worker['all_passed']
    return dict(image=IMAGE,image_id=image['Id'],branch='test1-2',base_commit='5656fd52e0eaa32bc5b253dee226488a6449a22b',
                changed_production_files=difference,protected_file_count=len(before)-len(changed),
                dependencies_and_sdk_layers_preserved=True,runtime_configuration_unchanged=True,
                c_replay_scenarios=20,c_replay_identical=True,c_replay_expected_failures=8,
                regressions_passed=tests,live_d=live,official_worker=worker,official_platform_tested=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--package',action='store_true');args=parser.parse_args()
    result=verify()
    (AUDIT/'final-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.package:
        target=ROOT/'team_share/image'/('research-assistant-'+VERSION+'.tar');partial=target.with_suffix('.tar.partial')
        assert not target.exists() and not partial.exists()
        docker('save','-o',str(partial),IMAGE)
        with partial.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        docker('load','-i',str(partial))
        assert json.loads(docker('image','inspect',IMAGE))[0]['Id']==result['image_id']
        partial.rename(target)
        target.with_suffix('.tar.sha256').write_text(digest+'  '+target.name+'\n',encoding='ascii')
        result.update(tar=str(target),sha256=digest,bytes=target.stat().st_size,docker_load_verified=True)
        release=ROOT/'team_share/releases'/VERSION;release.mkdir(parents=True,exist_ok=False)
        (release/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        (release/'README.md').write_text('D-only revision of test1-2 / 0.4.13. See manifest.json for exact changes and evidence.\n'
            'Public checks are not official hidden evaluation; original runtime version is preserved.\n'
            'Original model source is never patched locally. No platform upload or Git push.\n',encoding='utf-8')
        desktop=Path('C:/Users/sun/Desktop/anic/team_share/image')/target.name
        assert not desktop.exists()
        shutil.copyfile(target,desktop)
        with desktop.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==digest
        shutil.copyfile(target.with_suffix('.tar.sha256'),desktop.with_suffix('.tar.sha256'))
        print(json.dumps({'desktop_tar':str(desktop),'sha256':digest,'bytes':result['bytes']},ensure_ascii=False))
    else: print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
