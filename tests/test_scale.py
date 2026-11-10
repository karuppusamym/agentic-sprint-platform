"""Small-team operation: autopilot, Git/CI integration, playbooks + wiki, spec deepening, inbox."""
import hashlib
import hmac
import json

import httpx
import pytest
from conftest import CI_EVIDENCE, COMMIT_A, COMMIT_B

from asdp.config import AgentsPolicy, AutopilotPolicy, DevUser, Policy, Settings
from asdp.providers import AgentRunRequest, DevinProvider

AUTO = Policy(autopilot=AutopilotPolicy(enabled=True, max_wip=2))


def gh(h, event, payload, delivery="", secret=""):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if delivery:
        headers["X-GitHub-Delivery"] = delivery
    if secret:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return h.client.post("/api/v1/webhooks/github", content=body, headers=headers)


def pr(branch, sha, action="opened", draft=False, title="WF change"):
    return {"action": action, "pull_request": {"html_url": f"https://github.example/pr/{sha[:6]}", "draft": draft, "title": title,
                                                "head": {"ref": branch, "sha": sha}}}


def check(name, sha, conclusion="success", branch=""):
    return {"action": "completed", "check_run": {"name": name, "head_sha": sha, "conclusion": conclusion,
                                                 "html_url": f"https://github.example/checks/{name}",
                                                 "check_suite": {"head_branch": branch}}}


def junit(cases):
    rows = "".join(f'<testcase classname="suite" name="{n}">{"<failure/>" if not ok else ""}</testcase>' for n, ok in cases)
    return f'<?xml version="1.0"?><testsuite name="unit">{rows}</testsuite>'


def open_prs(h, key, sha=COMMIT_A, rounds=6):
    """Play the agents: open a pull request on every launched branch until implementation completes."""
    for _ in range(rounds):
        active = [r for r in h.story(key)["runs"] if r["state"] in ("AWAITING_MANUAL_LAUNCH", "RUNNING")]
        if not active:
            return
        for r in active:
            assert gh(h, "pull_request", pr(r["branch"], sha)).json()["result"] == "run_completed"


# ---------------------------------------------------------------- autopilot

def test_autopilot_needs_only_three_human_decisions_per_story(make_harness):
    h = make_harness(AUTO)
    h.sync()  # 1 click for the whole sprint; in production this is the Jira webhook / timer
    states = {s["key"]: s["state"] for s in h.ok("GET", "/sprints/active")["stories"]}
    assert states["WF-142"] == "HUMAN_REVIEW" and states["WF-143"] == "HUMAN_REVIEW"  # highest priority first
    assert states["WF-144"] == "ELIGIBLE" and states["WF-149"] == "ELIGIBLE"  # held back by the WIP limit of 2

    r = h.ok("POST", "/stories/WF-143/approve", "priya", json=h.approval_body("WF-143"))  # decision 1
    assert r["state"] == "IMPLEMENTING"  # planned and dispatched without a click
    open_prs(h, "WF-143")
    assert h.story("WF-143")["state"] == "CI_VALIDATION"
    for name in ("lint", "unit", "integration", "security"):
        gh(h, "check_run", check(name, COMMIT_A))
    assert h.story("WF-143")["state"] == "STAGING"
    gh(h, "check_run", check("staging-e2e", COMMIT_A))
    assert h.story("WF-143")["state"] == "UAT"

    r = h.ok("POST", "/stories/WF-143/uat", "quinn", json={"decision": "PASS", "commit": COMMIT_A})  # decision 2
    assert r["state"] == "DONE_ELIGIBLE"  # evidence posted to Jira without a click
    assert len(h.jira.comments["WF-143"]) == 1
    h.ok("POST", "/stories/WF-143/transition", "dan")  # decision 3
    s = h.story("WF-143")
    assert s["state"] == "DONE" and all(g["status"] == "PASS" for g in s["gates"])
    k = h.ok("GET", "/metrics/kpis")
    assert k["human_touches_per_done_story_median"] == 3
    assert k["share_of_lead_time_waiting_on_people"] is not None


def test_autopilot_never_approves_tests_or_closes(make_harness):
    h = make_harness(AUTO)
    h.sync()
    for _ in range(3):
        h.ok("POST", "/admin/reconcile", "lena")
    assert h.story("WF-142")["state"] == "HUMAN_REVIEW" and h.story("WF-142")["approvals"] == []
    h.err("POST", "/stories/WF-142/approve", 403, "FORBIDDEN", "svc-automation", json=h.approval_body("WF-142"))


