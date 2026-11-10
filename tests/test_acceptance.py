"""The twelve user acceptance scenarios from the specification (section 10.3)."""
from conftest import CI_EVIDENCE, COMMIT_A, COMMIT_B


def test_at01_duplicate_events_yield_exactly_one_story(h):
    h.sync()
    h.sync()
    payload = {"webhookEvent": "jira:issue_updated", "issue": {"id": "10142", "key": "WF-142"}, "changelog": {"id": "77"}}
    first = h.client.post("/api/v1/webhooks/jira", json=payload).json()
    second = h.client.post("/api/v1/webhooks/jira", json=payload).json()
    assert first["duplicate"] is False and second["duplicate"] is True
    stories = h.ok("GET", "/sprints/active")["stories"]
    assert [s["key"] for s in stories].count("WF-142") == 1
    assert len(stories) == 8


def test_at02_ambiguous_criterion_is_flagged_and_never_dispatched(h):
    h.sync()
    s = h.story("WF-145")
    assert s["state"] == "NEEDS_REFINEMENT"
    assert any("AC-01" in r and "measurable" in r for r in s["source"]["readinessReasons"])
    h.err("POST", "/stories/WF-145/analyze", 409, "NOT_ELIGIBLE", "dan")
    h.err("POST", "/stories/WF-145/dispatch", 409, "NOT_DISPATCHABLE", "lena")
    assert h.story("WF-145")["spec"] is None  # nothing was guessed


def test_at03_broken_navigation_fails_deterministically_and_is_refined(h):
    h.jira.edit("WF-144", labels=["ai-ready", "ui", "proto-fault-once"])
    s = h.to_review("WF-144")
    assert s["prototype"]["version"] == 2 and s["prototype"]["testResult"]["passed"]
    v1 = h.ok("GET", "/stories/WF-144/prototype/1")
    assert v1["status"] == "FAILED"
    assert "P-002" in {f["rule"] for f in v1["testResult"]["failed_rules"]}
    events = [e["event"] for e in s["timeline"]]
    assert "PROTOTYPE_TEST_FAILED" in events
    assert "AUTO_REVISE" in [e["newState"] for e in s["timeline"]]


def test_at04_three_failed_retries_escalate_to_a_human(h):
    h.jira.edit("WF-144", labels=["ai-ready", "ui", "proto-fault-always"])
    h.sync()
    h.ok("POST", "/stories/WF-144/analyze", "dan")
    s = h.story("WF-144")
    assert s["state"] == "NEEDS_HUMAN_INPUT"
    assert s["prototype"]["version"] == 4  # first build + exactly 3 automatic retries, then stop
    assert s["policy"]["autoAttempts"] == 3
    assert h.gates("WF-144")["G2"] == "FAIL"
    assert any("budget exhausted" in b for b in s["blockers"])
    h.err("POST", "/stories/WF-144/approve", 409, "NOT_IN_REVIEW", "priya", json=h.approval_body("WF-144"))


def test_at05_approval_of_v1_does_not_cover_v2(h):
    h.to_review("WF-142")
    stale = h.approval_body("WF-142")
    h.ok("POST", "/stories/WF-142/prototype/revise", "dan", json={"feedback": "Add a confirmation step before Accept"})
    s = h.story("WF-142")
    assert s["state"] == "HUMAN_REVIEW" and s["spec"]["version"] == 2 and s["prototype"]["version"] == 2
    h.err("POST", "/stories/WF-142/approve", 409, "STALE_REVISION", "priya", json=stale)
    h.err("POST", "/stories/WF-142/approve", 409, "STALE_VERSION", "priya", json={**stale, "expectedStoryRevision": s["revision"]})
    # and an approval that was valid stops being valid the moment a new version exists
    h.ok("POST", "/stories/WF-142/approve", "priya", json=h.approval_body("WF-142"))
    assert h.story("WF-142")["baseline"]["valid"] is True
    h.ok("POST", "/stories/WF-142/prototype/revise", "priya", json={"feedback": "Rename the Accept button"})
    s = h.story("WF-142")
    assert s["baseline"]["valid"] is False and h.gates("WF-142")["G3"] == "STALE"
    h.err("POST", "/stories/WF-142/dispatch", 409, "NOT_DISPATCHABLE", "lena")


