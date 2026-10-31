"""Verify B5 provenance/tests, retain every development round, and export the reviewed image."""
import hashlib
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DESKTOP=Path(r'C:/Users/sun/Desktop/anic')
VERSION='0.4.13-test1-2-b5-20261031'
IMAGE='research-assistant:'+VERSION
FINAL='b5-release-check-20261031'
HOLDOUT='b5-fresh-20261031'
EVIDENCE=ROOT/'output/b5-20261031'

def load(path):return json.loads(path.read_text(encoding='utf-8'))
def docker(*args):return subprocess.check_output(['docker',*args],text=True,encoding='utf-8')

def score_run(run,correct_b01=True):
    folder=DESKTOP/'output'/run
    frozen=load(DESKTOP/'output'/(run+'-frozen-questions.json'))
    expected={cid:names for cid,_,names in frozen['cases']}
    if correct_b01:expected['B-01']=load(EVIDENCE/'manual-gold-review.json')['corrected_B01_expected']
    summary=load(folder/'summary.json');rows=[]
    for row in summary['rows']:
        body=load(folder/'cases'/row['label']/'response.json')
        actual={p['filename'].casefold() for p in body.get('papers',[])}
        gold={n.casefold() for n in expected[row['case_id']]}
        h=len(actual&gold);u=len(actual-gold);n=len(gold)
        score=max(0,min(15,15*h/n-.75*u)) if n else None
        rows.append({'case_id':row['case_id'],'H':h,'N':n,'U':u,'score':score,
                     'precision':h/len(actual) if actual else (1 if not gold else 0),
                     'recall':h/n if n else None,'false_positives':sorted(actual-gold),'missed':sorted(gold-actual),
                     'format_and_files_passed':row['format_and_files_passed'],
                     'seconds':row['seconds'],'llm_calls':row['llm_calls']})
    scores=[r['score'] for r in rows if r['score'] is not None]
    return {'run':run,'image_id':load(folder/'provenance.json')['image_id'],'rows':rows,
            'mean_score':sum(scores)/len(scores) if scores else None,
            'note':'Developer-authored public tests, not official scores; N=0 is an empty-set test without applying the nonzero-N formula.'}