def test_autopilot_respects_wip_and_the_kill_switch(make_harness):
    h = make_harness(AUTO)
    h.ok("POST", "/admin/pause", "sam", json={"paused": True, "reason": "freeze"})
    h.sync()
    assert all(s["specVersion"] == 0 for s in h.ok("GET", "/sprints/active")["stories"])
    h.ok("POST", "/admin/pause", "sam", json={"paused": False})
    h.sync()
    in_flight = [s for s in h.ok("GET", "/sprints/active")["stories"] if s["specVersion"] > 0]
    assert len(in_flight) == 2


def test_autopilot_is_off_by_default(h):
    h.sync()
    assert h.story("WF-142")["state"] == "ELIGIBLE"


# ---------------------------------------------------------------- GitHub

def test_pull_request_completes_a_manual_devin_run_without_copy_paste(h):
    h.to_plan_ready("WF-143")
    h.ok("POST", "/stories/WF-143/dispatch", "lena")
    run = h.story("WF-143")["runs"][0]
    assert run["state"] == "AWAITING_MANUAL_LAUNCH"
    assert gh(h, "pull_request", pr(run["branch"], COMMIT_A, draft=True)).json()["result"] == "draft_recorded"
    assert h.story("WF-143")["runs"][0]["state"] == "AWAITING_MANUAL_LAUNCH"
    gh(h, "pull_request", pr(run["branch"], COMMIT_A, action="ready_for_review"))
    s = h.story("WF-143")
    assert s["state"] == "CI_VALIDATION" and s["headCommit"] == COMMIT_A and s["prUrl"].startswith("https://github.example/pr/")
    assert s["runs"][0]["externalUrl"] == ""  # still no invented session


def test_later_push_resets_gates_and_checks_are_pinned_to_the_commit(h):
    h.to_uat("WF-144")
    branch = h.story("WF-144")["runs"][0]["branch"]
    r = gh(h, "pull_request", pr(branch, COMMIT_B, action="synchronize")).json()
    assert r["result"] == "commit_ingested" and r["state"] == "CI_VALIDATION"
    gh(h, "check_run", check("lint", COMMIT_A, branch=branch))  # result for the old commit must not count
    gh(h, "check_run", check("unit", COMMIT_A, branch=branch))
    assert h.gates("WF-144")["G4"] == "STALE"
    gh(h, "check_run", check("lint", COMMIT_B, branch=branch))
    gh(h, "check_run", check("unit", COMMIT_B, branch=branch))
    assert h.gates("WF-144")["G4"] == "PASS"
    assert {c["name"] for c in h.story("WF-144")["checks"]} == {"lint", "unit"}


def test_failed_check_starts_a_repair_and_early_checks_wait_for_their_stage(h):
    h.to_ci("WF-144")
    gh(h, "check_run", check("staging-e2e", COMMIT_A))  # arrives before staging
    gh(h, "check_run", check("lint", COMMIT_A))
    assert h.gates("WF-144")["G4"] == "PENDING"  # unit has not reported
    gh(h, "check_run", check("unit", COMMIT_A, "failure"))
    s = h.story("WF-144")
    assert s["state"] == "IMPLEMENTING" and s["runs"][0]["taskKind"] == "repair"
    assert "github.example/checks/unit" in s["runs"][0]["brief"] and "Playbook: repair a failed gate" in s["runs"][0]["brief"]


def test_webhook_signature_and_redelivery(make_harness, tmp_path):
    h = make_harness()
    h.ctx.settings.github_webhook_secret = "s3cret"
    assert gh(h, "ping", {}).status_code == 401
    assert gh(h, "ping", {}, secret="wrong").status_code == 401
    assert gh(h, "ping", {}, delivery="d-1", secret="s3cret").json()["handled"] is True
    assert gh(h, "ping", {}, delivery="d-1", secret="s3cret").json()["reason"] == "duplicate delivery"
    h.ctx.settings.dev_mode, h.ctx.settings.github_webhook_secret = False, ""
    assert gh(h, "ping", {}).status_code == 503


def test_unknown_branch_is_ignored(h):
    h.sync()
    assert gh(h, "pull_request", pr("feature/unrelated", COMMIT_A)).json()["handled"] is False


