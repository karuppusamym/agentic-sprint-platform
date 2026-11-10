"""Git and CI integration: GitHub webhooks and JUnit ingestion.

This is what removes the copy-paste from the loop. A pull request opened on a portal branch
completes the agent run with its real head commit; check runs establish the CI gates on that
exact commit; a JUnit upload maps every test to the acceptance criterion it proves. Nobody
types a commit hash or clicks "tests passed".

The GitHub payload handling follows GitHub's documented webhook shapes and is covered by tests
with synthetic payloads. It has not been exercised against a live repository from here.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import xml.etree.ElementTree as ET

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import delivery
from .context import AppContext
from .core import SYSTEM, Actor, DomainError, audit, require, utcnow
from .gates import current_spec, gate_map, valid_baseline
from .models import AgentRun, CheckResult, InboxEvent, Story

BRANCH_RE = re.compile(r"^asdp/([A-Za-z][A-Za-z0-9]+-\d+)/")
KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
CRITERION_RE = re.compile(r"AC[-_ ]?0*(\d+)", re.I)
CHECK_FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
ACCEPTING_COMMITS = ("CI_VALIDATION", "STAGING", "UAT", "REWORK", "EVIDENCE_SYNC", "DONE_ELIGIBLE", "NEEDS_HUMAN_INPUT")


def verify_signature(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def _story_for(db: Session, tenant: str, branch: str = "", title: str = "", sha: str = "") -> Story | None:
    key = None
    m = BRANCH_RE.match(branch or "")
    if m:
        key = m.group(1).upper()
    elif title and (m := KEY_RE.search(title)):
        key = m.group(1)
    if key:
        return db.execute(select(Story).where(Story.tenant_id == tenant, Story.key == key)).scalar()
    if sha:
        return db.execute(select(Story).where(Story.tenant_id == tenant, Story.head_commit == sha)).scalar()
    return None


def handle_github(ctx: AppContext, db: Session, event: str, delivery_id: str, payload: dict) -> dict:
    if delivery_id:  # GitHub redelivers; each delivery is processed once
        db.add(InboxEvent(tenant_id=ctx.settings.tenant_id, dedupe_key=f"github:{delivery_id}", source="github",
                          payload={"event": event, "action": payload.get("action", "")}, received_at=utcnow()))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return {"handled": False, "reason": "duplicate delivery"}
        db.commit()
    if event == "ping":
        return {"handled": True, "reason": "pong"}
    if event == "pull_request":
        return _pull_request(ctx, db, payload)
    if event == "check_run":
        return _check_run(ctx, db, payload)
    return {"handled": False, "reason": f"event '{event}' is not used"}


def _pull_request(ctx: AppContext, db: Session, p: dict) -> dict:
    action, pr = p.get("action"), p.get("pull_request") or {}
    if action not in ("opened", "reopened", "synchronize", "ready_for_review"):
        return {"handled": False, "reason": f"pull_request.{action} is not used"}
    head = pr.get("head") or {}
    branch, sha, url = head.get("ref", ""), head.get("sha", ""), pr.get("html_url", "")
    head_repo, base_repo = (head.get("repo") or {}).get("full_name"), ((pr.get("base") or {}).get("repo") or {}).get("full_name")
    if head_repo and base_repo and head_repo != base_repo:  # anyone can fork and name a branch or title after a story
        return {"handled": False, "reason": "pull requests from forks are not accepted as story work"}
    story = _story_for(db, ctx.settings.tenant_id, branch, pr.get("title", ""))
    if not story:
        return {"handled": False, "reason": "no story matches this branch or title"}
    run = db.execute(select(AgentRun).where(AgentRun.story_id == story.id, AgentRun.branch == branch,
                                            AgentRun.state.in_(delivery.ACTIVE_RUN))).scalar()
    if run:
        run.pr_url, run.heartbeat_at = url, utcnow()
        if pr.get("draft"):
            run.last_event = "draft pull request open; waiting for ready-for-review"
            db.commit()
            return {"handled": True, "story": story.key, "result": "draft_recorded"}
        delivery.report_run(ctx, db, run, SYSTEM, "SUCCEEDED", commit=sha, pr_url=url, via_git=True)
        return {"handled": True, "story": story.key, "result": "run_completed", "run": run.id, "state": story.state}
    if story.state in ACCEPTING_COMMITS and valid_baseline(db, story):
        res = delivery.ingest_commit(ctx, db, story, SYSTEM, sha, url, source="github")
        return {"handled": True, "story": story.key, "result": "commit_ingested" if res["changed"] else "no_change", "state": story.state}
    audit(db, story.tenant_id, story.key, "GIT_EVENT_IGNORED", "system",
          detail={"branch": branch, "commit": sha, "reason": f"story is {story.state}"})
    db.commit()
    return {"handled": False, "story": story.key, "reason": f"story is {story.state}; no active run on this branch"}


def _check_run(ctx: AppContext, db: Session, p: dict) -> dict:
    cr = p.get("check_run") or {}
    if p.get("action") != "completed":
        return {"handled": False, "reason": "check run not completed yet"}
    sha, name = cr.get("head_sha", ""), cr.get("name", "")
    branch = (cr.get("check_suite") or {}).get("head_branch") or ""
    story = _story_for(db, ctx.settings.tenant_id, branch, sha=sha)
    if not story:
        return {"handled": False, "reason": "no story matches this branch or commit"}
    row = db.execute(select(CheckResult).where(CheckResult.story_id == story.id, CheckResult.commit == sha,
                                               CheckResult.name == name)).scalar()
    if row is None:
        row = CheckResult(story_id=story.id, commit=sha, name=name, conclusion="", received_at=utcnow())
        db.add(row)
    row.conclusion, row.url, row.received_at = cr.get("conclusion") or "", cr.get("html_url", ""), utcnow()
    db.commit()
    recorded = evaluate_checks(ctx, db, story)
    return {"handled": True, "story": story.key, "check": name, "gatesRecorded": recorded, "state": story.state}


def evaluate_checks(ctx: AppContext, db: Session, story: Story) -> list[str]:
    """Turn the stored check results for the head commit into gate results, once the story is
    at the stage that gate belongs to. Safe to call repeatedly."""
    recorded: list[str] = []
    for gate, stage in (("G4", "CI_VALIDATION"), ("G5", "CI_VALIDATION"), ("G6", "STAGING")):
        names = ctx.policy.ci.checks.get(gate) or []
        if not names or story.state != stage or not story.head_commit or gate_map(db, story)[gate] in ("PASS", "FAIL"):
            continue
        rows = {r.name: r for r in db.execute(select(CheckResult).where(CheckResult.story_id == story.id,
                                                                        CheckResult.commit == story.head_commit)).scalars().all()}
        have = [rows[n] for n in names if n in rows]
        failed = [r for r in have if r.conclusion in CHECK_FAILED]
        passed = [r for r in have if r.conclusion == "success"]
        evidence = {"source": "github-checks", "commit": story.head_commit,
                    "checks": [{"name": r.name, "conclusion": r.conclusion, "url": r.url} for r in have],
                    "required": names}
        if failed:
            delivery.record_ci_gate(ctx, db, story, SYSTEM, gate, "FAIL", story.head_commit,
                                    "Failed checks: " + ", ".join(r.name for r in failed), evidence)
            recorded.append(f"{gate}:FAIL")
            break  # the story left this stage for repair
        if len(passed) == len(names):
            delivery.record_ci_gate(ctx, db, story, SYSTEM, gate, "PASS", story.head_commit,
                                    "Checks passed: " + ", ".join(names), evidence)
            recorded.append(f"{gate}:PASS")
    return recorded


# ---------------------------------------------------------------- JUnit

def parse_junit(xml_text: str) -> dict:
    if len(xml_text) > 5_000_000:
        raise DomainError(413, "REPORT_TOO_LARGE", "JUnit report is larger than 5 MB")
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:  # no entity expansion from untrusted reports
        raise DomainError(400, "BAD_REPORT", "JUnit reports with a DOCTYPE or entities are not accepted")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise DomainError(400, "BAD_REPORT", f"JUnit report is not valid XML: {e}")
    cases = []
    for tc in root.iter("testcase"):
        status = "passed"
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "failed"
        elif tc.find("skipped") is not None:
            status = "skipped"
        cases.append({"name": tc.get("name", ""), "classname": tc.get("classname", ""), "status": status})
    if not cases:
        raise DomainError(400, "BAD_REPORT", "JUnit report contains no test cases")
    return {"tests": len(cases), "failed": sum(c["status"] == "failed" for c in cases),
            "skipped": sum(c["status"] == "skipped" for c in cases), "cases": cases}


def criterion_results(cases: list[dict], criterion_ids: list[str]) -> dict[str, dict]:
    """Map tests to criteria by the id carried in the test name (AC-01, AC_01, ac1 ...)."""
    out = {cid: {"passed": 0, "failed": 0, "skipped": 0, "tests": []} for cid in criterion_ids}
    for c in cases:
        for num in {int(n) for n in CRITERION_RE.findall(f"{c['classname']} {c['name']}")}:
            cid = f"AC-{num:02d}"
            if cid in out:
                out[cid][c["status"]] += 1
                out[cid]["tests"].append(c["name"][:120])
    return out


def ingest_junit(ctx: AppContext, db: Session, story: Story, actor: Actor, gate: str, commit: str, xml_text: str,
                 run_url: str = "") -> dict:
    require(actor, "gate.record")
    report = parse_junit(xml_text)
    spec = current_spec(db, story)
    if not spec:
        raise DomainError(409, "NO_SPEC", f"{story.key} has no specification to map tests to")
    ids = [c["id"] for c in spec.content["criteria"]]
    per = criterion_results(report["cases"], ids)
    failing = [cid for cid, r in per.items() if r["failed"]]
    untested = [cid for cid, r in per.items() if not r["passed"] and not r["failed"]]
    enforce = ctx.policy.quality.require_criterion_tests and gate == "G4"
    ok = report["failed"] == 0 and not (enforce and untested)
    parts = [f"{report['tests']} tests, {report['failed']} failed, {report['skipped']} skipped"]
    if failing:
        parts.append("failing criteria: " + ", ".join(failing))
    if untested:
        parts.append(("criteria with no passing test: " if enforce else "criteria not covered in this report: ") + ", ".join(untested))
    evidence = {"source": "junit", "commit": commit, "ci_run_url": run_url, "tests": report["tests"],
                "failed": report["failed"], "skipped": report["skipped"], "criteria": per, "untested": untested,
                "failed_tests": [c["name"] for c in report["cases"] if c["status"] == "failed"][:50]}
    res = delivery.record_ci_gate(ctx, db, story, actor, gate, "PASS" if ok else "FAIL", commit, "; ".join(parts), evidence)
    return {**res, "result": "PASS" if ok else "FAIL", "summary": "; ".join(parts), "criteria": per}
