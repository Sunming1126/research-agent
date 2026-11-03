"""E-only formal execution budget."""
import os

def idea_timeout():
    try:value=int(os.environ.get('RESEARCH_E_CASE_TIMEOUT','900'))
    except ValueError:value=900
    return max(30,min(900,value))