# ---------------------------------------------------------------- JUnit: tests mapped to criteria

def test_junit_maps_tests_to_criteria_and_establishes_g4(h):
    h.to_ci("WF-143")
    ok = junit([("test_AC_01_starts_one_run", True), ("test_ac-2 dead letter", True), ("AC03 dedupes", True), ("helper", True)])
    h.ctx.settings.automation_token = "ci-token"
    r = h.client.post(f"/api/v1/stories/WF-143/ci/junit?commit={COMMIT_A}&gate=G4&runUrl=https://ci.example/9",
                      content=ok, headers={"Authorization": "Bearer ci-token"})
    assert r.status_code == 200 and r.json()["result"] == "PASS"
    assert h.gates("WF-143")["G4"] == "PASS"
    trace = {t["criterion_id"]: t for t in h.story("WF-143")["traceability"]}
    assert trace["AC-02"]["ciTests"] == {"passed": 1, "failed": 0, "tests": ["test_ac-2 dead letter"]}
    bad = h.client.post(f"/api/v1/stories/WF-143/ci/junit?commit={COMMIT_A}", content=ok, headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_junit_fails_the_gate_when_a_criterion_has_no_test(h):
    h.to_ci("WF-143")
    r = h.client.post(f"/api/v1/stories/WF-143/ci/junit?commit={COMMIT_A}", headers={"X-User": "svc-automation"},
                      content=junit([("test_AC_01_x", True), ("test_AC_02_y", True)])).json()
    assert r["result"] == "FAIL" and "criteria with no passing test: AC-03" in r["summary"]
    assert h.story("WF-143")["state"] == "IMPLEMENTING"  # targeted repair with that evidence


def test_junit_failure_and_hostile_reports(h):
    h.to_ci("WF-144")
    post = lambda body, user="svc-automation": h.client.post(
        f"/api/v1/stories/WF-144/ci/junit?commit={COMMIT_A}", content=body, headers={"X-User": user})
    assert post('<!DOCTYPE x [<!ENTITY a "b">]><testsuite/>').json()["error"]["code"] == "BAD_REPORT"
    assert post("<testsuite></testsuite>").json()["error"]["code"] == "BAD_REPORT"
    assert post(junit([("AC-01 ok", True)]), "dan").status_code == 403
    assert post(junit([("AC-01 formats duration", False)])).json()["result"] == "FAIL"


def test_test_plan_and_e2e_skeleton(h):
    h.to_review("WF-142")
    plan = h.ok("GET", "/stories/WF-142/test-plan")
    assert [c["testNameMustContain"] for c in plan["criteria"]] == ["AC-01", "AC-02", "AC-03", "AC-04"]
    assert plan["e2eSkeleton"].count("test(") == 4 and 'test("AC-01 ' in plan["e2eSkeleton"]


# ---------------------------------------------------------------- playbooks and wiki

def test_brief_carries_the_playbook_and_only_relevant_wiki_pages(h):
    h.to_plan_ready("WF-144")  # ui story
    h.to_plan_ready("WF-143")  # backend story
    h.ok("POST", "/stories/WF-144/dispatch", "lena")
    h.ok("POST", "/stories/WF-143/dispatch", "lena")
    ui, backend = h.story("WF-144")["runs"][0]["brief"], h.story("WF-143")["runs"][0]["brief"]
    for brief in (ui, backend):
        assert "Playbook: implement a task from an approved baseline" in brief
        assert "Testing conventions" in brief and "whose name contains" in brief
    assert "Portal conventions" in ui and "Portal conventions" not in backend  # tagged ui
    ids = {p["id"] for p in h.ok("GET", "/playbooks")["playbooks"]}
    assert {"spec-authoring", "implement-task", "contract-first", "integration", "repair-ci", "rework-uat", "schema-migration"} <= ids


def test_wiki_notes_written_by_the_team_reach_the_next_brief(h):
    h.ok("POST", "/knowledge", "dan", json={"title": "Kafka consumers", "tags": ["triggers"],
                                             "body": "Consumers must be idempotent on message id; use the outbox helper."})
    h.err("POST", "/knowledge", 403, "FORBIDDEN", "svc-automation", json={"title": "x", "body": "y"})
    h.to_plan_ready("WF-143")
    h.ok("POST", "/stories/WF-143/dispatch", "lena")
    assert "use the outbox helper" in h.story("WF-143")["runs"][0]["brief"]
    assert "use the outbox helper" in h.ok("GET", "/knowledge/export")["markdown"]


def test_lessons_from_failures_stay_pending_until_a_person_activates_them(h):
    h.to_uat("WF-144")
    h.ok("POST", "/stories/WF-144/uat", "quinn", json={"decision": "FAIL", "commit": COMMIT_A, "feedback": "Rounds 90500 ms up to 1m 31s"})
    note = next(n for n in h.ok("GET", "/knowledge")["notes"] if n["status"] == "pending")
    assert note["source"] == "learned" and "1m 31s" in note["body"] and note["tags"] == ["dashboard"]
    assert "Lesson from WF-144" not in h.story("WF-144")["runs"][0]["brief"]  # not guidance yet
    assert any(i["kind"] == "lesson" for i in h.ok("GET", "/inbox", "dan")["items"])
    h.err("POST", f"/knowledge/{note['id']}/status", 403, "FORBIDDEN", "svc-automation", json={"status": "active"})
    h.ok("POST", f"/knowledge/{note['id']}/status", "dan",
         json={"status": "active", "body": "Durations are truncated, never rounded up: 90500 ms is 1m 30s."})
    h.ok("POST", "/stories/WF-144/commits", "dan", json={"commit": COMMIT_B})
    h.gate("WF-144", "G4", "FAIL", commit=COMMIT_B)
    assert "never rounded up" in h.story("WF-144")["runs"][0]["brief"]  # the repair run now knows


def test_devin_api_mode_sends_the_mapped_playbook_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"session_id": "devin-1", "url": "https://app.devin.ai/sessions/devin-1"})

    policy = Policy(agents=AgentsPolicy(devin_api_enabled=True, devin_playbooks={"implement-task": "playbook-abc"}))
    provider = DevinProvider(Settings(devin_api_key="k"), policy,
                             client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.devin.ai/v1"))
    receipt = provider.create_run(AgentRunRequest(story_key="WF-1", task_key="impl", spec_digest="d", branch="b", allowed_paths=[],
                                                  brief="do it", cost_limit=1, timeout_minutes=1, trace_id="t",
                                                  idempotency_key="k", playbook_id="playbook-abc"))
    assert receipt.mode == "api" and receipt.external_id == "devin-1"
    assert seen["playbook_id"] == "playbook-abc" and seen["idempotent"] is True


# ---------------------------------------------------------------- spec deepening by an engine

ENRICHMENT = {
    "business_rules": [{"text": "Trigger offsets are committed only after the run row is written", "criterion_id": "AC-01"},
                       {"text": "Bogus reference", "criterion_id": "AC-77"}],
    "assumptions": [{"text": "orders.created uses the shared Avro registry"}],
    "nonfunctional": [], "error_cases": [], "open_questions": [{"text": "Which dead-letter topic name?"}], "screens": [],
    "code_impact": [{"path": "services/triggers/consumer.py", "change": "add idempotency check", "risk": "high"},
                    {"path": "../../etc/passwd", "change": "x", "risk": "low"}],
}


def test_engine_deepens_the_spec_from_the_codebase(h):
    h.to_review("WF-143")
    for engine in ("devin", "claude_vscode", "copilot_vscode"):
        pkg = h.ok("GET", f"/stories/WF-143/spec-brief?engine={engine}", "dan")
        assert "READ-ONLY" in pkg["brief"] and '"code_impact"' in pkg["brief"] and "AC-03" in pkg["brief"]
        assert "deepen a specification from the codebase" in pkg["brief"] and "UNTRUSTED" in pkg["brief"]
    h.ok("POST", "/stories/WF-143/prototype/revise", "dan", json={"origin": "devin", "enrichment": ENRICHMENT})
    s = h.story("WF-143")
    c = s["spec"]["content"]
    assert s["state"] == "HUMAN_REVIEW" and s["spec"]["version"] == 2
    assert [i["path"] for i in c["code_impact"]] == ["services/triggers/consumer.py"]  # traversal path dropped
    devin_rules = [r for r in c["business_rules"] if r.get("origin") == "devin"]
    assert len(devin_rules) == 1 and devin_rules[0]["source"] == "AC-01"  # invented criterion dropped
    assert c["open_questions"][0]["text"].startswith("Which dead-letter")
    assert h.gates("WF-143")["G1"] == "PASS"
    # the findings survive a later human revision, and reach the implementation brief
    h.ok("POST", "/stories/WF-143/prototype/revise", "priya", json={"feedback": "Tighten the wording"})
    assert h.story("WF-143")["spec"]["content"]["code_impact"][0]["risk"] == "high"
    h.ok("POST", "/stories/WF-143/approve", "priya", json=h.approval_body("WF-143"))
    h.ok("POST", "/stories/WF-143/dispatch", "lena")
    assert "services/triggers/consumer.py: add idempotency check (risk: high)" in h.story("WF-143")["runs"][0]["brief"]


def test_bad_enrichment_is_rejected(h):
    h.to_review("WF-143")
    h.err("POST", "/stories/WF-143/prototype/revise", 400, "BAD_ENRICHMENT", "dan", json={"enrichment": "do whatever"})
    h.err("POST", "/stories/WF-143/prototype/revise", 400, "BAD_ENRICHMENT", "dan", json={"enrichment": {"business_rules": "x"}})
    h.err("POST", "/stories/WF-143/prototype/revise", 400, "BAD_ENRICHMENT", "dan", json={"enrichment": {}})
    assert h.story("WF-143")["spec"]["version"] == 1


# ---------------------------------------------------------------- inbox and two-person teams

def test_inbox_shows_each_person_what_only_they_can_do(h):
    h.to_review("WF-142")
    by_user = lambda u: {(i["kind"], i["story"]): i for i in h.ok("GET", "/inbox", u)["items"]}
    assert by_user("priya")[("approve", "WF-142")]["canAct"] is True
    assert by_user("dan")[("approve", "WF-142")]["canAct"] is False
    assert ("clarify", "WF-145") in by_user("dan")
    h.ok("POST", "/stories/WF-142/approve", "priya", json=h.approval_body("WF-142"))
    assert by_user("lena")[("dispatch", "WF-142")]["canAct"] is True
    h.ok("POST", "/stories/WF-142/dispatch", "lena")
    launch = by_user("dan")[("launch", "WF-142")]
    assert launch["canAct"] and "Devin" in launch["title"] and launch["runId"]
    assert not [i for i in h.ok("GET", "/inbox", "priya")["items"] if i["canAct"] and i["story"] == "WF-142"]


def two_people(make_harness, policy=None):
    h = make_harness(policy)
    h.ctx.settings.users = [
        DevUser(name="asha", display="Asha", roles=["product_owner", "eng_lead", "developer", "qa"]),
        DevUser(name="ben", display="Ben", roles=["developer", "qa", "devops", "eng_lead", "product_owner"]),
        DevUser(name="svc-automation", display="CI", roles=["automation"], human=False)]
    h.default_user = "asha"
    return h


def test_two_person_team_keeps_four_eyes_on_every_story(make_harness):
    h = two_people(make_harness, AUTO)
    h.ok("POST", "/sprints/sync", "asha")
    h.ok("POST", "/stories/WF-143/approve", "asha", json=h.approval_body("WF-143"))
    open_prs(h, "WF-143")
    for name in ("lint", "unit", "integration", "security", "staging-e2e"):
        gh(h, "check_run", check(name, COMMIT_A))
    assert h.story("WF-143")["state"] == "UAT"
    h.err("POST", "/stories/WF-143/uat", 403, "FOUR_EYES", "asha", json={"decision": "PASS", "commit": COMMIT_A})
    mine = next(i for i in h.ok("GET", "/inbox", "asha")["items"] if i["kind"] == "uat")
    assert mine["canAct"] is False and "someone else" in mine["reason"]
    assert next(i for i in h.ok("GET", "/inbox", "ben")["items"] if i["kind"] == "uat")["canAct"] is True
    h.ok("POST", "/stories/WF-143/uat", "ben", json={"decision": "PASS", "commit": COMMIT_A})
    h.ok("POST", "/stories/WF-143/transition", "asha")
    assert h.story("WF-143")["state"] == "DONE"


def test_team_file_is_loaded(tmp_path, monkeypatch):
    from asdp.config import ROOT
    monkeypatch.setenv("ASDP_USERS_PATH", str(ROOT / "config" / "users.two-person.example.yaml"))
    users = {u.name: u for u in Settings.from_env().users}
    assert set(users) == {"asha", "ben", "admin", "svc-automation"} and users["svc-automation"].human is False
    assert "product_owner" in users["asha"].roles and "qa" in users["ben"].roles
