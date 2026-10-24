"""对当前授权文件可识别的方法简称做归因交叉检查，不预置论文名单。"""
import re
from pathlib import PurePath

from . import EvidenceError


def validate_named_attribution(markdown, evidence, used_ids=None):
    names = {}
    ambiguous = set()
    for row in evidence:
        filename = row.get("filename", "")
        alias = re.sub(r"^\d{4}\.\d{4,5}[-_]?", "", PurePath(filename).stem)
        if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,30}", alias)
                or sum(c.isupper() for c in alias) < 2):
            continue
        key = alias.casefold()
        if key in names and names[key][1] != row["ref"]:
            ambiguous.add(key)
        names[key] = (alias, row["ref"])
    for key in ambiguous:
        names.pop(key, None)
    for paragraph in markdown.split("\n\n"):
        for sentence in re.split(r"(?<=[。！？])\s*(?!\s*\[)", paragraph):
            plain = re.sub(r"[*#>`]", "", sentence.strip())
            if (re.match(r"^(?:建议|待验证|拟议|本方案建议|我们建议)", plain)
                    and not re.search(r"论文|作者|原文|报告|实测|发现", plain)):
                continue
            cited = {int(n) for n in re.findall(r"\[(\d{1,2})(?:,\s*p\.\s*\d+)?\]", sentence)}
            pairs = {(int(n), int(p) if p else None) for n, p in
                     re.findall(r"\[(\d{1,2})(?:,\s*p\.\s*(\d+))?\]", sentence)}
            if not cited:
                continue
            for alias, owner in names.values():
                named = r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])"
                if owner in cited or not re.search(named + r".{0,18}(?:报告|实测|采用|使用|提出|阅读器|结果|证明|发现)", sentence, re.I):
                    continue
                supported = " ".join(str(row.get("claim", "")) + " " + str(row.get("quote", ""))
                                     for row in evidence if ((row["ref"], row["page"]) in pairs or (row["ref"], None) in pairs)
                                     and (used_ids is None or row["evidence_id"] in used_ids))
                if not re.search(named, supported, re.I):
                    raise EvidenceError(f"named method {alias} is attributed to another paper without supporting evidence",
                                        passages=[sentence])
