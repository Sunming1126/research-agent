"""Package the isolated B6 build with reproducible checks and all development results."""
import hashlib,json,shutil,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
import fitz
ROOT=Path(__file__).resolve().parents[1]
DESKTOP=Path(r'C:/Users/sun/Desktop/anic')
VERSION='0.4.13-test1-2-b6-20261031'
IMAGE='research-assistant:'+VERSION
OUT=ROOT/'output/b6-20261031'
FINAL=['b6-release-public-20261031','b6-release-controlled-20261031']
RUNS=['b5-controlled-20261031','b6-public-20261031','b6-controlled-20261031','b6-review-public-20261031',
      'b6-task-only-public-20261031','b6-task-only-controlled-20261031','b6-final-public-20261031','b6-final-controlled-20261031',*FINAL]
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def docker(*args):return subprocess.check_output(['docker',*args],text=True,encoding='utf-8')
def score(run):
    folder=DESKTOP/'output'/run
    frozen=load(DESKTOP/'output'/(run+'-frozen-questions.json'))
    expected=frozen.get('expected') or {x[0]:x[2] for x in frozen['cases']}
    # The pre-existing B5 developer question omitted RETRO in its original fixture.
    # Preserve that frozen file; reuse the documented source-reviewed correction.
    correction=False
    if 'cases' in frozen:
        expected['B-01']=load(ROOT/'output/b5-20261031/manual-gold-review.json')['corrected_B01_expected'];correction=True
    rows=[]
    for r in load(folder/'summary.json')['rows']:
        body=load(folder/'cases'/r['label']/'response.json')
        actual={x['filename'].casefold() for x in body.get('papers',[])};gold={x.casefold() for x in expected[r['case_id']]}
        h=len(actual&gold);u=len(actual-gold);n=len(gold)
        trace_paths=list(folder.glob('agent-output/data/workspace-live-audit/work/'+r['case_id']+'/*/evidence.json'))
        trace=load(trace_paths[-1]).get('retrieval_trace',{}) if trace_paths else {}
        rows.append(dict(case_id=r['case_id'],hits=h,relevant=n,false_positives=u,
            local_score=max(0,min(15,15*h/n-.75*u)) if n else None,
            missed=sorted(gold-actual),extra=sorted(actual-gold),seconds=r['seconds'],calls=r['llm_calls'],
            format_pass=r['format_and_files_passed'],decided=len(trace.get('decisions',{})),
            unprocessed=len(trace.get('unprocessed',[])),outcome=trace.get('outcome'),errors=trace.get('errors',[])))
    return dict(run=run,image_id=load(folder/'provenance.json')['image_id'],rows=rows,
        gold_source='Controlled fixture construction' if 'expected' in frozen else 'Public-paper manually reviewed developer gold',
        historical_gold_correction_documented=correction)