def main():
    inspection=json.loads(docker('image','inspect',IMAGE))[0]
    proof=load(EVIDENCE/'image-proof.json')
    assert inspection['Id']==proof['image_id']
    for run in (FINAL,HOLDOUT):
        folder=DESKTOP/'output'/run;provenance=load(folder/'provenance.json')
        assert provenance['image_id']==inspection['Id'] and provenance['official_api'] and not provenance['oom_killed']
        rows=load(folder/'summary.json')['rows'];assert len(rows)==3
        for row in rows:
            assert row['format_and_files_passed'] and row['local_session_trace_received'] and row['seconds']<180 and row['llm_calls']>0,row
            body=load(folder/'cases'/row['label']/'response.json')
            assert set(body)=={'case_id','papers'}
            assert body==load(folder/'agent-output/data/workspace-live-audit'/(row['case_id']+'.json'))
    counts={}
    for name in ('host-final','http-final','linux-final'):
        suites=list(ET.parse(EVIDENCE/(name+'.xml')).getroot().iter('testsuite'))
        assert all(int(s.get('errors',0))==int(s.get('failures',0))==int(s.get('skipped',0))==0 for s in suites)
        counts[name]=sum(int(s.get('tests',0)) for s in suites)
    assert sum(counts.values())>=288
    runs=['b5-baseline-valid-20261031','b5-first-20261031','b5-second-20261031','b5-third-20261031','b5-final-20261031','b5-verified-20261031',FINAL]
    comparison=[score_run(r) for r in runs]
    scope_regressions=[score_run('b5-holdout-20261031',False),score_run('b5-scope-recheck-20261031',False)]
    scope_check=scope_regressions[-1]
    assert scope_check['image_id']==inspection['Id'] and all(r['format_and_files_passed'] for r in scope_check['rows'])
    heldout=score_run(HOLDOUT,False)
    assert comparison[-1]['mean_score']>comparison[0]['mean_score'],'Latest same-question result did not improve over the baseline'
    env=dict(line.split('=',1) for line in (DESKTOP/'.env.local').read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.startswith('#'))
    secret=env['PLATFORM_API_KEY'];assert secret not in json.dumps(inspection)
    for run in runs+[HOLDOUT,'b5-holdout-20261031','b5-scope-recheck-20261031']:
        for path in (DESKTOP/'output'/run).rglob('*'):
            if path.is_file() and path.suffix in ('.json','.log','.txt','.md'):
                assert secret.encode() not in path.read_bytes(),'Credential found in audit artifact'
    for name in proof['changed_existing']+proof['new_files']:
        data=(ROOT/'src'/name).read_bytes()
        assert hashlib.sha256(data).hexdigest()==proof['source_hashes'][name]
        assert secret.encode() not in data
    target=DESKTOP/'team_share/image'/('research-assistant-'+VERSION+'.tar')
    partial=target.with_suffix('.tar.partial')
    assert not target.exists() and not partial.exists(),'Never overwrite an existing delivery'
    docker('save','-o',str(partial),IMAGE)
    with partial.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    docker('load','-i',str(partial))
    assert json.loads(docker('image','inspect',IMAGE))[0]['Id']==inspection['Id']
    partial.rename(target)
    target.with_suffix('.tar.sha256').write_text(f'{digest}  {target.name}\n',encoding='ascii')
    release=ROOT/'team_share/releases'/VERSION;release.mkdir(parents=True,exist_ok=True)
    manifest={'version':VERSION,'image':IMAGE,'image_id':inspection['Id'],'tar':str(target),
              'tar_sha256':digest,'tar_bytes':target.stat().st_size,'tar_load_verified':True,
              'source_branch':'codex/b-retrieval-e4','source_base_commit':'1349889','source_worktree':str(ROOT),
              'source_committed':False,'official_submission_performed':False,'official_score_claimed':False,
              'tests':counts,'image_proof':proof,'all_development_runs':comparison,'scope_regressions':scope_regressions,'heldout':heldout,
              'gold_review':load(EVIDENCE/'manual-gold-review.json'),
              'large_library_profile':load(EVIDENCE/'profile/large-library-profile.json'),
              'limitations':['公开开发题不是官方隐藏题；小样本不能证明稳定的平台增分。',
                             '300篇检查只验证冷启动性能；前页无相关线索的正文可能漏检。',
                             '模型仍可能把原文的研究范围或主要贡献判错；编号原文保证可追溯，不等于语义永远正确。'],
              'preserved_categories':['A','C','D','E'],'credentials_excluded':True}
    (release/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (release/'comparison.json').write_text(json.dumps({'development':comparison,'scope_regressions':scope_regressions,'heldout':heldout},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for name in ('image-proof.json','manual-gold-review.json','host-final.xml','http-final.xml','linux-final.xml'):
        shutil.copyfile(EVIDENCE/name,release/name)
    shutil.copyfile(EVIDENCE/'profile/large-library-profile.json',release/'large-library-profile.json')
    doc=ROOT/'docs/2026-10-31-B条件检索优化与验证.md'
    lines=['\n## 最终实测结果\n','|版本/轮次|B-01 H/N,U|B-02 H/N,U|B-03 H/N,U|开发集均分|','|---|---|---|---|---|']
    for record in comparison:
        cells=[f"{r['H']}/{r['N']}, {r['U']}" for r in record['rows']]
        lines.append('|'+record['run']+'|'+'|'.join(cells)+f"|{record['mean_score']:.2f}/15|")
    lines+=['','首批追加题发现作用域错误，已作为调试回归，不能再视为独立验证。修复前后：',
            '|轮次|题号|H/N|U|本地分|耗时秒|','|---|---|---|---|---|---|']
    for record in scope_regressions:
        for r in record['rows']:
            score='—' if r['score'] is None else f"{r['score']:.2f}"
            lines.append(f"|{record['run']}|{r['case_id']}|{r['H']}/{r['N']}|{r['U']}|{score}|{r['seconds']}|")
    lines+=['','新追加题（同一公开语料、不同问题，未按其结果调参）：',
            '|题号|H/N|U|本地分|耗时秒|','|---|---|---|---|---|']
    for r in heldout['rows']:
        lines.append(f"|{r['case_id']}|{r['H']}/{r['N']}|{r['U']}|{r['score']:.2f}|{r['seconds']}|")
    lines+=['','剩余问题：原对照B-01仍漏掉RAG和FiD；机制回归题误收Self-RAG；新追加B-01误收FiD。编号原文仍需由模型解释，模型对创新范围、主要研究目标和相似机制的判断尚不稳定。未通过硬编码论文名单修正这些结果。',
            '',f"回归：{counts}；合计{sum(counts.values())}项通过。",
            f"镜像：`{IMAGE}`；ID：`{inspection['Id']}`。",f"交付：`{target}`；SHA-256：`{digest}`。",
            '代码尚未提交或推送；原集成工作树未改动。']
    doc.write_text(doc.read_text(encoding='utf-8')+'\n'.join(lines)+'\n',encoding='utf-8')
    shutil.copyfile(doc,release/'README.md')
    destination=DESKTOP/'team_share/releases'/VERSION
    assert not destination.exists()
    shutil.copytree(release,destination)
    print(json.dumps({'tar':str(target),'sha256':digest,'bytes':target.stat().st_size,'tests':counts,
                      'baseline_mean':comparison[0]['mean_score'],'latest_mean':comparison[-1]['mean_score'],
                      'latest':comparison[-1]['rows'],'heldout':heldout['rows']},ensure_ascii=False))

if __name__=='__main__':main()
