"""论文筛选决策与公开结果格式。"""
import re

def compact_retrieval_chunk(chunk):
    """B 类模型只看判断相关性所需的短证据，完整片段仍留在本地索引。"""
    return {"chunk_id": chunk.chunk_id, "page": chunk.page,
            "heading": chunk.heading, "text": chunk.text[:1100]}


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
    """只在题目明确写出论文篇数时启用强匹配数量约束。"""
    number = r"([一二三四五六七八九十]|\d{1,2})"
    # “各一篇，合计三篇”要采用总数，不能把局部数量当成返回上限。
    match = re.search(r"(?:合计|总计|一共|共)\s*" + number + r"\s*篇", question)
    if not match:
        match = re.search(number + r"\s*篇", question)
    if not match:
        return None
    chinese = {char: index for index, char in enumerate("一二三四五六七八九十", start=1)}
    return chinese.get(match.group(1), int(match.group(1)) if match.group(1).isdigit() else None)
