import asyncio
import json
import math
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

os.environ["RESEARCH_RUNTIME"] = "local"

from fixtures import FakeLLM, case, papers, METRICS
from research_agent.core.config import Settings
from research_agent.core.engine import ResearchAgentEngine
from research_agent.core.models import CaseContext, MaterialRef
from research_agent.core.utils import sha256_file, write_json, read_json, confined
from research_agent.core.dispatch import load_dispatch
from research_agent.rag.materials import MaterialResolver, DocumentIndex, MaterialError, Chunker
from research_agent.rag.retrieval import HybridRetriever
from research_agent.workflows.tasks import validate_citations, EvidenceError
from research_agent.workflows.experiments import parse_metric_specs, failed_result, ExperimentRunner
from research_agent.contracts.validation import validate_result
from research_agent.integrations.llm import GatewayLLM, ModelError


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.names = papers(self.root/"papers")
        self.s = Settings(self.root, self.root/"out", "test", [self.root/"papers"])
        self.engine = ResearchAgentEngine(self.s, FakeLLM)

    def tearDown(self):
        self.temp.cleanup()

    def test_real_pdf_chunk_retrieve_and_cache(self):
        index = DocumentIndex(self.s, self.root/"out/cache")
        chunks = index.build(self.names)
        self.assertTrue(chunks)
        self.assertEqual({c.page for c in chunks}, {1,2})
        hits = HybridRetriever(chunks).search("城市绿地 温度 遥感")
        self.assertTrue(hits)
        self.assertEqual(chunks, index.build(self.names))
        self.assertTrue(all(c.document_sha256 for c in chunks))

    def test_no_fallback_to_other_papers(self):
        with self.assertRaises(MaterialError):
            MaterialResolver(self.s).resolve(["Missing.pdf"])

    def test_path_traversal_and_unknown_prose(self):
        for value in ["../papers/Study_1.pdf", "/etc/passwd", "C:/secret.pdf", "不明确的指定论文 PDF"]:
            with self.assertRaises(ValueError):
                MaterialResolver(self.s).resolve([value])
        with self.assertRaises(ValueError):
            Settings(self.root,self.root/"out","../other")

    def test_alias_limits_material_set(self):
        self.s.material_aliases = {"指定论文": [self.names[0]]}
        selected = MaterialResolver(self.s).resolve(["指定论文"])
        self.assertEqual([r.filename for r in selected], [self.names[0]])

    def test_cancellation_prevents_submission(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(TimeoutError):
            self.engine.solve_sync(case("A-01"), cancelled)
        self.assertFalse((self.s.output/"data/test/A-01.json").exists())

    def test_failed_retry_does_not_leave_stale_result(self):
        self.engine.solve_sync(case("A-01"))
        class FailedLLM:
            def json(self, *args, **kwargs):
                raise ModelError("simulated outage")
        self.engine.llm_factory = FailedLLM
        with self.assertRaises(ModelError):
            self.engine.solve_sync(case("A-01"))
        self.assertFalse((self.s.output/"data/test/A-01.json").exists())
        self.assertTrue(list((self.s.output/"data/test/work/A-01").rglob("previous-result.json")))

    def test_platform_without_active_session_fails_honestly(self):
        with patch.dict(os.environ, {"RESEARCH_EXPERIMENT_CASE": "D-02"}):
            result = self.engine.solve_sync(case("D-01"))
        self.assertEqual(result["status"], "materials_missing")

    def test_metric_identity_not_relabelled(self):
        exp = Path(os.environ.get("PUBLIC_EXPERIMENT_ROOT","/materials"))
        if not exp.is_dir():
            self.skipTest("public experiment mount not configured")
        self.s.experiment_mode = "local"
        self.s.local_experiment_root = exp
        payload = case("D-01")
        payload["input"] = payload["input"].replace("Macro-F1", "MAE")
        result = self.engine.solve_sync(payload)
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["metrics"][0]["observed"])

    def test_wrong_team_figure_and_extra_fields(self):
        payload = CaseContext.from_dict(case("D-01"))
        result = failed_result("D-01",METRICS["D-01"],"failed")
        result["figures"] = [{"path":"data/figure/other/D-01.png","caption":"x","x_label":"x","y_label":"y","legend":"l"}]
        with self.assertRaises(ValueError):
            validate_result(payload,result,team="test")
        result["figures"] = []
        result["error"] = "not allowed"
        with self.assertRaises(ValueError):
            validate_result(payload,result,team="test")

    def test_symlink_escape(self):
        (self.root/"secret.pdf").write_bytes(b"secret")
        try:
            (self.root/"papers/link.pdf").symlink_to(self.root/"secret.pdf")
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege is unavailable")
            raise
        with self.assertRaises(ValueError):
            MaterialResolver(self.s).resolve(["link.pdf"])

    def test_ambiguous_duplicate_name(self):
        (self.root/"papers/sub").mkdir()
        (self.root/"papers/sub/Study_1.pdf").write_bytes((self.root/"papers/Study_1.pdf").read_bytes())
        with self.assertRaises(MaterialError):
            MaterialResolver(self.s).resolve(["Study_1.pdf"])

    def test_case_insensitive_duplicate_selection(self):
        (self.root/"papers/study_1.pdf").write_bytes((self.root/"papers/Study_1.pdf").read_bytes())
        if (self.root/"papers/study_1.pdf").samefile(self.root/"papers/Study_1.pdf"):
            self.skipTest("case-insensitive filesystem cannot hold both filenames")
        with self.assertRaises(MaterialError):
            MaterialResolver(self.s).resolve(["Study_1.pdf","study_1.pdf"])

    def test_empty_and_invalid_pdf(self):
        (self.root/"papers/bad.pdf").write_text("Not a PDF")
        with self.assertRaises(MaterialError):
            DocumentIndex(self.s,self.root/"out/cache").build(["bad.pdf"])

    def test_scanned_pdf_requires_ocr(self):
        import fitz
        from PIL import Image
        picture = self.root/"image.png"
        Image.new("RGB",(100,100),"white").save(picture)
        doc = fitz.open()
        page = doc.new_page()
        page.insert_image(page.rect, filename=str(picture))
        doc.save(self.root/"papers/scan.pdf")
        doc.close()
        with self.assertRaises(MaterialError):
            DocumentIndex(self.s,self.root/"out/cache").build(["scan.pdf"])

    def test_long_paragraph_really_splits(self):
        ref = MaterialRef(Path("x.pdf"),"x.pdf","pdf","x"*64)
        text = "城市遥感观测植被变化与区域温度。" * 200
        chunks = Chunker().chunk(ref, [(1,text,"")])
        self.assertGreater(len(chunks),2)
        self.assertTrue(all(len(c.text) <= 850 for c in chunks))
        self.assertTrue(all(c.end > c.start for c in chunks))

    def test_schema_and_case_validation(self):
        wrong = case("A-01")
        wrong["case_id"] = "A-../secret"
        with self.assertRaises(ValueError):
            CaseContext.from_dict(wrong)
        valid = CaseContext.from_dict(case("A-100"))
        with self.assertRaises(ValueError):
            validate_result(valid, {"case_id":"A-01","answer":"x","clarifying_question":"?"})

    def test_no_gateway_no_fake_answer(self):
        with patch.dict(os.environ, {"PLATFORM_API_KEY":"","PLATFORM_LLM_URL":"","AGENT_MODEL_NAME":""}):
            with self.assertRaises(ModelError):
                GatewayLLM().json("chat", {})

    def test_handlers_and_report_readback(self):
        for letter in "ABCE":
            payload = case(letter+"-01", self.names if letter != "A" else [])
            result = self.engine.solve_sync(payload)
            target = self.s.output/f"data/test/{letter}-01.json"
            self.assertEqual(result,read_json(target))
            if letter in "CE":
                self.assertEqual(result["markdown"], (self.s.output/result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(read_json(self.s.output/"data/test/B-01.json")["papers"]),5)

    def test_no_relevant_results(self):
        index = DocumentIndex(self.s,self.root/"out/cache")
        hits = HybridRetriever(index.build(self.names)).search("zqxwvkj")
        self.assertEqual(hits,[])

    def test_fake_page_and_missing_citation_rejected(self):
        ledger = [{"evidence_id":"ev-1","ref":1,"page":1}]
        with self.assertRaises(EvidenceError):
            validate_citations("内容"*500+"[1, p.99]",ledger,["ev-1"])
        with self.assertRaises(EvidenceError):
            validate_citations("内容"*500,ledger,["ev-1"])

    def test_three_metrics_and_finite_values(self):
        payload = case("D-01")
        specs = parse_metric_specs(payload["input"])
        self.assertEqual(specs,METRICS["D-01"])
        failed = failed_result("D-01",specs,"failed")
        failed["metrics"][0]["observed"] = float("nan")
        with self.assertRaises(ValueError):
            validate_result(CaseContext.from_dict(payload),failed)
        with self.assertRaises(ValueError):
            parse_metric_specs("unknown three metrics")

    def test_failure_does_not_borrow_other_metrics(self):
        s = Settings(self.root,self.root/"out","test", experiment_mode="local")
        result = ResearchAgentEngine(s,FakeLLM).solve_sync(case("D-01"))
        self.assertEqual(result["status"],"materials_missing")
        self.assertTrue(all(m["observed"] is None for m in result["metrics"]))

    def test_timeout_maps_to_failed(self):
        exp = Path(os.environ.get("PUBLIC_EXPERIMENT_ROOT","/materials"))
        if not exp.is_dir():
            self.skipTest("public experiment mount not configured")
        self.s.experiment_mode = "local"
        self.s.local_experiment_root = exp
        import subprocess
        with patch("research_agent.workflows.experiments.subprocess.run", side_effect=subprocess.TimeoutExpired("experiment",1)):
            result = self.engine.solve_sync(case("D-01"))
        self.assertEqual(result["status"],"failed")

    def test_http_health_solve_and_sdk_adapter(self):
        from agent import ResearchAgent
        from http_fixture import serve_local
        server = serve_local(self.engine, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            self.assertEqual(json.load(urllib.request.urlopen(base+"/health"))["status"],"healthy")
            payload = {"task_id":"test-http","input":case("A-01")}
            req = urllib.request.Request(base+"/solve", json.dumps(payload).encode(), {"Content-Type":"application/json"})
            answer = json.load(urllib.request.urlopen(req))["output"]["answer"]
            self.assertEqual(json.loads(answer)["case_id"],"A-01")
            sdk_answer = asyncio.run(ResearchAgent(self.engine).solve(case("A-02")))
            self.assertEqual(json.loads(sdk_answer["answer"])["case_id"],"A-02")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def make_dispatch(self):
        ids, entries = [], []
        for letter in "ABCDE":
            for number in range(1,6):
                ident = f"{letter}-{number:02d}"
                payload = case(ident,self.names if letter in "BCE" else [])
                relative = f"data/test/assigned/{ident}.json"
                write_json(self.root/relative,payload)
                ids.append(ident)
                entries.append({"case_id":ident,"function":payload["function"],"title":payload["title"],
                                "question_file":relative,"question_sha256":sha256_file(self.root/relative)})
        dispatch = {"team":"test","schema_version":2,"cases_per_category":5,
                    "rules_version":"research-assistant-objective-v2","case_ids":ids,"cases":entries}
        write_json(self.root/"data/test/dispatch.json",dispatch)
        return dispatch

    def test_dispatch_hash_and_ownership(self):
        self.make_dispatch()
        self.assertEqual(len(load_dispatch(self.s)),25)
        (self.root/"data/test/assigned/A-01.json").write_text("{}")
        with self.assertRaises(ValueError):
            load_dispatch(self.s)

    def test_full_25_case_batch_with_real_d(self):
        exp = Path(os.environ.get("PUBLIC_EXPERIMENT_ROOT","/materials"))
        if not exp.is_dir():
            self.skipTest("public experiment mount not configured")
        self.make_dispatch()
        self.s.experiment_mode="local"
        self.s.local_experiment_root=exp
        summary=self.engine.solve_sync({"dispatch":True})
        self.assertTrue(summary["complete"],summary)
        self.assertEqual(self.engine.precheck(),{"cases":25,"valid":True})
        for number in range(1,6):
            result=read_json(self.s.output/f"data/test/D-{number:02d}.json")
            self.assertEqual(result["status"],"completed",result)
            self.assertEqual(len(result["metrics"]),3)
            self.assertTrue((self.s.output/result["figures"][0]["path"]).is_file())
        # 确认回读校验能够发现 Markdown 被改动。
        report=self.s.output/"data/expert/C-01-综述-test.md"
        report.write_text(report.read_text(encoding="utf-8")+"tampered", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.engine.precheck()


if __name__ == "__main__":
    unittest.main()