def main():
    info=json.loads(docker('image','inspect',IMAGE))[0];proof=load(OUT/'image-proof.json')
    assert info['Id']==proof['image_id']
    counts={}
    for name in ['host-final','http-final','linux-final']:
        suites=list(ET.parse(OUT/(name+'.xml')).getroot().iter('testsuite'))
        assert all(int(s.get('failures','0'))==int(s.get('errors','0'))==0 for s in suites)
        counts[name]=sum(int(s.get('tests','0'))-int(s.get('skipped','0')) for s in suites)
    b5=load(OUT/'b5-source-sha256.json')
    delta=[name for name,digest in b5.items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest]
    assert set(delta)=={'src/research_agent/workflows/b_workflow.py','src/research_agent/workflows/b_evidence.py','src/research_agent/integrations/b_prompt.py'}
    results=[score(run) for run in RUNS]
    for run in FINAL:
        folder=DESKTOP/'output'/run;prov=load(folder/'provenance.json')
        assert prov['image_id']==info['Id'] and not prov['oom_killed']
        sessions=[s for s in load(folder/'otlp-spans.json') if s['name']=='agent.session']
        assert len(sessions)==3 and all(s['attributes'].get('success') is True for s in sessions)
        rows=load(folder/'summary.json')['rows'];assert len(rows)==3
        for r in rows:
            assert r['format_and_files_passed'] and r['local_session_trace_received'] and r['seconds']<180,r
            response=load(folder/'cases'/r['label']/'response.json')
            assert set(response)=={'case_id','papers'}
            assert all(set(x)=={'filename','reason'} for x in response['papers'])
            assert response==load(folder/'agent-output/data/workspace-live-audit'/(r['case_id']+'.json'))
    # PDFs may have different document IDs; verify their extracted text is identical.
    text_hashes={}
    for run in ['b5-controlled-20261031','b6-release-controlled-20261031']:
        hashes={}
        for p in (DESKTOP/'build'/run/'data/papers').glob('*.pdf'):
            with fitz.open(p) as doc:text=''.join(page.get_text() for page in doc)
            hashes[p.name]=hashlib.sha256(text.encode()).hexdigest()
        text_hashes[run]=hashes
    assert len(text_hashes['b5-controlled-20261031'])==52
    assert text_hashes['b5-controlled-20261031']==text_hashes['b6-release-controlled-20261031']
    secret=dict(line.split('=',1) for line in (DESKTOP/'.env.local').read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.startswith('#'))['PLATFORM_API_KEY'].encode()
    for run in RUNS:
        for p in (DESKTOP/'output'/run).rglob('*'):
            if p.is_file():assert secret not in p.read_bytes(),'Secret in audit artifact'
    for name in proof['changed_existing']+proof['new_files']:
        raw=(ROOT/'src'/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==proof['source_hashes'][name]
        assert secret not in raw
    target=DESKTOP/'team_share/image'/('research-assistant-'+VERSION+'.tar')
    partial=target.with_suffix('.tar.partial')
    assert not target.exists() and not partial.exists()
    docker('save','-o',str(partial),IMAGE)
    with partial.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    docker('load','-i',str(partial));assert json.loads(docker('image','inspect',IMAGE))[0]['Id']==info['Id']
    partial.rename(target);target.with_suffix('.tar.sha256').write_text(digest+'  '+target.name+'\n',encoding='ascii')
    manifest=dict(version=VERSION,image=IMAGE,image_id=info['Id'],tar=str(target),tar_sha256=digest,
        tar_bytes=target.stat().st_size,tar_load_verified=True,tests=counts,source_branch='codex/b-retrieval-e4',
        source_worktree=str(ROOT),changed_from_b5=delta,source_committed=False,official_submission_performed=False,
        official_score_claimed=False,image_proof=proof,development_results=results,
        controlled_source_text_identical=True,preserved_categories=['A','C','D','E'],
        limitations=['No official B questions/answers/logs available; the exact B-02 failure is unconfirmed.',
                    'Controlled fixtures and public developer tests are not an independent official test set.',
                    'Large libraries still have time/candidate/fulltext limits; semantic false negatives can remain.',
                    'B5 controlled comparison includes a model timeout; concurrent service latency was not controlled.'])
    doc=ROOT/'docs/2026-10-31-B6检索失分分析与修复.md'
    lines=['','## 实测记录（不是官方分数）','','|版本/轮次|题号|命中/应命中|误收|本地公式得分|已判候选|未审候选|耗时秒|','|---|---|---|---|---|---|---|---|']
    for result in results:
        for r in result['rows']:
            lines.append(f"|{result['run']}|{r['case_id']}|{r['hits']}/{r['relevant']}|{r['false_positives']}|{r['local_score']}|{r['decided']}|{r['unprocessed']}|{r['seconds']}|")
    lines+=['',f'回归通过：{counts}，总计 {sum(counts.values())} 项。',f'镜像 ID：`{info["Id"]}`。',
            f'镜像包：`{target}`。',f'SHA-256：`{digest}`。','未提交、未推送代码，未提交官方评测。']
    content=doc.read_text(encoding='utf-8')+'\n'.join(lines)+'\n'
    doc.write_text(content,encoding='utf-8')
    release=ROOT/'team_share/releases'/VERSION;release.mkdir(parents=True,exist_ok=True)
    (release/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (release/'comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (release/'controlled-text-hashes.json').write_text(json.dumps(text_hashes,indent=2)+'\n',encoding='utf-8')
    for n in ['image-proof.json','host-final.xml','http-final.xml','linux-final.xml']:
        shutil.copyfile(OUT/n,release/n)
    shutil.copyfile(doc,release/'README.md')
    shutil.copyfile(ROOT/'output/b5-20261031/manual-gold-review.json',release/'historical-gold-review.json')
    destination=DESKTOP/'team_share/releases'/VERSION
    assert not destination.exists();shutil.copytree(release,destination)
    print(json.dumps(dict(tar=str(target),sha256=digest,tests=counts,results=[r for r in results if r['run'] in FINAL]),ensure_ascii=False))
if __name__=='__main__':main()
