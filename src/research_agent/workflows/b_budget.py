"""B-only formal time budget; leave all other categories unchanged."""
import os


def retrieval_timeout():
    return max(30, min(540, int(os.environ.get("RESEARCH_B_CASE_TIMEOUT", "540"))))
