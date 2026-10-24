"""综述的本篇方法定位；避免高频相关工作挤掉机制定义。"""
import re
from pathlib import PurePath


def own_method_score(chunk):
    text = re.sub(r"\s+", " ", chunk.text)
    patterns = ((r"we (?:propose|introduce|present|refer to it as)", 3),
                (r"our (?:generative )?(?:model|method|approach)|we train our", 2),
                (r"two (?:models|variants|forms)|consists of|is defined as", 2),
                (r"本文提出|本研究提出|我们提出|方法定义", 3))
    score = sum(weight for pattern, weight in patterns if re.search(pattern, text, re.I))
    alias = re.sub(r"^\d{4}\.\d{4,5}[-_]?", "", PurePath(chunk.filename).stem)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,30}", alias):
        if re.search(re.escape(alias) + r".{0,25}(?:trains|enables|consists|generates|uses)", text, re.I):
            score += 3
    if re.search(r"related work|prior work|concurrent|相关工作", text[:160], re.I):
        score -= 4
    if re.search(r"appendix|附录", chunk.heading, re.I):
        score -= 1
    return score


def own_method_chunks(chunks, filename, maximum=2):
    candidates = [c for c in chunks if c.filename == filename and c.kind == "text"
                  and c.page is not None and c.page > 1 and len(c.text.strip()) >= 100]
    ranked = sorted(candidates, key=lambda c: (-own_method_score(c), c.page, c.start))
    return [c for c in ranked if own_method_score(c) >= 2][:maximum]
