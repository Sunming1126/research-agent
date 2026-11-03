"""D-only review parsing and evidence binding to current runtime materials."""
import json
import re


def response_json(response):
    choices=response.get('choices',[]) if isinstance(response,dict) else []
    if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict) or choices[0].get('finish_reason')!='stop':
        raise ValueError('incomplete_review_response')
    message=choices[0].get('message',{})
    if not isinstance(message,dict) or message.get('role')!='assistant' or not isinstance(message.get('content'),str):
        raise ValueError('invalid_review_message')
    text=message['content'].strip()
    fenced=re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```",text,re.S|re.I)
    if fenced:text=fenced.group(1)
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('review_not_object')
    return value


def bound_quote(quote, text):
    # 仅恢复空白排版差异，仍返回实际原文；不做模糊或语义匹配。
    if not isinstance(quote,str) or not quote.strip():return None
    if quote in text:return quote
    chars=[];positions=[]
    for i,c in enumerate(text):
        if not c.isspace():chars.append(c);positions.append(i)
    needle=''.join(quote.split());offset=''.join(chars).find(needle)
    if offset<0:return None
    return text[positions[offset]:positions[offset+len(needle)-1]+1]


def material_segments(materials):
    """当轮材料的可回溯位置索引；编号只定位原文，不证明模型结论正确。"""
    spans={}
    for name,text in materials.items():
        for i,start in enumerate(range(0,len(text),500),1):
            spans[f'{name}:{i}']=text[start:start+500]
    return spans


def material_reference(row,materials):
    if 'material_id' in row:
        key=row['material_id']
        return material_segments(materials).get(key) if isinstance(key,str) else None
    quote=row.get('material_quote')
    if not isinstance(quote,str) or not 12<=len(quote.strip())<=2000:return None
    return next((q for text in materials.values() if (q:=bound_quote(quote,text)) is not None),None)


def review_prompt(materials, source, experiments=None):
    return (
        "审查本轮完整实现是否违反当前材料。不要重写代码，不要提出风格/性能偏好，不得假设旧题算法。"
        "逐项核对公式、状态更新、阶段分支、字段与类型、单位、默认值、并列次序、空输入及终止条件。run_experiment接收已解析的Python字典并返回Python对象，不能把参数当JSON字符串再json.loads或把返回值json.dumps。"
        "先按材料手工推演小输入，再与代码逐步比较，不能因为代码表面完整就判为正确。"
        "local_tests是本轮模型提出的假设测试，不是裁判答案：先检查输入是否合法、期望值是否严格由材料推出。"
        "不要为了通过错误测试而修改正确算法。只能报告材料明确要求且源码实际违反的问题。"
        "只返回JSON对象 {decision:keep或repair,defects:数组}。无确切错误用keep和空数组。"
        "最多3个defects，每项含material_id（选择material_segments中实际违反的规则编号）、source_quote（源码逐字摘录）、"
        "explanation（为什么冲突）、counterexample（符合输入约束的最小输入及应有行为推演）。"
        "所有字段均为字符串。禁止把格式样例当数值答案，禁止导出材料。\n"
        + json.dumps({'material_segments':material_segments(materials),'source':source,'local_tests':experiments},ensure_ascii=False)
    )


def review_result(response, materials, source):
    try:value=response_json(response)
    except (ValueError,TypeError):return {'state':'invalid_response','defects':[],'rejected':0}
    if value.get('decision')=='keep' and value.get('defects')==[]:
        return {'state':'keep','defects':[],'rejected':0}
    defects=value.get('defects')
    if value.get('decision')!='repair' or not isinstance(defects,list) or not 1<=len(defects)<=3:
        return {'state':'invalid_response','defects':[],'rejected':0}
    verified=[];rejected=0
    for row in defects:
        keys=('source_quote','explanation','counterexample')
        if not isinstance(row,dict) or any(not isinstance(row.get(k),str) or not row[k].strip() or len(row[k])>2000 for k in keys):
            rejected+=1;continue
        sq=row['source_quote']
        material=material_reference(row,materials)
        code=bound_quote(sq,source)
        if len(sq.strip())<2 or material is None or code is None:
            rejected+=1;continue
        verified.append({**{k:row[k] for k in keys},'material_quote':material,'source_quote':code})
    return {'state':'repair' if verified else 'ungrounded','defects':verified,'rejected':rejected}


def grounded_defects(response, materials, source):
    return review_result(response,materials,source)['defects']


def review_retry_reason(result, execution):
    """矛盾或不可定位的审查不能当作通过；仅触发一次核对，不强迫修改算法。"""
    if result['state'] in ('invalid_response','ungrounded'):
        return result['state']
    if result['state']=='keep' and execution.get('state')=='tested' and any(
            not r.get('passed',False) for r in execution.get('results',[])):
        return 'keep_despite_test_failure'
    return None
