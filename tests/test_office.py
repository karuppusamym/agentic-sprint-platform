"""Office setup with no model SDK: VS Code chat handoff, Jira Data Center, connection checks, .env."""
import json
import os

import httpx
from fastapi.testclient import TestClient

from asdp import cli
from asdp.config import AgentsPolicy, GenerationPolicy, JiraPolicy, Policy, Settings, load_env_file
from asdp.gates import current_spec
from asdp.jira import JiraDataCenter
from asdp.main import create_app
from asdp.models import Story
from asdp.prototype import TemplateGenerator
from sqlalchemy import select


def chat_client(h, user="dan"):
    return TestClient(create_app(h.ctx), base_url="http://testserver/api/v1", headers={"X-User": user})


def spec_content(h, key):
    with h.ctx.SessionLocal() as db:
        return current_spec(db, db.execute(select(Story).where(Story.key == key)).scalar()).content


def test_spec_is_deepened_through_vscode_chat_files_without_any_model_api(h, tmp_path):
    h.to_review("WF-142")
    assert h.ctx.generation_status["active"] == "template"
    c, out = chat_client(h), tmp_path / ".asdp"
    path = cli.pull(c, "WF-142", "spec", "copilot_vscode", out)
    assert "READ-ONLY" in path.read_text(encoding="utf-8") and "AC-01" in path.read_text(encoding="utf-8")
    answer = {"business_rules": [{"text": "Export is capped at 10,000 rows", "criterion_id": "AC-01"}], "assumptions": [],
              "nonfunctional": [], "error_cases": [], "open_questions": [], "screens": [],
              "code_impact": [{"path": "services/export/", "change": "add CSV writer", "risk": "low"}]}
    # the chat wrote prose around a fenced block, as chats do
    (out / "WF-142" / "enrichment.json").write_text("Here is the analysis:\n```json\n" + json.dumps(answer) + "\n```\nDone.", encoding="utf-8")
    cli.push(c, "WF-142", "spec", "copilot_vscode", out / "WF-142" / "enrichment.json")
    s = h.story("WF-142")
    assert s["state"] == "HUMAN_REVIEW" and s["spec"]["version"] == 2
    content = spec_content(h, "WF-142")
    assert content["code_impact"][0]["path"] == "services/export/"
    assert any("10,000" in r["text"] and r.get("origin") == "copilot_vscode" for r in content["business_rules"])


def test_prototype_written_in_chat_goes_through_the_same_gate(h, tmp_path):
    h.to_review("WF-142")
    c, out = chat_client(h), tmp_path / ".asdp"
    brief = cli.pull(c, "WF-142", "prototype", "copilot_vscode", out).read_text(encoding="utf-8")
    for needle in ('data-testid="nav-AC-01"', 'data-screen="sc-AC-01"', "data-simulated", "No network", ".asdp/WF-142/prototype.html"):
        assert needle in brief, needle
    good, _ = TemplateGenerator().generate(spec_content(h, "WF-142"), 0, [])
    file = out / "WF-142" / "prototype.html"
    file.write_text("```html\n" + good.replace("Scenarios", "Export orders") + "\n```", encoding="utf-8")
    cli.push(c, "WF-142", "prototype", "copilot_vscode", file)
    s = h.story("WF-142")
    assert s["state"] == "HUMAN_REVIEW" and s["prototype"]["version"] == 2 and h.gates("WF-142")["G2"] == "PASS"

    # a chat-written prototype that calls the network is rejected, and the next brief says why
    file.write_text(good.replace("show('home');", "fetch('https://example.com');show('home');"), encoding="utf-8")
    cli.push(c, "WF-142", "prototype", "copilot_vscode", file)
    assert h.story("WF-142")["state"] == "NEEDS_HUMAN_INPUT" and h.gates("WF-142")["G2"] == "FAIL"
    again = h.ok("GET", "/stories/WF-142/prototype-brief?engine=copilot_vscode")["brief"]
    assert "failed these rules" in again and "P-005" in again


def test_task_packages_are_pulled_into_the_repo_for_the_chat(h, tmp_path):
    h.to_plan_ready("WF-142")
    h.ok("POST", "/stories/WF-142/dispatch", "lena")
    folder = cli.pull(chat_client(h), "WF-142", "task", "copilot_vscode", tmp_path / ".asdp")
    files = list(folder.glob("task-*.md"))
    assert files and "WF-142" in files[0].read_text(encoding="utf-8")


def test_brief_refuses_a_disabled_engine(make_harness):
    h = make_harness(Policy(agents=AgentsPolicy(engines={"devin": False, "copilot_vscode": True, "claude_vscode": False},
                                                primary="copilot_vscode", attended_engine="copilot_vscode")))
    h.to_review("WF-142")
    h.err("GET", "/stories/WF-142/prototype-brief?engine=claude_vscode", 409, "ENGINE_DISABLED")
    assert h.ok("GET", "/stories/WF-142/prototype-brief?engine=copilot_vscode")["engineLabel"].startswith("GitHub Copilot")


# ---------------------------------------------------------------- Jira Data Center

def dc_issue(n, sprint):
    return {"id": str(n), "key": f"WF-{n}", "fields": {
        "summary": f"Story {n}", "description": "Intro\n\nAcceptance Criteria:\n- Given a user, when they export, then a CSV with 3 columns downloads within 2 seconds",
        "status": {"name": "In Progress"}, "issuetype": {"name": "Story"}, "labels": ["ai-ready"], "components": [],
        "priority": {"name": "High"}, "updated": "2026-11-09T10:00:00.000+0000", "issuelinks": [], "attachment": [],
        "comment": {"comments": [{"body": "plain text comment"}]}, "customfield_10020": sprint}}


