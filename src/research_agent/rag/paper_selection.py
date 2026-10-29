"""论文筛选决策与公开结果格式。"""
import re

def compact_retrieval_chunk(chunk, queries=()):
    """B 类模型只看判断相关性所需的短证据，完整片段仍留在本地索引。"""
    from .evidence_selection import query_window
    return {"chunk_id": chunk.chunk_id, "page": chunk.page,
            "heading": chunk.heading, "text": query_window(chunk.text, queries)}


def valid_paper_approval(item, allowed):
    """模型批准的论文必须对应当前批次真实片段，修复重审也按同一规则。"""
    ids = item.get("chunk_ids", [])
    return (item.get("relevant") is not False
            and item.get("decision", "include") == "include"
            and isinstance(ids, list) and bool(ids) and all(isinstance(x, str) for x in ids)
            and set(ids) <= allowed and isinstance(item.get("reason"), str)
            and bool(item["reason"].strip()))


def explicitly_excluded_paper(paper, exclusions):
    """只处理明确否定结论或文件名中的具名排除项，不因正文提及排除词误删。"""
    reason = paper.get("reason", "")
    # 先去掉否定排除的短语，再检查真正的排除结论；保留其后独立的否定判断。
    reason = re.sub(r"\b(?:not|never)\s+(?:explicitly\s+)?excluded\b|"
                    r"(?:并非|不是|不属于|未被|没有被|不在|并不在).{0,8}(?:明确排除项|明确排除的|排除范围|题目排除项)|"
                    r"(?:不应|无需|不予|不可|不能)排除", "", reason, flags=re.I)
    if re.search(r"(?:属于|是|为|列为|列入|在).{0,12}(?:明确排除项|明确排除的|题目排除)|"
                 r"(?:题目|用户|要求).{0,5}明确排除|不符合(?:本题|题目|检索)要求|"
                 r"(?:按|根据)(?:本题|题目|要求|用户).{0,6}排除|explicitly excluded", reason, re.I):
        return True
    stem = re.sub(r"^\d{4}\.\d{4,5}[-_]", "", paper["filename"].removesuffix(".pdf"))
    for term in exclusions:
        # 长句描述留给语义判断，仅把独立英文方法专名作为文件名排除规则。
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{2,30}", term.strip()):
            if stem.casefold() == term.strip().casefold():
                return True
    return False


def requested_paper_count(question):
    """只把明确的全局总数/上限当截断条件，不把同一篇、局部数或下限当上限。"""
    digits = {char: index for index, char in enumerate('一二三四五六七八九', 1)}
    digits['两'] = 2
    digits['零'] = 0

    def parse(value):
        if value.isdigit():
            return int(value)
        if value in digits:
            return digits[value]
        if value.count('十') == 1:
            left, right = value.split('十')
            if (not left or left in digits) and (not right or right in digits):
                return (digits[left] if left else 1) * 10 + (digits[right] if right else 0)
        return None

    choices = []
    for match in re.finditer(r'(?<!\d)(\d{1,3}|[零一二三四五六七八九十两]{1,3})\s*篇', question):
        before, after = question[max(0, match.start()-28):match.start()], question[match.end():match.end()+10]
        value = parse(match.group(1))
        if value is None:
            continue
        aggregate = bool(re.search(r'(?:合计|总计|一共|总共|共)\s*$', before))
        if not aggregate:
            if re.search(r'(?:同|每|另|某|任意|第)\s*$', before):
                continue
            if re.search(r'(?:至少|最少|不少于|不低于|(?<!不)多于|(?<!不)超过|不止)\s*$', before) or re.match(r'\s*(?:以上|或更多|及以上)', after):
                continue
            if re.search(r'(?:每(?:个|一)?|各).{0,12}(?:方向|主题|目标|领域|类别|问题).{0,8}$', before) or re.search(r'(?:各|分别)(?:找|选|取|推荐|返回)?\s*$', before):
                continue
        upper = bool(re.search(r'(?:最多|至多|不超过|上限(?:为)?|限(?:定)?|恰好|仅(?:需|要)?|只(?:需|要)?)\s*$', before))
        requested = bool(re.search(r'(?:查找|寻找|检索|找出|找|返回|列出|给出|给我|推荐|选择|选取|筛选|提供|需要|要求|收集)\s*$', before))
        if aggregate or upper or requested or not before.strip(' 请，,：:'):
            choices.append((3 if aggregate else 2 if upper else 1, -match.start(), value))
    words = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
    tens = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10)))
    units = '|'.join(list(words)[1:10])
    number = r'(?:\d{1,3}|' + '|'.join(words) + '|' + '|'.join(tens) + r')(?:[- ](?:' + units + '))?'
    for match in re.finditer(r'\b(' + number + r')\s+(?:papers?|articles?|studies|publications?)\b', question, re.I):
        before = question[max(0, match.start()-70):match.start()].lower()
        after = question[match.end():match.end()+35].lower()
        raw = match.group(1).lower()
        parts = re.split(r'[- ]', raw)
        if raw.isdigit():
            value = int(raw)
        elif len(parts) == 1:
            value = words.get(raw, tens.get(raw))
        elif len(parts) == 2 and parts[0] in tens and parts[1] in words and 1 <= words[parts[1]] <= 9:
            value = tens[parts[0]] + words[parts[1]]
        else:
            continue
        if value is None:
            continue
        aggregate = bool(re.search(r'\b(?:overall|in total|altogether|total(?: of)?)\b[^.;!?]*$', before))
        upper = bool(re.search(r'\b(?:at most|no more than|not more than|up to|maximum(?: of)?)\s*$', before))
        exact = bool(re.search(r'\b(?:exactly|only)\s*$', before))
        if not aggregate and (re.match(r'\s*(?:per\b|for each\b|for every\b|in each\b)', after)
                              or re.search(r'\b(?:each|every)\b[^.;!?]*$', before)):
            continue
        if not upper and (re.search(r'\b(?:at least|no fewer than|not fewer than|minimum(?: of)?|more than)\s*$', before)
                          or re.match(r'\s*(?:or more\b|and above\b)', after)):
            continue
        requested = bool(re.search(r'\b(?:find|return|select|recommend|retrieve|give(?: me)?|list|identify)\s*(?:the\s+|top\s+)?$', before))
        if aggregate or upper or exact or requested or not before.strip():
            choices.append((3 if aggregate or exact else 2 if upper else 1, -match.start(), value))
    if not choices:
        return None
    # 明确上限取最紧的约束；不能因另一处总数更大而忽略“最多”。
    hard = [value for priority, _, value in choices if priority >= 2]
    return min(hard) if hard else max(choices)[2]
