"""保留平台错误码用于定位问题；最终答卷仍严格使用赛事字段。"""
from __future__ import annotations

import re


class ExperimentProcessError(RuntimeError):
    def __init__(self, returncode, stderr):
        self.returncode = returncode
        self.code = next(iter(re.findall(r"\bD_SOURCE_[A-Z_]+\b", stderr)), None)
        super().__init__("experiment client failed" + (": " + self.code if self.code else ""))


def error_record(exc, stage):
    code = getattr(exc, "code", None)
    category = ("model_quota" if code == "ACCOUNT_QUOTA" else
                "platform_infrastructure" if code == "D_SOURCE_AUDIT_UNAVAILABLE" else
                "source_admission" if code else
                "timeout_or_cancelled" if isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutExpired" else
                "generation_or_validation" if stage in {"generation", "public_selftest"} else
                "execution_or_artifacts")
    return {"type": type(exc).__name__, "message": str(exc)[:600], "stage": stage,
            "code": code, "category": category, "returncode": getattr(exc, "returncode", None),
            "retryable": False if code == "ACCOUNT_QUOTA" else None}