def test_at06_two_stories_on_the_same_module_are_serialized(h):
    h.to_plan_ready("WF-142")
    h.to_plan_ready("WF-149")  # same component: workflow-builder
    h.ok("POST", "/stories/WF-142/dispatch", "lena")
    r = h.ok("POST", "/stories/WF-149/dispatch", "lena")
    assert r["started"] == [] and r["queued"] == ["contract"]
    s149 = h.story("WF-149")
    assert s149["runs"] == []
    assert any("owned by WF-142" in b for b in s149["blockers"])
    h.finish_runs("WF-142")
    assert h.story("WF-142")["state"] == "CI_VALIDATION"
    s149 = h.story("WF-149")  # released locks let the queued story start on its own
    assert [r["state"] for r in s149["runs"]] == ["AWAITING_MANUAL_LAUNCH"]


def test_at07_developer_fix_via_pr_reruns_ci_and_uat(h):
    h.to_uat("WF-144")
    r = h.ok("POST", "/stories/WF-144/commits", "dan", json={"commit": COMMIT_B, "prUrl": "https://git.example/pr/99", "source": "copilot_vscode"})
    assert r["state"] == "CI_VALIDATION"
    g = h.gates("WF-144")
    assert g["G4"] == g["G5"] == g["G6"] == "STALE"
    h.err("POST", "/stories/WF-144/uat", 409, "NOT_IN_UAT", "quinn", json={"decision": "PASS", "commit": COMMIT_B})
    h.err("POST", "/stories/WF-144/gates", 409, "COMMIT_MISMATCH", "svc-automation",
          json={"gate": "G4", "result": "PASS", "commit": COMMIT_A, "evidence": CI_EVIDENCE})
    for gate in ("G4", "G5", "G6"):
        h.gate("WF-144", gate, commit=COMMIT_B)
    assert h.story("WF-144")["state"] == "UAT"
    h.err("POST", "/stories/WF-144/uat", 409, "COMMIT_MISMATCH", "quinn", json={"decision": "PASS", "commit": COMMIT_A})
    h.ok("POST", "/stories/WF-144/uat", "quinn", json={"decision": "PASS", "commit": COMMIT_B})
    assert h.story("WF-144")["headCommit"] == COMMIT_B


def test_at08_material_ux_change_after_approval_requires_reapproval(h):
    h.to_plan_ready("WF-142")
    h.ok("POST", "/stories/WF-142/dispatch", "lena")
    assert h.story("WF-142")["activeRuns"] == 1
    h.ok("POST", "/stories/WF-142/prototype/revise", "priya", json={"feedback": "Split generation into a two-step wizard"})
    s = h.story("WF-142")
    assert s["state"] == "HUMAN_REVIEW" and s["baseline"]["valid"] is False
    assert s["activeRuns"] == 0 and all(r["state"] == "CANCELLED" for r in s["runs"])
    assert "DESIGN_CHANGE" in [e["event"] for e in s["timeline"]]
    h.ok("POST", "/stories/WF-142/approve", "priya", json=h.approval_body("WF-142"))
    assert h.story("WF-142")["state"] == "PLAN_READY"


def test_at08b_uat_flagged_as_design_change_loops_to_review(h):
    h.to_uat("WF-144")
    h.ok("POST", "/stories/WF-144/uat", "quinn",
         json={"decision": "FAIL", "commit": COMMIT_A, "feedback": "Duration should be a chart, not text", "materialDesignChange": True})
    s = h.story("WF-144")
    assert s["state"] == "HUMAN_REVIEW" and s["baseline"]["valid"] is False


