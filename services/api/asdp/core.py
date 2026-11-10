"""Shared primitives: errors, digests, audit trail, state machine, leases, RBAC."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, text, update
from sqlalchemy.orm import Session

from .models import AuditEvent, Story


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:16]


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_text(canonical(obj))


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


# ---------------------------------------------------------------- identity / RBAC

@dataclass
class Actor:
    name: str
    roles: list[str] = field(default_factory=list)
    human: bool = True


SYSTEM = Actor(name="system", roles=["automation"], human=False)

ALL_ROLES = {"product_owner", "developer", "qa", "eng_lead", "devops", "security", "admin", "automation"}

PERMISSIONS: dict[str, set[str]] = {
    "read": ALL_ROLES,
    "story.sync": {"product_owner", "developer", "qa", "eng_lead", "automation", "admin"},
    "story.analyze": {"product_owner", "developer", "eng_lead", "automation"},
    "story.revise": {"product_owner", "developer", "qa", "eng_lead"},
    "story.annotate": {"product_owner", "developer", "qa", "eng_lead", "security"},
    "story.approve": {"product_owner", "eng_lead"},
    "story.dispatch": {"eng_lead", "developer", "automation"},  # always still needs a valid human approval
    "knowledge.manage": {"product_owner", "developer", "qa", "eng_lead"},
    "story.pause": {"product_owner", "developer", "eng_lead", "security", "devops"},
    "run.cancel": {"eng_lead", "devops", "developer", "security"},
    "run.report": {"automation", "developer", "eng_lead"},
    "commit.ingest": {"automation", "developer", "eng_lead"},
    "gate.record": {"automation", "devops"},
    "story.uat": {"developer", "qa"},
    "evidence.sync": {"developer", "qa", "eng_lead", "automation"},
    "story.transition": {"developer", "qa", "eng_lead"},
    "admin": {"admin", "eng_lead", "security", "devops"},
}
# Actions that must be performed by a real person, never by a bot identity.
HUMAN_ONLY = {"story.approve", "story.uat", "story.transition"}


def require(actor: Actor, permission: str) -> None:
    if not (set(actor.roles) & PERMISSIONS[permission]):
        raise DomainError(403, "FORBIDDEN", f"{actor.name} lacks permission '{permission}'")
    if permission in HUMAN_ONLY and not actor.human:
        raise DomainError(403, "HUMAN_REQUIRED", f"'{permission}' must be performed by a named person, not {actor.name}")


# ---------------------------------------------------------------- audit (append-only, hash chained)

def audit(db: Session, tenant_id: str, subject: str, event: str, actor: str, *, old: str = "", new: str = "",
          detail: dict | None = None, correlation_id: str = "") -> AuditEvent:
    # Two writers must never link to the same previous event. On PostgreSQL a transaction-scoped
    # advisory lock serialises appenders; on SQLite the INSERT below takes the database write lock.
    # Either way the chain head is read only after this transaction owns the right to append.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "asdp-audit:" + tenant_id})
    ts = utcnow()
    row = AuditEvent(tenant_id=tenant_id, subject=subject, event=event, actor=actor, old_state=old, new_state=new,
                     detail=detail or {}, correlation_id=correlation_id, timestamp=ts, prev_hash="", hash="")
    db.add(row)
    db.flush()
    prev = db.execute(
        select(AuditEvent.hash).where(AuditEvent.tenant_id == tenant_id, AuditEvent.id < row.id)
        .order_by(AuditEvent.id.desc()).limit(1)
    ).scalar() or ""
    body = {"subject": subject, "event": event, "actor": actor, "old": old, "new": new,
            "detail": detail or {}, "ts": ts.isoformat(), "prev": prev}
    row.prev_hash, row.hash = prev, sha256_json(body)
    db.flush()
    return row


def verify_audit_chain(db: Session, tenant_id: str) -> dict:
    prev = ""
    count = 0
    for row in db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.id)).scalars():
        body = {"subject": row.subject, "event": row.event, "actor": row.actor, "old": row.old_state,
                "new": row.new_state, "detail": row.detail or {}, "ts": row.timestamp.isoformat(), "prev": prev}
        if row.prev_hash != prev or row.hash != sha256_json(body):
            return {"ok": False, "events": count, "broken_at": row.id}
        prev = row.hash
        count += 1
    return {"ok": True, "events": count, "broken_at": None}


# ---------------------------------------------------------------- state machine

PRE_ANALYSIS = {"DISCOVERED", "ELIGIBLE", "NEEDS_REFINEMENT", "BLOCKED"}
REFINEMENT = {"ANALYZING", "SPEC_DRAFT", "SPEC_CHECK", "PROTOTYPE_BUILD", "PROTOTYPE_TEST", "AUTO_REVISE",
              "HUMAN_REVIEW", "CHANGE_REQUESTED", "NEEDS_HUMAN_INPUT"}
POST_APPROVAL = {"APPROVED_BASELINE", "PLANNING", "PLAN_READY", "IMPLEMENTING", "CI_VALIDATION", "STAGING", "UAT",
                 "REWORK", "EVIDENCE_SYNC", "DONE_ELIGIBLE"}
TERMINAL = {"DONE", "CANCELLED"}
ALL_STATES = PRE_ANALYSIS | REFINEMENT | POST_APPROVAL | TERMINAL | {"PAUSED", "FAILED", "CARRYOVER"}

TRANSITIONS: dict[str, set[str]] = {
    "DISCOVERED": {"ELIGIBLE", "NEEDS_REFINEMENT", "BLOCKED"},
    "ELIGIBLE": {"ANALYZING", "NEEDS_REFINEMENT", "BLOCKED", "DISCOVERED"},
    "NEEDS_REFINEMENT": {"ELIGIBLE", "BLOCKED", "DISCOVERED"},
    "BLOCKED": {"ELIGIBLE", "NEEDS_REFINEMENT", "DISCOVERED"},
    "ANALYZING": {"SPEC_DRAFT", "NEEDS_REFINEMENT"},
    "SPEC_DRAFT": {"SPEC_CHECK"},
    "SPEC_CHECK": {"PROTOTYPE_BUILD", "NEEDS_HUMAN_INPUT"},
    "PROTOTYPE_BUILD": {"PROTOTYPE_TEST"},
    "PROTOTYPE_TEST": {"AUTO_REVISE", "HUMAN_REVIEW", "NEEDS_HUMAN_INPUT"},
    "AUTO_REVISE": {"SPEC_CHECK", "NEEDS_HUMAN_INPUT"},
    "HUMAN_REVIEW": {"CHANGE_REQUESTED", "APPROVED_BASELINE"},
    "CHANGE_REQUESTED": {"SPEC_CHECK"},
    "NEEDS_HUMAN_INPUT": {"SPEC_CHECK", "CHANGE_REQUESTED", "IMPLEMENTING", "CI_VALIDATION"},
    "APPROVED_BASELINE": {"PLANNING"},
    "PLANNING": {"PLAN_READY"},
    "PLAN_READY": {"IMPLEMENTING"},
    "IMPLEMENTING": {"CI_VALIDATION", "NEEDS_HUMAN_INPUT"},
    "CI_VALIDATION": {"STAGING", "IMPLEMENTING", "NEEDS_HUMAN_INPUT"},
    "STAGING": {"UAT", "IMPLEMENTING", "CI_VALIDATION", "NEEDS_HUMAN_INPUT"},
    "UAT": {"EVIDENCE_SYNC", "REWORK", "CI_VALIDATION"},
    "REWORK": {"CI_VALIDATION", "IMPLEMENTING"},
    "EVIDENCE_SYNC": {"DONE_ELIGIBLE", "CI_VALIDATION"},
    "DONE_ELIGIBLE": {"DONE", "CI_VALIDATION"},
    "FAILED": {"ELIGIBLE"},
}
for _s in POST_APPROVAL:  # a material design change always loops back to spec/prototype approval
    TRANSITIONS[_s] = TRANSITIONS[_s] | {"CHANGE_REQUESTED"}

INTERRUPTS = {"PAUSED", "CANCELLED", "FAILED", "CARRYOVER"}


def can_transition(old: str, new: str) -> bool:
    if new in INTERRUPTS:
        return old not in TERMINAL and old != new
    return new in TRANSITIONS.get(old, set())


def transition(db: Session, story: Story, new: str, actor: str, reason: str = "", detail: dict | None = None) -> None:
    old = story.state
    if not can_transition(old, new):
        raise DomainError(409, "INVALID_TRANSITION", f"{story.key}: {old} -> {new} is not allowed")
    if new in ("PAUSED", "CARRYOVER"):
        story.paused_from = old
    story.state = new
    story.revision += 1
    story.updated_at = utcnow()
    d = dict(detail or {})
    if reason:
        d["reason"] = reason
    audit(db, story.tenant_id, story.key, "STATE_CHANGED", actor, old=old, new=new, detail=d)


def resume(db: Session, story: Story, actor: str) -> None:
    if story.state not in ("PAUSED", "CARRYOVER") or not story.paused_from:
        raise DomainError(409, "NOT_PAUSED", f"{story.key} is not paused")
    old, target = story.state, story.paused_from
    story.state, story.paused_from = target, ""
    story.revision += 1
    story.updated_at = utcnow()
    audit(db, story.tenant_id, story.key, "STATE_CHANGED", actor, old=old, new=target, detail={"reason": "resumed"})


# ---------------------------------------------------------------- per-story lease

def acquire_lease(db: Session, story: Story, owner: str, ttl_seconds: int = 600) -> None:
    """Atomic compare-and-set: exactly one active lease per story."""
    now = utcnow()
    res = db.execute(
        update(Story)
        .where(Story.id == story.id, or_(Story.lease_owner.is_(None), Story.lease_expiry < now, Story.lease_owner == owner))
        .values(lease_owner=owner, lease_expiry=now + timedelta(seconds=ttl_seconds))
    )
    if res.rowcount != 1:
        raise DomainError(409, "LEASE_HELD", f"{story.key} is already claimed by another run")
    db.flush()
    db.refresh(story)


def release_lease(db: Session, story: Story, owner: str) -> None:
    db.execute(update(Story).where(Story.id == story.id, Story.lease_owner == owner).values(lease_owner=None, lease_expiry=None))
    db.flush()
    db.refresh(story)
