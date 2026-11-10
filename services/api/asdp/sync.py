"""Jira discovery, readiness classification, webhook inbox and reconciliation (spec 4.1)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .context import AppContext
from .core import POST_APPROVAL, PRE_ANALYSIS, TERMINAL, Actor, DomainError, audit, resume, sha256_json, transition, utcnow
from .gates import latest_source, record_gate
from .jira import JiraError, JiraIssue
from .models import InboxEvent, SprintSnapshot, Story, StorySourceRevision
from .specgen import assess_criterion, assign_criterion_ids, criteria_digest, scan_injection

DIGEST_FIELDS = ["summary", "description", "acceptance_criteria", "status", "issue_type", "labels", "components",
                 "assignee", "priority", "links", "attachments", "sprint"]
READINESS_STATE = {"ready": "ELIGIBLE", "needs_refinement": "NEEDS_REFINEMENT", "blocked": "BLOCKED",
                   "not_eligible": "DISCOVERED"}
OPEN_LINK_STATUSES = {"done", "closed", "resolved"}


def classify(ctx: AppContext, issue: JiraIssue, criteria: list[dict]) -> tuple[str, list[str], list[str]]:
    p = ctx.policy.jira
    warnings: list[str] = []
    for a in issue.attachments:
        if a.get("mime") not in p.supported_attachment_types:
            warnings.append(f"Unsupported attachment type ignored: {a.get('filename')} ({a.get('mime')})")
    flags = scan_injection([issue.description] + issue.comments + issue.acceptance_criteria)
    if flags:
        warnings.append(f"Instruction-like text found in Jira content ({len(flags)}); it is treated as untrusted data and never executed.")

    not_eligible = []
    if issue.status not in p.eligible_statuses:
        not_eligible.append(f"Jira status is '{issue.status}'; pickup requires {' / '.join(p.eligible_statuses)}")
    if p.require_label and p.require_label not in issue.labels:
        not_eligible.append(f"Missing label '{p.require_label}'")
    if not_eligible:
        return "not_eligible", not_eligible, warnings

    blocked = [f"Blocked by {l['key']} ({l.get('status', '?')})" for l in issue.links
               if "blocked by" in l.get("type", "").lower() and l.get("status", "").lower() not in OPEN_LINK_STATUSES]
    if blocked:
        return "blocked", blocked, warnings

    refine = []
    if not criteria:
        refine.append("No acceptance criteria found")
    for c in criteria:
        for problem in assess_criterion(c["text"]):
            refine.append(f"{c['id']}: {problem}")
    if not issue.assignee:
        refine.append("No assigned developer")
    if refine:
        return "needs_refinement", refine, warnings
    return "ready", [], warnings


def upsert_story(ctx: AppContext, db: Session, issue: JiraIssue, actor: str) -> Story:
    tenant = ctx.settings.tenant_id
    ws = ctx.workspace(db)
    story = db.execute(select(Story).where(Story.tenant_id == tenant, Story.jira_issue_id == issue.id)).scalar()
    now = utcnow()
    if story is None:
        story = Story(tenant_id=tenant, workspace_id=ws.id, jira_issue_id=issue.id, key=issue.key,
                      created_at=now, updated_at=now, labels=[], components=[], readiness_reasons=[], warnings=[],
                      criteria_ids={})
        db.add(story)
        try:
            db.flush()
        except IntegrityError:  # concurrent discovery of the same issue: exactly one row survives
            db.rollback()
            story = db.execute(select(Story).where(Story.tenant_id == tenant, Story.jira_issue_id == issue.id)).scalar_one()
        else:
            audit(db, tenant, issue.key, "STORY_DISCOVERED", actor, new="DISCOVERED")

    criteria, known = assign_criterion_ids(issue.acceptance_criteria, story.criteria_ids or {})
    story.criteria_ids = known
    fields = issue.to_fields()
    fields["criteria"] = criteria
    sdig = sha256_json({k: fields[k] for k in DIGEST_FIELDS})
    cdig = criteria_digest(criteria)
    prev = latest_source(db, story)
    changed = prev is None or prev.source_digest != sdig
    if changed:
        existing = db.execute(select(StorySourceRevision).where(StorySourceRevision.story_id == story.id,
                                                                StorySourceRevision.source_digest == sdig)).scalar()
        if existing:  # Jira content reverted to an earlier snapshot; re-append as the latest
            db.delete(existing)
            db.flush()
        db.add(StorySourceRevision(story_id=story.id, jira_updated_at=issue.updated, source_digest=sdig,
                                   criteria_digest=cdig, fields_json=fields, fetched_at=now))
        story.revision += 1
        audit(db, tenant, issue.key, "STORY_SYNCED", actor, detail={"source_digest": sdig, "jira_updated": issue.updated})

    story.key, story.summary, story.issue_type = issue.key, issue.summary, issue.issue_type
    story.jira_status, story.owner, story.priority = issue.status, issue.assignee, issue.priority
    story.labels, story.components = list(issue.labels), list(issue.components)
    story.sprint_jira_id = (issue.sprint or {}).get("id", "")
    story.in_active_sprint = bool(issue.sprint and issue.sprint.get("state") == "active")
    story.updated_at = now
    readiness, reasons, warnings = classify(ctx, issue, criteria)
    story.warnings = warnings
    db.flush()

    if not story.in_active_sprint:
        if story.state not in TERMINAL and story.state != "CARRYOVER":
            _carry_over(ctx, db, story, actor, "Sprint is closed or the story left the active sprint")
        return story
    if story.state == "CARRYOVER":
        resume(db, story, actor)

    if story.state in PRE_ANALYSIS:
        story.readiness, story.readiness_reasons = readiness, reasons
        target = READINESS_STATE[readiness]
        if story.state != target:
            transition(db, story, target, actor, reason="; ".join(reasons) or "eligible")
        if target == "ELIGIBLE" and (changed or story.eligible_at is None):
            story.eligible_at = story.eligible_at or now
            record_gate(db, story, "G0", "PASS", actor, summary="Fresh Jira snapshot, in active sprint, no open blockers, policy satisfied",
                        evidence={"source_digest": sdig, "criteria_digest": cdig, "jira_status": issue.status,
                                  "labels": issue.labels, "policy_version": ctx.policy.version})
        elif target != "ELIGIBLE" and changed:
            record_gate(db, story, "G0", "FAIL", actor, summary="; ".join(reasons), evidence={"reasons": reasons, "source_digest": sdig})
    elif changed and prev is not None and prev.criteria_digest != cdig and story.state not in TERMINAL | {"PAUSED", "FAILED"}:
        _handle_drift(ctx, db, story, actor)
    return story


def _carry_over(ctx: AppContext, db: Session, story: Story, actor: str, reason: str) -> None:
    from .delivery import cancel_story_runs
    cancel_story_runs(ctx, db, story, actor, reason)
    transition(db, story, "CARRYOVER", actor, reason=reason)
    story.blocker = reason


def _handle_drift(ctx: AppContext, db: Session, story: Story, actor: str) -> None:
    """Jira acceptance criteria changed mid-flight: any approval is now stale and work must
    loop back through spec/prototype review."""
    from .delivery import cancel_story_runs
    audit(db, story.tenant_id, story.key, "JIRA_CRITERIA_CHANGED", actor,
          detail={"state": story.state, "effect": "approval invalidated; re-review required"})
    cancel_story_runs(ctx, db, story, actor, "Jira acceptance criteria changed")
    story.blocker = "Jira acceptance criteria changed after this version was produced. Request a revision to regenerate the spec and prototype."
    if story.state in ("HUMAN_REVIEW", "NEEDS_HUMAN_INPUT") or story.state in POST_APPROVAL:
        transition(db, story, "CHANGE_REQUESTED", actor, reason="Jira acceptance criteria changed")



def sync_sprint(ctx: AppContext, db: Session, actor: Actor) -> dict:
    ws = ctx.workspace(db)
    try:
        issues = ctx.jira.search_active_sprint(ctx.policy.jira.discovery_jql)
    except JiraError as e:
        raise DomainError(502, "JIRA_UNAVAILABLE", f"Jira read failed; portal continues on the last snapshot: {e}")
    now = utcnow()
    seen_ids = set()
    for issue in issues:
        seen_ids.add(issue.id)
        if issue.sprint:
            snap = db.execute(select(SprintSnapshot).where(SprintSnapshot.workspace_id == ws.id,
                                                           SprintSnapshot.jira_sprint_id == issue.sprint["id"])).scalar()
            if snap is None:
                snap = SprintSnapshot(workspace_id=ws.id, jira_sprint_id=issue.sprint["id"], fetched_at=now)
                db.add(snap)
            snap.name, snap.state = issue.sprint.get("name", ""), issue.sprint.get("state", "")
            snap.start, snap.end, snap.fetched_at = issue.sprint.get("start", ""), issue.sprint.get("end", ""), now
        upsert_story(ctx, db, issue, actor.name)
        db.commit()
    # stories that left the sprint query entirely
    for story in db.execute(select(Story).where(Story.tenant_id == ctx.settings.tenant_id)).scalars().all():
        if story.jira_issue_id not in seen_ids and story.in_active_sprint:
            story.in_active_sprint = False
            if story.state not in TERMINAL and story.state != "CARRYOVER":
                _carry_over(ctx, db, story, actor.name, "Story is no longer in the active sprint")
    ws.last_sync_at = now
    db.commit()
    return {"synced": len(issues), "at": now.isoformat() + "Z"}


def sync_issue(ctx: AppContext, db: Session, key: str, actor: Actor) -> Story:
    try:
        issue = ctx.jira.get_issue(key)
    except JiraError as e:
        raise DomainError(502, "JIRA_UNAVAILABLE", f"Jira read failed: {e}")
    story = upsert_story(ctx, db, issue, actor.name)
    db.commit()
    return story


def ingest_webhook(ctx: AppContext, db: Session, payload: dict, actor: Actor) -> dict:
    """Durable inbox with deduplication by tenant + issue id + changelog/version."""
    issue = payload.get("issue") or {}
    issue_id, key = str(issue.get("id", "")), issue.get("key", "")
    if not key:
        raise DomainError(400, "BAD_WEBHOOK", "payload has no issue.key")
    version = str((payload.get("changelog") or {}).get("id") or payload.get("timestamp") or "")
    dedupe = f"jira:{issue_id}:{payload.get('webhookEvent', '')}:{version}"
    ev = InboxEvent(tenant_id=ctx.settings.tenant_id, dedupe_key=dedupe, source="jira", payload=payload, received_at=utcnow())
    db.add(ev)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"duplicate": True, "key": key}
    db.commit()
    story = sync_issue(ctx, db, key, actor)
    ev.processed_at = utcnow()
    db.commit()
    return {"duplicate": False, "key": key, "state": story.state}
