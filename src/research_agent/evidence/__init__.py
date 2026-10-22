"""可回查证据与报告约束。"""

class EvidenceError(ValueError):
    def __init__(self, message, *, passages=None):
        super().__init__(message)
        self.passages = passages or []
