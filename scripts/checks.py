"""运行离线回归，并保存精简的机器可读摘要。"""
import json
import os
import sys
import unittest
from pathlib import Path

os.environ["RESEARCH_RUNTIME"] = "local"
sys.path.insert(0, "/src/tests")
suite = unittest.defaultTestLoader.discover("/src/tests")
result = unittest.TextTestRunner(verbosity=2).run(suite)
summary = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
           "skipped": len(result.skipped), "successful": result.wasSuccessful(),
           "environment": {"network": "none", "cpus": 1, "memory": "1024m"},
           "model": "fake for offline tests; real model checked separately",
           "experiments": "actual public D-01 through D-05"}
Path("/results/checks.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
raise SystemExit(0 if result.wasSuccessful() else 1)

