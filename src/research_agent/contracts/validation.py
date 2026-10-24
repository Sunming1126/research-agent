from __future__ import annotations

import math
import re
from pathlib import Path

from ..core.utils import confined, sha256_file
from ..integrations.instrumentation import tool


def _only(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("output fields do not match schema")


def _string(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    return type(value) in {int, float} and math.isfinite(value)


def validate_wire_fields(case, result):
    """纯函数：检查最终 HTTP 答卷，不依赖已经结束的 SDK 工具上下文。"""
    fields = {
        "Chat": ("case_id", "answer", "clarifying_question"),
        "Literature_Retrieval": ("case_id", "papers"),
        "Survey": ("case_id", "path", "markdown"),
        "Idea": ("case_id", "path", "markdown"),
        "Reproduction": ("case_id", "status", "metrics", "figures"),
    }
    _only(result, fields[case.function])
    if result["case_id"] != case.case_id:
        raise ValueError("case_id mismatch")
    return result


def report_citations(body):
    # [111] 等晶向不算文献；支持 [1,2]、[1, 2] 和内部证据页码标注。
    citations = []
    for match in re.finditer(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)(?:,\s*p\.\s*\d+)?\]", body):
        citations.extend(re.split(r"\s*,\s*", match.group(1)))
    return citations


def validate_report_limits(markdown, function):
    body = markdown.split("## 参考文献", 1)[0]
    limit = 4500 if function == "Survey" else 6000
    if len(body) > limit:
        raise ValueError(f"report body exceeds {limit} characters (including whitespace)")
    if function == "Survey" and len(report_citations(body)) > 40:
        raise ValueError("Survey body exceeds 40 citation instances")


@tool(name="validate_case_artifacts")
def validate_result(case, result, output_root=None, *, team="workspace_team", filenames=None, specs=None, proof=None, readback=False):
    """按题型核验字段、授权引用和产物；readback 时再核对磁盘内容。"""
    validate_wire_fields(case, result)
    if case.function == "Chat":
        _only(result, ["case_id", "answer", "clarifying_question"])
        if not all(_string(result[x]) for x in ("answer", "clarifying_question")):
            raise ValueError("empty Chat fields")
    elif case.function == "Literature_Retrieval":
        _only(result, ["case_id", "papers"])
        if not isinstance(result["papers"], list):
            raise ValueError("papers must be an array")
        seen = set()
        for paper in result["papers"]:
            _only(paper, ["filename", "reason"])
            name = paper["filename"]
            if not _string(name) or not name.lower().endswith(".pdf") or not _string(paper["reason"]):
                raise ValueError("invalid paper")
            if name.casefold() in seen or filenames is not None and name not in filenames:
                raise ValueError("duplicate/unauthorized paper filename")
            seen.add(name.casefold())
    elif case.function in {"Survey", "Idea"}:
        _only(result, ["case_id", "path", "markdown"])
        kind = "综述" if case.function == "Survey" else "灵感Idea报告"
        expected = f"data/expert/{case.case_id}-{kind}-{team}.md"
        if result["path"] != expected:
            raise ValueError("invalid report path")
        markdown = result["markdown"]
        if not isinstance(markdown, str) or markdown.count("## 参考文献") != 1:
            raise ValueError("report requires one references section")
        body, references = markdown.split("## 参考文献")
        validate_report_limits(markdown, case.function)
        if len(re.sub(r"\s", "", body)) < 800:
            raise ValueError("report body shorter than 800 characters")
        rows = re.findall(r"^\[(\d+)\]\s+(.+\.pdf)\s*$", references, re.M)
        if len({name for _, name in rows}) < (5 if case.function == "Survey" else 1):
            raise ValueError("insufficient PDF references")
        refmap = dict(rows)
        if len(refmap) != len(rows) or len(set(refmap.values())) != len(rows):
            raise ValueError("duplicate references")
        if filenames is not None and not set(refmap.values()) <= set(filenames):
            raise ValueError("unauthorized reference")
        # 科学正文里的晶向 [111] 等三位方括号数字不属于论文编号。
        citations = report_citations(body)
        if set(citations) != set(refmap):
            raise ValueError("body citations and references differ")
        if readback:
            if confined(output_root, expected).read_text(encoding="utf-8") != markdown:
                raise ValueError("JSON/Markdown readback mismatch")
    elif case.function == "Reproduction":
        _only(result, ["case_id", "status", "metrics", "figures"])
        # 对外仍只返回 Schema 约定的 completed；平台 runner 的 complete
        # 在实验执行器中已归一化，这里拒绝未经归一化的原始回执。
        if result["status"] not in {"completed", "failed", "materials_missing"}:
            raise ValueError("invalid experiment status")
        metrics = result["metrics"]
        if not isinstance(metrics, list) or len(metrics) != 3:
            raise ValueError("experiment requires exactly three metrics")
        for metric in metrics:
            _only(metric, ["name", "unit", "reference", "observed", "relative_error"])
            if not _string(metric["name"]) or not _string(metric["unit"]):
                raise ValueError("invalid metric name/unit")
            for key in ("reference", "observed", "relative_error"):
                if metric[key] is not None and not _number(metric[key]):
                    raise ValueError("metric must be a finite JSON number or null")
            if result["status"] == "completed" and metric["observed"] is None:
                raise ValueError("completed experiment has unknown observations")
            if result["status"] != "completed" and metric["observed"] is not None:
                raise ValueError("failed experiment must not claim measurements")
            if (metric["reference"] is None or metric["observed"] is None) and metric["relative_error"] is not None:
                raise ValueError("unverifiable relative error")
        if len({m["name"] for m in metrics}) != 3:
            raise ValueError("metric names must be unique")
        if specs is not None and [(m["name"],m["unit"]) for m in metrics] != specs:
            raise ValueError("metric contract differs from input")
        if not isinstance(result["figures"], list):
            raise ValueError("figures must be an array")
        if result["status"] == "completed":
            # completed 必须与当前运行证据完全对应，不能提交历史实验结果。
            if not proof or proof.get("submission") != result or proof.get("case_id") != case.case_id:
                raise ValueError("completed experiment lacks current-run proof")
            if not result["figures"]:
                raise ValueError("completed experiment has no figure")
        for figure in result["figures"]:
            _only(figure, ["path", "caption", "x_label", "y_label", "legend"])
            if not all(_string(figure[key]) for key in figure):
                raise ValueError("empty figure metadata")
            if not figure["path"].startswith(f"data/figure/{team}/"):
                raise ValueError("figure outside team directory")
            if output_root:
                path = confined(output_root, figure["path"])
                from PIL import Image
                with Image.open(path) as image:
                    image.verify()
                if proof and sha256_file(path) != proof["figure_hashes"].get(figure["path"]):
                    raise ValueError("figure modified after run")
    else:
        raise ValueError("unsupported task function")
    return result

