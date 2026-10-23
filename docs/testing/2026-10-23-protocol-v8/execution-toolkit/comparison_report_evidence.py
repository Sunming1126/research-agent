"""Independent PDF page lookup for report evidence; no semantic gold scores."""
import json
from pathlib import Path
import re
import unicodedata
import fitz


def normalize(text):return ''.join(re.findall(r'\w',unicodedata.normalize('NFKC',text).lower()))


def main():
    result=[];pages={}
    for cid in ['C-01','C-02','E-01','E-02']:
        files=list((Path('/output/data/workspace_team/work')/cid).glob('*/evidence.json'))
        if len(files)!=1:
            result.append({'case_id':cid,'status':'BLOCKED','reason':'No unique completed evidence ledger'});continue
        ledger=json.loads(files[0].read_text(encoding='utf-8')).get('claims',[])
        checks=[]
        for claim in ledger:
            filename=claim.get('filename','');page=claim.get('page');quote=claim.get('quote','')
            item={'evidence_id':claim.get('evidence_id'),'filename':filename,'page':page}
            try:
                if Path(filename).name!=filename:raise ValueError('Unsafe PDF name')
                if not isinstance(page,int) or page<1:raise ValueError('Invalid evidence page')
                if filename not in pages:
                    with fitz.open(Path('/papers')/filename) as pdf:
                        pages[filename]=[
                            [normalize(p.get_text('text',sort=True)),normalize(p.get_text('text',sort=False)),
                             normalize('\n'.join(b[4] for b in p.get_text('blocks',sort=True) if len(b)>6 and b[6]==0))]
                            for p in pdf]
                if page>len(pages[filename]):raise ValueError('Page outside PDF')
                text=normalize(quote)
                item['quote_found_on_cited_page']=bool(text) and any(text in variant for variant in pages[filename][page-1])
                if not item['quote_found_on_cited_page']:
                    item['quote_for_review']=quote
                    item['found_on_other_pages']=[i+1 for i,variants in enumerate(pages[filename]) if any(text in v for v in variants)]
            except Exception as exc:
                item.update(quote_found_on_cited_page=False,error=type(exc).__name__+': '+str(exc))
            checks.append(item)
        found=sum(x['quote_found_on_cited_page'] for x in checks)
        result.append({'case_id':cid,'status':'PASS' if checks and found==len(checks) else 'REVIEW',
                       'quotes_found':found,'quotes_checked':len(checks),'checks':checks,
                       'scope':'Independent source/page/quote lookup only; not a full factual or originality score'})
    Path('/results/report-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{k:v for k,v in x.items() if k!='checks'} for x in result],ensure_ascii=False))


if __name__=='__main__':main()