def test_at09_evidence_retries_never_duplicate_the_jira_comment(h):
    h.to_uat("WF-144")
    h.ok("POST", "/stories/WF-144/uat", "quinn", json={"decision": "PASS", "commit": COMMIT_A})
    h.jira.fail_writes = 1  # request never reaches Jira
    h.err("POST", "/stories/WF-144/evidence/sync", 502, "JIRA_WRITE_FAILED", "dan")
    assert h.story("WF-144")["state"] == "EVIDENCE_SYNC" and h.gates("WF-144")["G8"] == "PENDING"
    h.jira.fail_after_write = 1  # request reaches Jira but the response is lost
    h.err("POST", "/stories/WF-144/evidence/sync", 502, "JIRA_WRITE_FAILED", "dan")
    r = h.ok("POST", "/stories/WF-144/evidence/sync", "dan")
    assert r["attempts"] == 3
    again = h.ok("POST", "/stories/WF-144/evidence/sync", "dan")
    assert again["remoteId"] == r["remoteId"]
    assert len(h.jira.comments["WF-144"]) == 1
    assert h.gates("WF-144")["G8"] == "PASS" and h.story("WF-144")["state"] == "DONE_ELIGIBLE"


def test_at10_sprint_ends_before_tests_finish_no_false_done(h):
    h.to_ci("WF-144")
    h.jira.close_sprint()
    h.sync()
    s = h.story("WF-144")
    assert s["state"] == "CARRYOVER"
    h.err("POST", "/stories/WF-144/gates", 409, "WRONG_STAGE", "svc-automation",
          json={"gate": "G4", "result": "PASS", "commit": COMMIT_A, "evidence": CI_EVIDENCE})
    h.err("POST", "/stories/WF-144/transition", 409, "NOT_DONE_ELIGIBLE", "dan")
    assert h.jira.issues["WF-144"].status == "In Progress"


def test_at10b_sprint_closes_between_last_sync_and_done(h):
    h.to_done_eligible("WF-144")
    h.jira.close_sprint()  # the portal has not synced yet; the live check must catch it
    h.err("POST", "/stories/WF-144/transition", 409, "SPRINT_CLOSED", "dan")
    assert h.story("WF-144")["state"] == "CARRYOVER"
    assert h.jira.issues["WF-144"].status == "In Progress"


def test_at11_provider_api_not_entitled_shows_manual_handoff(h):
    h.to_plan_ready("WF-143")
    h.ok("POST", "/stories/WF-143/dispatch", "lena")
    run = h.story("WF-143")["runs"][0]
    assert run["engine"] == "devin" and run["mode"] == "manual" and run["state"] == "AWAITING_MANUAL_LAUNCH"
    assert run["externalId"] == "" and run["externalUrl"] == ""  # no invented session
    assert "WF-143" in run["brief"] and "UNTRUSTED" in run["brief"]
    h.err("POST", f"/runs/{run['id']}/report", 409, "SESSION_NOT_ATTACHED", "dan", json={"state": "SUCCEEDED", "commit": COMMIT_A})
    devin = next(e for e in h.ok("GET", "/admin/config")["engines"] if e["engine"] == "devin")
    assert devin["mode"] == "manual" and devin["api"] is False
    h.ok("POST", f"/runs/{run['id']}/attach", "dan", json={"url": "https://app.devin.ai/sessions/abc123"})
    assert h.story("WF-143")["runs"][0]["state"] == "RUNNING"


def test_at12_uat_rejected_story_cannot_transition_to_done(h):
    h.to_uat("WF-144")
    h.ok("POST", "/stories/WF-144/uat", "quinn", json={"decision": "FAIL", "commit": COMMIT_A, "feedback": "Shows 1m 31s for 90500 ms"})
    s = h.story("WF-144")
    assert s["state"] == "REWORK" and h.gates("WF-144")["G7"] == "FAIL"
    rework = s["runs"][0]
    assert rework["taskKind"] == "rework" and rework["attended"] is True and "1m 31s" in rework["brief"]
    h.err("POST", "/stories/WF-144/transition", 409, "NOT_DONE_ELIGIBLE", "dan")
    h.err("POST", "/stories/WF-144/evidence/sync", 409, "NOT_READY_FOR_EVIDENCE", "dan")
    assert h.jira.issues["WF-144"].status == "In Progress"