def test_jira_data_center_adapter_uses_a_bearer_token_and_v2_shapes():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/rest/api/2/myself":
            return httpx.Response(200, json={"displayName": "Svc ASDP"})
        if req.url.path == "/rest/api/2/search":
            start = json.loads(req.content)["startAt"]
            page = [dc_issue(1, ["com.atlassian.greenhopper.service.sprint.Sprint@1f[id=7,rapidViewId=3,state=ACTIVE,name=Sprint 4,startDate=2026-11-01T09:00:00.000Z,endDate=2026-11-14T09:00:00.000Z]"])] \
                if start == 0 else [dc_issue(2, [{"id": 7, "name": "Sprint 4", "state": "active"}])]
            return httpx.Response(200, json={"total": 2, "issues": page})
        if req.url.path == "/rest/api/2/issue/WF-1/comment" and req.method == "POST":
            return httpx.Response(201, json={"id": "900"})
        return httpx.Response(404, text="not found")

    client = httpx.Client(base_url="https://jira.corp.example", transport=httpx.MockTransport(handler),
                          headers={"Authorization": "Bearer pat-123"})
    j = JiraDataCenter("https://jira.corp.example", "pat-123", "customfield_10020", "", True, client=client)
    assert j.whoami() == "Svc ASDP"
    issues = j.search_active_sprint("project = WF")
    assert [i.key for i in issues] == ["WF-1", "WF-2"]
    assert issues[0].sprint == {"id": "7", "name": "Sprint 4", "state": "active", "start": "2026-11-01T09:00:00.000Z", "end": "2026-11-14T09:00:00.000Z"}
    assert issues[1].sprint["state"] == "active" and len(issues[0].acceptance_criteria) == 1
    assert issues[0].comments == ["plain text comment"]
    assert j.add_comment("WF-1", "evidence line") == "900"
    assert json.loads(seen[-1].content) == {"body": "evidence line"}  # plain text, not Atlassian Document Format
    assert all(r.headers["authorization"] == "Bearer pat-123" for r in seen)


# ---------------------------------------------------------------- doctor

def status(rows, name):
    return next(r["status"] for r in rows if r["check"] == name)


def test_doctor_reports_what_is_missing_without_calling_anything():
    rows = cli.doctor(Settings(), Policy(jira=JiraPolicy(source="datacenter"), agents=AgentsPolicy(devin_api_enabled=True),
                                         generation=GenerationPolicy(provider="template")), offline=True)
    assert status(rows, "Model access") == "OK"
    assert status(rows, "Jira") == "FAIL" and "ASDP_JIRA_API_TOKEN" in next(r["detail"] for r in rows if r["check"] == "Jira")
    assert status(rows, "Devin") == "FAIL" and status(rows, "GitHub webhook") == "WARN"
    assert status(cli.doctor(Settings(), Policy(), offline=True), "Devin") == "OK"  # manual mode needs no key


def test_doctor_checks_live_connections_and_never_prints_secrets(capsys):
    def jira(req):
        if req.url.path.endswith("/myself"):
            return httpx.Response(200, json={"displayName": "Svc ASDP"})
        return httpx.Response(200, json={"total": 1, "issues": [dc_issue(1, None)]})

    s = Settings(jira_base_url="https://jira.corp.example", jira_api_token="pat-s3cr3tval", devin_api_key="apk-s3cr3tval",
                 github_webhook_secret="hook-s3cr3tval", automation_token="ci-s3cr3tval")
    p = Policy(jira=JiraPolicy(source="datacenter"), agents=AgentsPolicy(devin_api_enabled=True))
    rows = cli.doctor(s, p, jira_client=httpx.Client(base_url="https://jira.corp.example", transport=httpx.MockTransport(jira)),
                      devin_client=httpx.Client(base_url="https://api.devin.ai/v1",
                                                transport=httpx.MockTransport(lambda r: httpx.Response(401, json={}))))
    assert status(rows, "Jira sign-in") == "OK" and status(rows, "Jira sprint query") == "OK"
    assert status(rows, "Jira sprint field") == "FAIL"  # the issue carried no sprint: wrong custom field id
    assert status(rows, "Jira acceptance criteria") == "OK" and status(rows, "Devin") == "FAIL"
    assert cli.print_rows(rows) == 1
    assert "s3cr3tval" not in capsys.readouterr().out


def test_env_file_fills_gaps_but_never_overrides_the_real_environment(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# comment\nASDP_JIRA_EMAIL="bot@corp.example"\nASDP_TENANT_ID=from-file\n\nnot a setting\n', encoding="utf-8")
    monkeypatch.setenv("ASDP_TENANT_ID", "from-shell")
    monkeypatch.delenv("ASDP_JIRA_EMAIL", raising=False)
    monkeypatch.setenv("ASDP_ENV_FILE", str(f))
    s = Settings.from_env()
    assert s.jira_email == "bot@corp.example" and s.tenant_id == "from-shell"
    monkeypatch.delenv("ASDP_JIRA_EMAIL")  # set by the loader; keep it out of other tests
    assert load_env_file(str(tmp_path / "missing.env")) == []
