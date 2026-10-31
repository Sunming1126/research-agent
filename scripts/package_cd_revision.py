"""Verify scoped changes, current model evidence and export a reviewable tar."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT/'output/cd2-20261030'
VERSION = '0.4.13-test1-2-cd2-20261030'
IMAGE = 'research-assistant:'+VERSION
FOUNDATION = 'research-assistant:0.4.13-test1-2-20261030'
PREVIOUS = 'research-assistant:0.4.13-test1-2-d1-20261030'
ALLOWED = {'src/research_agent/'+p for p in (
    'integrations/prompts.py','integrations/survey_prompt.py','workflows/reports.py',
    'workflows/report_context.py','workflows/survey_sources.py','workflows/layered_d_runner.py',
    'workflows/sample_validation.py','workflows/sample_worker.py','workflows/d02_policy.py')}


def docker(*args):
    return subprocess.check_output(['docker',*args],text=True,encoding='utf-8')


def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def digest(p):
    with Path(p).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def image_files(tag):
    code="import hashlib,json;from pathlib import Path;print(json.dumps({str(p.relative_to('/app')):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app').rglob('*.py')}))"
    return json.loads(docker('run','--rm','--network','none','--entrypoint','python',tag,'-c',code))


def main():
    before=load(AUDIT/'before-hashes.json')
    changed=[p for p,h in before.items() if digest(ROOT/p)!=h]
    assert set(changed)<=ALLOWED,changed
    previous,current=image_files(PREVIOUS),image_files(IMAGE)
    difference=sorted(p for p in previous.keys()|current.keys() if previous.get(p)!=current.get(p))
    assert set(difference)==ALLOWED,difference
    for p,h in current.items():
        if (ROOT/p).is_file():assert digest(ROOT/p)==h,p
    assert not any('/d_algorithms/' in p for p in current)
    foundation,image=[json.loads(docker('image','inspect',t))[0] for t in (FOUNDATION,IMAGE)]
    assert foundation['Config']==image['Config']
    assert image['RootFS']['Layers'][:len(foundation['RootFS']['Layers'])]==foundation['RootFS']['Layers']
    docker('run','--rm','--network','none','--entrypoint','python',IMAGE,'-m','pip','check')
    count=0
    for name in ('core-final.xml','report-release.xml','http-release.xml'):
        suites=ET.parse(AUDIT/name).getroot().findall('testsuite')
        assert all(all(int(s.get(k,0))==0 for k in ('failures','errors','skipped')) for s in suites)
        count+=sum(int(s.get('tests',0)) for s in suites)
    c_root=Path('C:/Users/sun/Desktop/anic/output/cd2-verified-c-20261030')
    c=load(c_root/'summary.json')['rows']
    assert len(c)==2 and all(r['format_and_files_passed'] and all(a.get('ok') for a in r['report_audits']) for r in c)
    c_provenance=load(c_root/'provenance.json')
    c_image=c_provenance['image_id']
    assert c_image==image['Id']
    d02=load(AUDIT/'live-d02-final/summary.json')
    assert len(d02)==1 and d02[0]['image_id']==image['Id'] and d02[0]['status']=='passed_public_sample'
    assert d02[0]['source_matches_current_response']
    check=d02[0]['public_selftest'];selected=next(r for r in check['attempts'] if r['attempt']==check['selected_attempt'])
    assert selected['candidate_present'] and selected['rank_consistent'] and selected['baseline_rank_consistent']
    repeat=load(AUDIT/'live-d02-repeat/summary.json')
    assert len(repeat)==1 and repeat[0]['image_id']==image['Id'] and repeat[0]['status']=='passed_public_sample'
    repeat_check=repeat[0]['public_selftest'];selected_repeat=next(r for r in repeat_check['attempts'] if r['attempt']==repeat_check['selected_attempt'])
    assert selected_repeat['candidate_present'] and selected_repeat['rank_consistent'] and selected_repeat['baseline_rank_consistent']
    repeat_worker=load(AUDIT/'worker-repeat/final-validation.json')
    assert repeat_worker['all_passed']
    assert all(r['source_sha256']==repeat[0]['source_sha256'] for r in repeat_worker['cases'] if r['case_id']=='D-02')
    worker=load(AUDIT/'worker-verified/final-validation.json')
    assert worker['all_passed'] and worker['run_cases']==17 and worker['rank_cases']==7
    assert all(r['source_sha256']==d02[0]['source_sha256'] for r in worker['cases'] if r['case_id']=='D-02')
    benchmark=load(AUDIT/'worker-verified/D-02-public_sample.json')['benchmarks']
    assert len(benchmark)==6 and all(r['seconds']<20 for r in benchmark)
    result=dict(version=VERSION,image=IMAGE,image_id=image['Id'],branch='test1-2',
        base_commit='5656fd52e0eaa32bc5b253dee226488a6449a22b',
        changed_from_d1=difference,protected_existing_files=len(before)-len(changed),
        sdk_dependencies_runtime_config_preserved=True,regressions_passed=count,
        c_checks=[{k:r[k] for k in ('case_id','seconds','llm_calls','total_tokens','body_characters','citation_instances','format_and_files_passed','report_audits')} for r in c],
        c_test_image_id=c_image,c_production_code_identical_to_test=True,
        d02_checks=d02,d02_independent_repeat=repeat,worker=worker,repeat_worker=repeat_worker,benchmarks=benchmark,official_hidden_evaluation=False,
        limitation='Local API/public checks do not establish official score or the exact cause of eval-b5c0f3f8.')
    target=ROOT/'team_share/image'/('research-assistant-'+VERSION+'.tar')
    partial=target.with_suffix('.tar.partial')
    assert not target.exists() and not partial.exists()
    docker('save','-o',str(partial),IMAGE)
    sha=digest(partial)
    docker('load','-i',str(partial))
    assert json.loads(docker('image','inspect',IMAGE))[0]['Id']==image['Id']
    partial.rename(target)
    target.with_suffix('.tar.sha256').write_text(sha+'  '+target.name+'\n',encoding='ascii')
    result.update(tar=str(target),sha256=sha,bytes=target.stat().st_size,docker_load_verified=True)
    release=ROOT/'team_share/releases'/VERSION;release.mkdir(parents=True,exist_ok=False)
    (release/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (AUDIT/'final-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (release/'README.md').write_text('C/D专项优化，test1-2，基础依赖和内部版本仍为0.4.13。\n'
        '本地验证不是官方分数；请按完整镜像标签和SHA256辨认。详见manifest和专项分析文档。\n',encoding='utf-8')
    desktop=Path('C:/Users/sun/Desktop/anic/team_share/image')/target.name
    assert not desktop.exists()
    shutil.copyfile(target,desktop);assert digest(desktop)==sha
    shutil.copyfile(target.with_suffix('.tar.sha256'),desktop.with_suffix('.tar.sha256'))
    print(json.dumps(dict(tar=str(desktop),sha256=sha,bytes=result['bytes'],tests=count),ensure_ascii=False))


if __name__=='__main__':main()
