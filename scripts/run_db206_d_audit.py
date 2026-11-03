"""Developer-authored unseen contracts, not competition tasks or official scores."""
import json,time,copy
from pathlib import Path
from research_agent.core.config import Settings
from research_agent.core.models import CaseContext
from research_agent.core.session import CaseSession
from research_agent.integrations.llm import GatewayLLM
from research_agent.workflows.layered_d_runner import LayeredDRunner
from research_agent.workflows.d_selftest import run_tests
ROOT=Path('/audit')
CONTRACTS=[('D-02', '''Implement a transactional inventory ledger. Input: phase (preview or summary), initial dict of string keys to nonnegative integer stocks, operations list of {id:string,delta:dict[string,int]}, optional capacity nonnegative integer bounding TOTAL stock. Default initial={} and operations=[]. Process operations in list order, including repeated ids. Compute each entire delta atomically against previous accepted state. Reject an operation if any resulting stock is negative, or if capacity is present and resulting total exceeds capacity. Equality to capacity is allowed. Capacity=0 is a real bound, never a missing value. Rejection changes no keys or values, including keys newly mentioned by that rejected operation. Accepted operations retain zero-valued keys including new ones. In preview return exactly {records:[{id,accepted:boolean,stocks:dict}],final:dict}; each stocks is an independent snapshot after that operation. In summary return exactly {accepted:int,rejected:int,final:dict}. Empty operations is valid; never mutate the request.'''),
('D-05', '''Implement clipped-distance weighted smoothing and radius calibration. Input: phase is smooth or calibrate, values is a list of numbers or null, power positive number default 1. For target index i and integer radius r>=0, use source indices j with abs(i-j)<=r (clip to list endpoints), where values[j] is not null. Weight w=1/(1+abs(i-j))**power. Smooth returns exactly {values:[weighted mean at every index]}; radius defaults to 1. If no usable sources return null. In smooth include the center when it is not null. In calibrate, radii is a nonempty list of distinct nonnegative ints, possibly unsorted. For EACH radius, predict each nonnull index with its own value excluded (leave-one-out), using other nonnull entries in the clipped window. Discard an index from that radius's error calculation only if prediction has no sources. Error is arithmetic mean squared prediction error over usable indices, or null if none. Pick smallest finite error, breaking EXACT error ties by smaller radius regardless of input order. If every error is null, pick minimum radius. Output exactly {chosen_radius:int,scores:[{radius:int,mse:number or null,count:int}],values:[smoothed values using chosen radius including center]}. scores keeps input radii order. Do not use circular wraparound, zero imputation, global mean fallback, or unweighted averaging. Empty values is legal.''')]

def inventory(q):
    stocks=copy.deepcopy(q.get('initial',{}));rows=[];passed=0
    for op in q.get('operations',[]):
        proposal=dict(stocks)
        for k,v in op['delta'].items():proposal[k]=proposal.get(k,0)+v
        ok=all(v>=0 for v in proposal.values()) and ('capacity' not in q or sum(proposal.values())<=q['capacity'])
        if ok:stocks=proposal;passed+=1
        rows.append({'id':op['id'],'accepted':ok,'stocks':dict(stocks)})
    if q['phase']=='preview':return {'records':rows,'final':stocks}
    return {'accepted':passed,'rejected':len(rows)-passed,'final':stocks}

def smoothing(q):
    values=q['values'];power=q.get('power',1)
    def calc(i,r,leave=False):
        pairs=[(values[j],1/(1+abs(i-j))**power) for j in range(max(0,i-r),min(len(values),i+r+1)) if values[j] is not None and (not leave or j!=i)]
        return sum(v*w for v,w in pairs)/sum(w for v,w in pairs) if pairs else None
    if q['phase']=='smooth':return {'values':[calc(i,q.get('radius',1)) for i in range(len(values))]}
    rows=[]
    for r in q['radii']:
        errors=[]
        for i,v in enumerate(values):
            pred=calc(i,r,True)
            if v is not None and pred is not None:errors.append((pred-v)**2)
        rows.append({'radius':r,'mse':sum(errors)/len(errors) if errors else None,'count':len(errors)})
    valid=[x for x in rows if x['mse'] is not None]
    chosen=min(valid,key=lambda x:(x['mse'],x['radius']))['radius'] if valid else min(q['radii'])
    return {'chosen_radius':chosen,'scores':rows,'values':[calc(i,chosen) for i in range(len(values))]}

