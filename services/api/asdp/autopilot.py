"""Autopilot and the human inbox.

Autopilot does everything between the human decisions. People are asked for exactly three
things per story: approve the spec and prototype, accept the build (UAT), and authorise Done.
Analysis, dispatch, CI evaluation and evidence publication happen without a click.

It never approves, never signs UAT, never closes a story, and never dispatches without an
approval whose digests still match.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import delivery, refine
from .context import AppContext
from .core import POST_APPROVAL, REFINEMENT, SYSTEM, Actor, DomainError, PERMISSIONS
from .gates import valid_baseline
from .models import AgentRun, AuditEvent, JiraSync, KnowledgeNote, PlanTask, Story

PRIORITY = {"Highest": 0, "High": 1, "Medium": 2, "Low": 3, "Lowest": 4}
IN_FLIGHT = REFINEMENT | POST_APPROVAL
MAX_EVIDENCE_ATTEMPTS = 5


def tick(ctx: AppContext, db: Session) -> dict:
    out = {"analyzed": [], "dispatched": [], "published": [], "gates": []}
    ap = ctx.policy.autopilot
    if not ap.enabled or ctx.workspace(db).paused:
        return out
    from .integrations import evaluate_checks
    tenant = ctx.settings.tenant_id
    stories = db.execute(select(Story).where(Story.tenant_id == tenant)).scalars().all()
    stories.sort(key=lambda s: (PRIORITY.get(s.priority, 9), s.key))

    for s in stories:  # checks that arrived before the story reached their stage
        if s.state in ("CI_VALIDATION", "STAGING"):
            out["gates"] += [f"{s.key} {g}" for g in evaluate_checks(ctx, db, s)]

    if ap.auto_publish_evidence:
        for s in stories:
            if s.state != "EVIDENCE_SYNC":
                continue
            attempts = db.execute(select(func.max(JiraSync.attempt)).where(JiraSync.story_id == s.id, JiraSync.kind == "comment",
                                                                           JiraSync.result != "OK")).scalar() or 0
            if attempts >= MAX_EVIDENCE_ATTEMPTS:
                s.blocker = f"Evidence could not be posted to Jira after {attempts} attempts; publish it manually once Jira is reachable."
                continue
            try:
                delivery.sync_evidence(ctx, db, s, SYSTEM)
                out["published"].append(s.key)
            except DomainError as e:
                db.rollback()
                s.blocker = f"Evidence not published yet: {e.message}"
        db.commit()

    if ap.auto_dispatch:
        for s in stories:
            if s.state == "PLAN_READY" and valid_baseline(db, s):
                try:
                    delivery.dispatch(ctx, db, s, SYSTEM)
                    out["dispatched"].append(s.key)
                except DomainError as e:
                    db.rollback()
                    s.blocker = f"Not dispatched: {e.message}"
                    db.commit()

    if ap.auto_analyze:
        wip = sum(1 for s in stories if s.state in IN_FLIGHT)
        for s in stories:
            if wip >= ap.max_wip:
                break
            if s.state == "ELIGIBLE":
                try:
                    refine.analyze(ctx, db, s, SYSTEM)
                    out["analyzed"].append(s.key)
                    wip += 1
                except DomainError:
                    db.rollback()
    return out


# ---------------------------------------------------------------- inbox

def _can(actor: Actor, perm: str) -> bool:
    return bool(set(actor.roles) & PERMISSIONS[perm]) and (actor.human or perm not in ("story.approve", "story.uat", "story.transition"))


def inbox(ctx: AppContext, db: Session, actor: Actor) -> list[dict]:
    """Everything that is waiting for a person, oldest first, with what this user can do about it."""
    tenant, ap = ctx.settings.tenant_id, ctx.policy.autopilot
    now = delivery.utcnow()
    since = dict(db.execute(select(AuditEvent.subject, func.max(AuditEvent.timestamp))
                            .where(AuditEvent.tenant_id == tenant, AuditEvent.event == "STATE_CHANGED")
                            .group_by(AuditEvent.subject)).all())
    items: list[dict] = []

    def add(kind, story, title, detail, perm, at=None, reason="", run=None):
        at = at or since.get(story.key) or story.updated_at
        can = _can(actor, perm) and not reason
        items.append({"kind": kind, "story": story.key, "summary": story.summary, "title": title, "detail": detail,
                      "since": at.isoformat() + "Z", "ageMinutes": int((now - at).total_seconds() // 60),
                      "canAct": can, "reason": reason or ("" if can else f"needs one of: {', '.join(sorted(PERMISSIONS[perm]))}"),
                      "runId": run})

    for s in db.execute(select(Story).where(Story.tenant_id == tenant)).scalars().all():
        if s.state == "HUMAN_REVIEW":
            add("approve", s, "Review and approve", f"Spec v{s.current_spec_version} and prototype v{s.current_prototype_version} passed their checks.", "story.approve")
        elif s.state == "UAT":
            base = valid_baseline(db, s)
            four = ctx.policy.team.four_eyes and base is not None and base.actor == actor.name and _can(actor, "story.uat")
            add("uat", s, "Acceptance test", f"Build {s.head_commit[:10]} passed CI and staging.", "story.uat",
                reason="You approved this baseline, so someone else must sign the acceptance test." if four else "")
        elif s.state == "DONE_ELIGIBLE":
            add("close", s, "Authorise Done", "Every gate has passed and evidence is in Jira.", "story.transition")
        elif s.state in ("NEEDS_HUMAN_INPUT", "CHANGE_REQUESTED"):
            add("decide", s, "Needs a decision", s.blocker or "Automation stopped and needs direction.", "story.revise")
        elif s.state == "NEEDS_REFINEMENT":
            add("clarify", s, "Clarify in Jira", "; ".join(s.readiness_reasons[:3]), "story.sync")
        elif s.state == "EVIDENCE_SYNC" and (not (ap.enabled and ap.auto_publish_evidence) or s.blocker):
            add("publish", s, "Publish evidence", s.blocker or "UAT passed; evidence is ready to post to Jira.", "evidence.sync")
        elif s.state == "PLAN_READY" and not (ap.enabled and ap.auto_dispatch):
            add("dispatch", s, "Dispatch the plan", "Approved and planned; waiting to start.", "story.dispatch")
    runs = db.execute(select(AgentRun).join(Story, Story.id == AgentRun.story_id)
                      .where(Story.tenant_id == tenant, AgentRun.state == "AWAITING_MANUAL_LAUNCH")).scalars().all()
    for r in runs:
        s, t = db.get(Story, r.story_id), db.get(PlanTask, r.task_id)
        label = ctx.providers.providers[r.provider].label
        add("launch", s, f"Launch in {label}", f"Task '{t.task_key.split('~')[0]}' on branch {r.branch}. "
            "Open a pull request from that branch and the portal picks it up.", "run.report", at=r.created_at, run=r.id)
    notes = db.execute(select(KnowledgeNote).where(KnowledgeNote.tenant_id == tenant, KnowledgeNote.status == "pending")).scalars().all()
    for n in notes:
        can = _can(actor, "knowledge.manage")
        items.append({"kind": "lesson", "story": n.story_key, "summary": n.title, "title": "Review a proposed wiki note",
                      "detail": n.body[:240], "since": n.created_at.isoformat() + "Z",
                      "ageMinutes": int((now - n.created_at).total_seconds() // 60), "canAct": can,
                      "reason": "" if can else "needs a team role", "runId": None, "noteId": n.id})
    order = {"approve": 0, "uat": 0, "close": 0, "decide": 1, "launch": 1, "publish": 1, "dispatch": 1, "clarify": 2, "lesson": 3}
    items.sort(key=lambda i: (not i["canAct"], order.get(i["kind"], 9), i["since"]))
    return items
