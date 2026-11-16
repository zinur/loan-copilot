"""Режим workflow: HTTP замокано, перевіряємо розбір вердикту та гейт."""
from module_4_production.src import run_eval
from module_4_production.src.workflow_client import CodeMieWorkflowClient, parse_verdict


def test_parse_verdict_json_and_keywords():
    assert parse_verdict('{"decision": "Reject", "reason": "x"}') == "reject"
    assert parse_verdict("Рекомендую схвалити, ескалація не потрібна") == "hitl_escalate"  # ключові слова грубі — тому JSON краще
    assert parse_verdict("нічого") is None


class FakeClient:
    def __init__(self, verdict):
        self.verdict = verdict

    def run(self, q):
        return {"status": "Succeeded", "output": '{"decision": "%s"}' % self.verdict, "execution_id": "e1"}


def test_workflow_gate_blocks_wrong_verdicts(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval, "_retrieve_context", lambda q, top_k=4: [])
    monkeypatch.setattr(run_eval, "save_eval_run_artifact", lambda r, s: tmp_path / "x.jsonl")
    s = run_eval.run_workflow_eval(client=FakeClient("approve"), limit=20)
    assert s.total_cases == 20 and s.ci_passed is False  # approve збігається лише з 8/20


def test_client_runs_with_mocked_http(monkeypatch):
    class R:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data

    class S:
        def post(self, url, **kw): return R({"execution_id": "e1"})
        def get(self, url, **kw):
            if url.endswith("/states"): return R([{"id": "s1", "completed_at": "1"}])
            if url.endswith("/output"): return R({"output": '{"decision":"approve"}'})
            return R({"overall_status": "Succeeded"})

    monkeypatch.setenv("CODEMIE_TOKEN", "t")
    c = CodeMieWorkflowClient("http://h", "wf", session=S(), poll_interval=0)
    assert c.run("q")["output"] == '{"decision": "approve"}'


def test_local_backend_runs_golden_set_with_fake_llm(monkeypatch, tmp_path):
    """Режим local: LLM підмінено — перевіряємо, що весь контур (пошук → JSON → гейт) працює без CodeMie."""
    import json
    from types import SimpleNamespace

    from module_4_production.src.local_backend import LocalPolicyBackend

    def fake_chat(messages, temperature=0.0):
        return SimpleNamespace(content='```json\n{"decision": "reject", "answer": "POL-LOAN-01 §2.1", "citations": []}\n```', provider="fake")

    out = LocalPolicyBackend(chat=fake_chat).run("Мені 19 років, чи можу взяти кредит?")
    assert out["status"] == "Succeeded" and json.loads(out["output"])["decision"] == "reject"

    monkeypatch.setattr(run_eval, "save_eval_run_artifact", lambda r, s: tmp_path / "x.jsonl")
    s = run_eval.run_workflow_eval(client=LocalPolicyBackend(chat=fake_chat), limit=20)
    assert s.total_cases == 20 and s.ci_passed is False  # «reject» збігається лише з частиною кейсів


def test_local_backend_survives_llm_failure():
    from module_4_production.src.local_backend import LocalPolicyBackend

    def boom(*a, **k):
        raise RuntimeError("no key")

    assert LocalPolicyBackend(chat=boom).run("q")["status"] == "Failed"