REQUESTS=[[
{'phase':'preview','initial':{'a':4,'b':1},'operations':[{'id':'x','delta':{'a':-5,'b':3}},{'id':'x','delta':{'b':-1,'a':2}},{'id':'z','delta':{'new':0}}]},
{'phase':'summary','initial':{},'capacity':0,'operations':[{'id':'a','delta':{'new':1}},{'id':'b','delta':{'new':0}}]},
{'phase':'preview','initial':{'a':1},'capacity':2,'operations':[{'id':'x','delta':{'a':1}},{'id':'y','delta':{'a':1}},{'id':'z','delta':{'a':-2,'b':2}}]},
{'phase':'summary','initial':{},'operations':[]},
{'phase':'preview','initial':{'a':2},'operations':[{'id':'x','delta':{'a':-1}},{'id':'x','delta':{'a':-1}}]},
], [
{'phase':'smooth','values':[2,None,10,3],'radius':2,'power':2},
{'phase':'calibrate','values':[0,1,None,9,2],'radii':[3,0,2,1],'power':2},
{'phase':'calibrate','values':[4,4,4],'radii':[2,1,0]},
{'phase':'calibrate','values':[None,None],'radii':[3,0,2]},
{'phase':'calibrate','values':[],'radii':[2,1]},
{'phase':'smooth','values':[None,3,None],'radius':0},
{'phase':'calibrate','values':[None,3,None],'radii':[0,2,1]},
]]
results=[]
for idx,(cid,method) in enumerate(CONTRACTS):
    root=ROOT/cid;root.mkdir(parents=True,exist_ok=True)
    mats=root
    (mats/'method.md').write_text(method)
    (mats/'protocol.json').write_text(json.dumps({'entrypoint':'run_experiment(request)','python':'3.9 standard library only'}))
    (mats/'sample.json').write_text(json.dumps(REQUESTS[idx][0]))
    reference=inventory if idx==0 else smoothing
    tests=[{'name':f'heldout-{i}','request':q,'expected':reference(q)} for i,q in enumerate(REQUESTS[idx])]
    (root/'frozen-heldout.json').write_text(json.dumps(tests,indent=2))
    case=CaseContext.from_dict({'case_id':cid,'function':'Reproduction','title':'Self-authored contract validation','input':'Implement the method exactly','materials':[],'constraints':[],'expected_output':'current method internal output','output_schema_ref':'Reproduction'})
    llm=GatewayLLM(token_budget=float('inf'),timeout=1780)
    runner=LayeredDRunner(Settings(root,root/'out'),root/'work',llm)
    begin=time.monotonic()
    try:
        with CaseSession(cid,1780).activate():
            path=runner.prepare_checked_implementation(case)
            initial=run_tests(path.read_text(),tests)
            path=runner.review_implementation(case,path)
            final=run_tests(path.read_text(),tests)
        row={'case':cid,'initial':initial,'final':final,'review':json.loads((root/'work/source-review.json').read_text()),'usage':llm.usage,'seconds':round(time.monotonic()-begin,2)}
    except Exception as exc:row={'case':cid,'error_type':type(exc).__name__,'error':str(exc)[:200],'usage':llm.usage,'seconds':round(time.monotonic()-begin,2)}
    results.append(row);(ROOT/'results.json').write_text(json.dumps(results,indent=2))
    print(json.dumps({'case':cid,'seconds':row['seconds'],'initial_passed':sum(r['passed'] for r in row.get('initial',{}).get('results',[])), 'final_passed':sum(r['passed'] for r in row.get('final',{}).get('results',[])),'tests':len(tests),'review':row.get('review'),'error':row.get('error_type')}),flush=True)
