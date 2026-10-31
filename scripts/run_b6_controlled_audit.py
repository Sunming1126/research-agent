"""52-document controlled retrieval test: 5 public papers + 47 self-authored fixtures.
Ground truth is constructed and frozen before requests, never mounted into /data.
Synthetic fixtures test semantics/budget only; they are not scientific evidence or official questions.
"""
import hashlib,json,sys
from pathlib import Path
import fitz
import run_official_live_audit as runner
runner.ROOT=Path(r'C:/Users/sun/Desktop/anic')
original=runner.specimens
run_id=sys.argv[sys.argv.index('--run-id')+1]
gold={'B-01':[],'B-02':[],'B-03':[]}
QUESTIONS={
'B-01':'检索研究城市绿地与地表温度关系的原创实证论文。重点关注遥感数据、绿地覆盖率和不同城市区域的温度差异。排除只讨论植物分类而没有分析城市温度的论文。不限定篇数。',
'B-02':'查找采用卫星遥感数据、实证量化城市绿地覆盖率与地表温度关系的原创论文。三个条件必须同时满足。排除综述和只讨论植被分类的研究。不限定篇数。',
'B-03':'分别查找两类原创实证研究：（一）用现场测温传感器分析城市绿地与地表温度的关系；（二）用卫星数据定量估计非城市森林的蒸散量。满足任一类即可，不须同时满足。排除综述和仅植物分类。不限篇数。'}

def specimens():
    library=runner.ROOT/'build'/run_id/'data/papers'
    for i in range(47):
        name='Study_'+hashlib.sha256(('b6-controlled-20261031-'+str(i)).encode()).hexdigest()[:12]+'.pdf'
        title='Field investigation '+str(i+101)
        deep=None
        if i<6:
            text='We quantify how urban green-space coverage relates to land surface temperature using Landsat thermal satellite imagery. Our original observational study compares residential and industrial city districts. Higher vegetation fractions are associated with lower surface temperature after adjustment for building density. We measure green cover fraction, not plant taxonomy.'
            gold['B-01'].append(name);gold['B-02'].append(name)
        elif i<10:
            text='Our original empirical study quantifies the relationship between urban green-space cover and land surface temperature using calibrated ground thermometers. We compare tree-shaded and treeless city districts and report a negative association. We do not use satellite remote sensing; all temperature observations come from field sensors. The study concerns thermal exposure, not vegetation taxonomy.'
            gold['B-01'].append(name);gold['B-03'].append(name)
        elif i<12:
            text='Urban green spaces and land surface temperature motivate this original investigation. This opening section introduces the city setting. The data collection approach and measured relationship are given in the methods and results sections on page five; this abstract does not specify them.'
            deep='Methods and results: We use Landsat satellite thermal observations to quantify urban green-space coverage and its relationship with land surface temperature. Green cover fraction is inversely associated with surface temperature across city districts. These are new empirical results, not a review of previous papers.'
            gold['B-01'].append(name);gold['B-02'].append(name)
        elif i<15:
            text='We use thermal satellite imagery to estimate evapotranspiration in remote non-urban forests. Our original empirical measurements concern canopy water flux. We do not investigate city green-space cooling or plant classification; the research area contains no urban districts.'
            gold['B-03'].append(name)
        elif i%4==0:
            text='Our review summarizes previous remote sensing studies of urban green cover and surface temperature. We synthesize published city cooling associations. We collect no new empirical observations and introduce no new estimation method. This article is a literature review, not an original empirical study.'
        elif i%4==1:
            text='We classify plant species in urban green spaces from satellite images. Our original experiment evaluates botanical taxonomy accuracy. Surface temperature and city heat are mentioned as motivation only; no thermal variable is measured or analyzed. We compare leaf morphology classes, not urban temperature differences.'
        elif i%4==2:
            text='Our original study measures land surface temperature across urban road and building materials using satellite observations. It does not study green spaces, vegetation coverage or trees. Green-space cooling is mentioned only in related work. The empirical analysis is restricted to asphalt and concrete albedo.'
        else:
            text='Our original remote sensing experiment maps non-urban wetland vegetation classes. We analyze species classification and habitat extent. There are no city districts, no surface temperature relationships and no forest evapotranspiration estimates. Urban green-space cooling papers are cited as background only.'
        doc=fitz.open()
        for page_index in range(5 if deep else 1):
            page=doc.new_page()
            content=(deep if page_index==4 else text if page_index==0 else 'Study context and sampling logistics. The quantitative measurements and analysis are reported in the subsequent methods section.')
            content=title+' | Synthetic retrieval fixture (not a published study)\n\n'+content
            assert page.insert_textbox(fitz.Rect(40,40,550,780),content,fontsize=12)>=0
        doc.save(library/name);doc.close()
    frozen={'kind':'controlled self-authored fixtures plus public distractors; not official gold',
        'questions':QUESTIONS,'expected':gold,'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(library.glob('*.pdf'))}}
    path=runner.ROOT/'output'/(run_id+'-frozen-questions.json')
    if path.exists():raise RuntimeError('Frozen test already exists')
    path.write_text(json.dumps(frozen,ensure_ascii=False,indent=2),encoding='utf-8')
    replacements={cid:dict(label=cid+'-controlled-52',require_nonempty=True,payload=runner.question(cid,q,['本地论文文件夹内的 PDF'])) for cid,q in QUESTIONS.items()}
    return [replacements.get(x['payload']['case_id'],x) for x in original()]

if __name__=='__main__':
    runner.specimens=specimens
    runner.main()
