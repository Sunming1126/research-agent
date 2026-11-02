"""Formal per-question ceilings; explicit smaller local budgets still win."""

CASE_TIME_LIMITS = {'Chat': 540, 'Literature_Retrieval': 540,
                    'Survey': 900, 'Idea': 900, 'Reproduction': 1800}
DEFAULT_CASE_TIMEOUT = max(CASE_TIME_LIMITS.values())


def wire_timeout(function, configured):
    # Invalid/unvalidated payloads keep the prior conservative bound until
    # CaseContext reports their real schema error. Never infer content by ID.
    return min(configured, CASE_TIME_LIMITS.get(function, 180))
