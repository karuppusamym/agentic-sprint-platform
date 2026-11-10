"""HTTP API (spec section 9). Mutating endpoints return a run id / state and never block on an agent."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import autopilot, delivery, integrations, refine, sync
from .config import ENGINES, validate_engines
from .context import AppContext
from .core import (PERMISSIONS, POST_APPROVAL, SYSTEM, Actor, DomainError, audit, require, resume, transition, utcnow,
                   verify_audit_chain)
from .gates import current_prototype, current_spec, gate_status, latest_source, valid_baseline
from .jira import MockJira
from .knowledge import load_playbooks, note_json, render_notes
from .models import (CheckResult, KnowledgeNote, AgentRun, Annotation, Approval, Artifact, AuditEvent, FeatureSpec, JiraSync, PlanTask, Prototype,
                     SprintSnapshot, Story)
from .specgen import diff_specs

PRIORITY = {"Highest": 0, "High": 1, "Medium": 2, "Low": 3, "Lowest": 4}
STAGES = [("Intake", ["DISCOVERED", "ELIGIBLE", "NEEDS_REFINEMENT", "BLOCKED"]),
          ("Spec & prototype", ["ANALYZING", "SPEC_DRAFT", "SPEC_CHECK", "PROTOTYPE_BUILD", "PROTOTYPE_TEST", "AUTO_REVISE",
                                "HUMAN_REVIEW", "CHANGE_REQUESTED", "NEEDS_HUMAN_INPUT"]),
          ("Implementation", ["APPROVED_BASELINE", "PLANNING", "PLAN_READY", "IMPLEMENTING", "REWORK"]),
          ("Validation", ["CI_VALIDATION", "STAGING", "UAT"]),
          ("Closure", ["EVIDENCE_SYNC", "DONE_ELIGIBLE", "DONE"]),
          ("Stopped", ["PAUSED", "CANCELLED", "FAILED", "CARRYOVER"])]
STAGE_OF = {s: name for name, states in STAGES for s in states}


def iso(dt):
    return dt.isoformat() + "Z" if dt else None


def create_app(ctx: AppContext | None = None) -> FastAPI:
    ctx = ctx or AppContext()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        if not ctx.settings.inline_jobs:
            threading.Thread(target=_background, args=(ctx, stop), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Agentic Sprint Delivery Platform", version="1.0", lifespan=lifespan)
    app.state.ctx = ctx
    app.add_middleware(CORSMiddleware, allow_origins=[ctx.settings.portal_origin], allow_methods=["*"],
                       allow_headers=["*"], allow_credentials=False)

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    def get_db():
        db = ctx.SessionLocal()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_actor(request: Request) -> Actor:
        s = ctx.settings
        bearer = request.headers.get("authorization", "")
        if bearer.lower().startswith("bearer "):  # CI pipelines authenticate with the automation token
            if s.automation_token and hmac.compare_digest(bearer[7:].strip(), s.automation_token):
                return Actor(name="svc-automation", roles=["automation"], human=False)
            raise DomainError(401, "UNAUTHENTICATED", "Invalid automation token")
        if s.auth_mode == "proxy":
            # Behind an SSO reverse proxy that strips client-supplied identity headers.
            name = request.headers.get("x-forwarded-user", "")
            roles = [r.strip() for r in request.headers.get("x-forwarded-groups", "").split(",") if r.strip() in PERMISSIONS["read"]]
            if not name or not roles:
                raise DomainError(401, "UNAUTHENTICATED", "No authenticated identity from the SSO proxy")
            return Actor(name=name, roles=roles, human="automation" not in roles)
        name = request.headers.get("x-user") or request.query_params.get("user") or ""
        user = next((u for u in s.users if u.name == name), None)
        if not user:
            raise DomainError(401, "UNAUTHENTICATED", "Unknown user. Dev auth: send X-User with one of the configured dev users.")
        return Actor(name=user.name, roles=list(user.roles), human=user.human)

    def drive(db: Session) -> None:
        """Let autopilot take the next steps right after an event instead of waiting for the timer."""
        try:  # CI checks that arrived before their stage are evaluated as soon as the stage is reached
            for st in db.execute(select(Story).where(Story.tenant_id == ctx.settings.tenant_id,
                                                     Story.state.in_(("CI_VALIDATION", "STAGING")))).scalars().all():
                integrations.evaluate_checks(ctx, db, st)
        except Exception as e:
            db.rollback()
            print(f"[asdp] check evaluation failed: {e}")
        if ctx.policy.autopilot.enabled:
            try:
                autopilot.tick(ctx, db)
            except Exception as e:  # never fail the user's request because a follow-up step failed
                db.rollback()
                print(f"[asdp] autopilot follow-up failed: {e}")

    def get_story(db: Session, key: str) -> Story:
        s = db.execute(select(Story).where(Story.tenant_id == ctx.settings.tenant_id, Story.key == key)).scalar()
        if not s:
            raise DomainError(404, "NOT_FOUND", f"story {key} not found")
        return s

    def get_run(db: Session, run_id: int) -> AgentRun:
        r = db.get(AgentRun, run_id)
        if not r or db.get(Story, r.story_id).tenant_id != ctx.settings.tenant_id:
            raise DomainError(404, "NOT_FOUND", f"run {run_id} not found")
        return r

    # ------------------------------------------------------------ serializers
    def run_json(db: Session, r: AgentRun, brief: bool = False) -> dict:
        t, s = db.get(PlanTask, r.task_id), db.get(Story, r.story_id)
        d = {"id": r.id, "story": s.key, "task": t.task_key.split("~")[0], "taskKind": t.kind, "engine": r.provider,
             "engineLabel": ctx.providers.providers[r.provider].label, "mode": r.mode, "state": r.state,
             "externalId": r.external_id, "externalUrl": r.external_url, "branch": r.branch, "attempt": r.attempt,
             "cost": r.cost, "traceId": r.trace_id, "prUrl": r.pr_url, "commit": r.commit, "lastEvent": r.last_event,
             "failure": r.failure, "createdBy": r.created_by, "createdAt": iso(r.created_at), "finishedAt": iso(r.finished_at),
             "attended": ctx.providers.providers[r.provider].attended, "allowedPaths": t.owner_paths}
        if brief:
            d["brief"] = r.brief
        return d

    def task_json(t: PlanTask) -> dict:
        return {"id": t.id, "key": t.task_key.split("~")[0], "title": t.title, "kind": t.kind, "deps": t.deps,
                "ownerPaths": t.owner_paths, "engine": t.engine, "priority": t.priority, "status": t.status,
                "blocker": t.blocker, "detail": t.detail}

    def allowed_actions(db: Session, s: Story, actor: Actor) -> dict:
        def can(perm: str) -> bool:
            try:
                require(actor, perm)
                return True
            except DomainError:
                return False
        st = s.state
        has_open = any(t.status in ("PENDING", "WAITING") for t in delivery.story_tasks(db, s))
        return {
            "sync": can("story.sync"),
            "analyze": st == "ELIGIBLE" and can("story.analyze"),
            "revise": bool(s.current_spec_version) and st in ({"HUMAN_REVIEW", "CHANGE_REQUESTED", "NEEDS_HUMAN_INPUT"} | POST_APPROVAL) and can("story.revise"),
            "annotate": bool(s.current_prototype_version) and can("story.annotate"),
            "approve": st == "HUMAN_REVIEW" and can("story.approve"),
            "dispatch": (st == "PLAN_READY" or (st in ("IMPLEMENTING", "REWORK") and has_open)) and can("story.dispatch"),
            "ingestCommit": st in ("CI_VALIDATION", "STAGING", "UAT", "REWORK", "EVIDENCE_SYNC", "DONE_ELIGIBLE", "NEEDS_HUMAN_INPUT") and bool(s.head_commit) and can("commit.ingest"),
            "recordGate": st in ("CI_VALIDATION", "STAGING") and can("gate.record"),
            "uat": st == "UAT" and can("story.uat"),
            "syncEvidence": st == "EVIDENCE_SYNC" and can("evidence.sync"),
            "transition": st == "DONE_ELIGIBLE" and can("story.transition"),
            "pause": st not in ("PAUSED", "DONE", "CANCELLED", "CARRYOVER") and can("story.pause"),
            "resume": st == "PAUSED" and can("story.pause"),
            "cancel": st not in ("DONE", "CANCELLED") and can("story.pause"),
            "reportRun": can("run.report"), "cancelRun": can("run.cancel"),
        }

    def story_summary(db: Session, s: Story) -> dict:
        runs = db.execute(select(AgentRun).where(AgentRun.story_id == s.id, AgentRun.state.in_(delivery.ACTIVE_RUN))).scalars().all()
        queued = [t for t in delivery.story_tasks(db, s) if t.status == "WAITING"]
        blockers = ([s.blocker] if s.blocker else []) + (s.readiness_reasons if s.state in ("NEEDS_REFINEMENT", "BLOCKED", "DISCOVERED") else []) \
            + [f"{t.task_key.split('~')[0]}: {t.blocker}" for t in queued if t.blocker]
        return {"key": s.key, "summary": s.summary, "issueType": s.issue_type, "state": s.state, "stage": STAGE_OF.get(s.state, ""),
                "jiraStatus": s.jira_status, "owner": s.owner, "priority": s.priority, "labels": s.labels,
                "components": s.components, "readiness": s.readiness, "warnings": s.warnings, "blockers": blockers,
                "revision": s.revision, "specVersion": s.current_spec_version, "prototypeVersion": s.current_prototype_version,
                "headCommit": s.head_commit, "prUrl": s.pr_url, "cost": round(s.cost_total, 2), "inActiveSprint": s.in_active_sprint,
                "leased": bool(s.lease_owner), "activeRuns": len(runs), "queuedTasks": len(queued),
                "awaitingManualLaunch": sum(1 for r in runs if r.state == "AWAITING_MANUAL_LAUNCH"),
                "gates": [{"gate": g["gate"], "status": g["status"]} for g in gate_status(db, s)], "updatedAt": iso(s.updated_at)}

    def preview_url(story: Story, version: int) -> str:
        exp = int(time.time()) + ctx.policy.prototype.preview_ttl_seconds
        msg = f"{story.id}.{version}.{exp}"
        sig = hmac.new(ctx.settings.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
        return f"{ctx.settings.public_base_url}/preview/{msg}.{sig}"

    def proto_json(story: Story, p: Prototype, full: bool = True) -> dict:
        d = {"version": p.version, "specVersion": p.spec_version, "sha256": p.sha256, "generator": p.generator,
             "status": p.status, "createdBy": p.created_by, "createdAt": iso(p.created_at), "simulated": True,
             "previewUrl": preview_url(story, p.version), "previewExpiresInSeconds": ctx.policy.prototype.preview_ttl_seconds}
        if full:
            d["manifest"], d["testResult"] = p.manifest, p.test_result
            d["testIds"] = sorted({st["testid"] for j in p.manifest.get("journeys", []) for st in j["steps"] if "testid" in st})
        return d

    def spec_json(sp: FeatureSpec, full: bool = True) -> dict:
        d = {"version": sp.version, "sha256": sp.sha256, "criteriaSha256": sp.criteria_digest, "status": sp.status,
             "createdBy": sp.created_by, "createdAt": iso(sp.created_at), "checkResult": sp.check_result}
        if full:
            d["content"] = sp.content
        return d

    def audit_json(e: AuditEvent) -> dict:
        return {"id": e.id, "subject": e.subject, "event": e.event, "actor": e.actor, "oldState": e.old_state,
                "newState": e.new_state, "detail": e.detail, "timestamp": iso(e.timestamp), "correlationId": e.correlation_id}

    def story_detail(db: Session, s: Story, actor: Actor) -> dict:
        src, spec, proto = latest_source(db, s), current_spec(db, s), current_prototype(db, s)
        baseline = valid_baseline(db, s)
        approvals = db.execute(select(Approval).where(Approval.story_id == s.id).order_by(Approval.id.desc())).scalars().all()
        notes = db.execute(select(Annotation).where(Annotation.story_id == s.id).order_by(Annotation.id.desc())).scalars().all()
        runs = db.execute(select(AgentRun).where(AgentRun.story_id == s.id).order_by(AgentRun.id.desc())).scalars().all()
        specs = db.execute(select(FeatureSpec).where(FeatureSpec.story_id == s.id).order_by(FeatureSpec.version.desc())).scalars().all()
        protos = db.execute(select(Prototype).where(Prototype.story_id == s.id).order_by(Prototype.version.desc())).scalars().all()
        events = db.execute(select(AuditEvent).where(AuditEvent.tenant_id == s.tenant_id, AuditEvent.subject == s.key)
                            .order_by(AuditEvent.id.desc()).limit(150)).scalars().all()
        syncs = db.execute(select(JiraSync).where(JiraSync.story_id == s.id).order_by(JiraSync.id.desc())).scalars().all()
        f = src.fields_json if src else {}
        last_ok = baseline or next((a for a in approvals if a.decision == "APPROVE"), None)
        return {
            **story_summary(db, s),
            "source": {"description": f.get("description", ""), "criteria": f.get("criteria", []), "links": f.get("links", []),
                       "attachments": f.get("attachments", []), "jiraUpdated": f.get("updated", ""),
                       "sourceDigest": src.source_digest if src else "", "criteriaDigest": src.criteria_digest if src else "",
                       "fetchedAt": iso(src.fetched_at) if src else None, "readinessReasons": s.readiness_reasons},
            "spec": spec_json(spec) if spec else None,
            "specVersions": [spec_json(x, full=False) for x in specs],
            "prototype": proto_json(s, proto) if proto else None,
            "prototypeVersions": [proto_json(s, x, full=False) for x in protos],
            "baseline": {"valid": bool(baseline), "approvalId": baseline.id if baseline else None,
                         "approvedSpecVersion": last_ok.spec_version if last_ok else None,
                         "approvedPrototypeVersion": last_ok.prototype_version if last_ok else None},
            "approvals": [{"id": a.id, "decision": a.decision, "actor": a.actor, "roles": a.actor_roles, "comment": a.comment,
                           "specVersion": a.spec_version, "prototypeVersion": a.prototype_version, "specSha256": a.spec_digest,
                           "prototypeSha256": a.prototype_digest, "criteriaSha256": a.criteria_digest, "timestamp": iso(a.timestamp),
                           "inForce": bool(baseline and baseline.id == a.id)} for a in approvals],
            "annotations": [{"id": n.id, "prototypeVersion": n.prototype_version, "selector": n.selector, "comment": n.comment,
                             "author": n.author, "resolved": n.resolved, "createdAt": iso(n.created_at)} for n in notes],
            "tasks": [task_json(t) for t in delivery.story_tasks(db, s)],
            "runs": [run_json(db, r, brief=True) for r in runs],
            "gateDetails": gate_status(db, s),
            "checks": [{"name": c.name, "conclusion": c.conclusion, "url": c.url, "commit": c.commit}
                       for c in db.execute(select(CheckResult).where(CheckResult.story_id == s.id, CheckResult.commit == s.head_commit)).scalars().all()] if s.head_commit else [],
            "traceability": delivery.traceability(db, s),
            "jiraSync": [{"operationKey": j.operation_key, "kind": j.kind, "result": j.result, "remoteId": j.remote_id,
                          "attempt": j.attempt, "error": j.error, "updatedAt": iso(j.updated_at)} for j in syncs],
            "timeline": [audit_json(e) for e in events],
            "allowedActions": allowed_actions(db, s, actor),
            "policy": {"autoRefinementMaxAttempts": ctx.policy.prototype.auto_refinement_max_attempts,
                       "autoRepairMaxAttempts": ctx.policy.agents.auto_repair_max_attempts,
                       "maxPerStoryUsd": ctx.policy.cost.max_per_story_usd, "autoAttempts": s.auto_attempts,
                       "repairAttempts": s.repair_attempts},
        }

    def engines_json(db: Session) -> list[dict]:
        return ctx.providers.matrix(ctx.workspace(db).engines)

    # ------------------------------------------------------------ identity
    api = "/api/v1"

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get(f"{api}/me")
    def me(actor: Actor = Depends(get_actor)):
        return {"name": actor.name, "roles": actor.roles, "human": actor.human, "authMode": ctx.settings.auth_mode,
                "permissions": sorted(p for p, roles in PERMISSIONS.items() if set(actor.roles) & roles)}

    @app.get(f"{api}/users")
    def users():
        if ctx.settings.auth_mode != "dev":
            raise DomainError(404, "NOT_FOUND", "user switching exists only in dev auth mode")
        return [{"name": u.name, "display": u.display, "roles": u.roles, "human": u.human} for u in ctx.settings.users]

    # ------------------------------------------------------------ sprint
    @app.get(f"{api}/sprints/active")
    def active_sprint(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        ws = ctx.workspace(db)
        db.commit()
        snap = db.execute(select(SprintSnapshot).where(SprintSnapshot.workspace_id == ws.id).order_by(SprintSnapshot.fetched_at.desc())).scalar()
        stories = db.execute(select(Story).where(Story.tenant_id == ctx.settings.tenant_id)).scalars().all()
        stories.sort(key=lambda s: (PRIORITY.get(s.priority, 9), s.key))
        stale = not ws.last_sync_at or (utcnow() - ws.last_sync_at).total_seconds() > ctx.policy.jira.reconcile_minutes * 120
        return {"workspace": {"name": ws.name, "project": ws.jira_project, "paused": ws.paused, "pausedReason": ws.paused_reason},
                "sprint": ({"id": snap.jira_sprint_id, "name": snap.name, "state": snap.state, "start": snap.start, "end": snap.end} if snap else None),
                "lastSyncAt": iso(ws.last_sync_at), "stale": stale, "jira": ctx.jira.capabilities(),
                "stages": [{"name": n, "states": st} for n, st in STAGES],
                "queue": {"activeSessions": delivery._active_runs(db, ctx.settings.tenant_id), "maxActiveSessions": ctx.policy.agents.max_active_sessions},
                "engines": engines_json(db),
                "stories": [story_summary(db, s) for s in stories]}

    @app.post(f"{api}/sprints/sync")
    def sprint_sync(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        require(actor, "story.sync")
        res = sync.sync_sprint(ctx, db, actor)
        drive(db)
        return res

    @app.post(f"{api}/webhooks/jira")
    def jira_webhook(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
        secret = ctx.settings.webhook_secret
        if not secret and not ctx.settings.dev_mode:
            raise DomainError(503, "WEBHOOK_NOT_CONFIGURED", "Set ASDP_WEBHOOK_SECRET to accept Jira webhooks")
        if secret and not hmac.compare_digest(request.headers.get("x-webhook-secret", ""), secret):
            raise DomainError(401, "BAD_WEBHOOK_SECRET", "invalid webhook secret")
        return sync.ingest_webhook(ctx, db, payload, SYSTEM)

    # ------------------------------------------------------------ story
    @app.get(f"{api}/stories/{{key}}")
    def story_get(key: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        return story_detail(db, get_story(db, key), actor)

    @app.post(f"{api}/stories/{{key}}/sync")
    def story_sync(key: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        require(actor, "story.sync")
        s = sync.sync_issue(ctx, db, key, actor)
        return {"state": s.state, "revision": s.revision}

    @app.post(f"{api}/stories/{{key}}/analyze", status_code=202)
    def story_analyze(key: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        return refine.analyze(ctx, db, get_story(db, key), actor)

    @app.post(f"{api}/stories/bulk-analyze", status_code=202)
    def bulk_analyze(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        require(actor, "story.analyze")
        started, skipped = [], []
        keys = [s.key for s in db.execute(select(Story).where(Story.tenant_id == ctx.settings.tenant_id, Story.state == "ELIGIBLE")).scalars().all()]
        for k in keys:
            try:
                started.append({"key": k, **refine.analyze(ctx, db, get_story(db, k), actor)})
            except DomainError as e:
                db.rollback()
                skipped.append({"key": k, "reason": e.message})
        return {"started": started, "skipped": skipped}

    @app.get(f"{api}/stories/{{key}}/specs/{{version}}")
    def spec_get(key: str, version: int, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        s = get_story(db, key)
        sp = db.execute(select(FeatureSpec).where(FeatureSpec.story_id == s.id, FeatureSpec.version == version)).scalar()
        if not sp:
            raise DomainError(404, "NOT_FOUND", "spec version not found")
        return spec_json(sp)

    @app.get(f"{api}/stories/{{key}}/specs/{{a}}/diff/{{b}}")
    def spec_diff(key: str, a: int, b: int, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        s = get_story(db, key)
        rows = {v: db.execute(select(FeatureSpec).where(FeatureSpec.story_id == s.id, FeatureSpec.version == v)).scalar() for v in (a, b)}
        if not rows[a] or not rows[b]:
            raise DomainError(404, "NOT_FOUND", "spec version not found")
        return {"from": a, "to": b, "changes": diff_specs(rows[a].content, rows[b].content)}

    @app.post(f"{api}/stories/{{key}}/prototype/revise", status_code=202)
    def proto_revise(key: str, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        html_doc = body.get("prototypeHtml")
        if html_doc is not None and (not isinstance(html_doc, str) or len(html_doc) > 2_000_000):
            raise DomainError(400, "BAD_ARTIFACT", "prototypeHtml must be a string under 2 MB")
        return refine.revise(ctx, db, get_story(db, key), actor, body.get("feedback", ""), kind=body.get("kind", "change"),
                             prototype_html=html_doc, manifest=body.get("manifest"),
                             enrichment=body.get("enrichment"), origin=body.get("origin", "agent"))

    @app.get(f"{api}/stories/{{key}}/prototype/{{version}}")
    def proto_get(key: str, version: int, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        s = get_story(db, key)
        p = db.execute(select(Prototype).where(Prototype.story_id == s.id, Prototype.version == version)).scalar()
        if not p:
            raise DomainError(404, "NOT_FOUND", "prototype version not found")
        return proto_json(s, p)

    @app.get("/preview/{token}")
    def preview(token: str, db: Session = Depends(get_db)):
        """Short-lived signed URL. Served with a locked-down CSP and a sandbox directive so the
        artifact runs in an opaque origin with no network access, wherever it is opened."""
        try:
            sid, version, exp, sig = token.split(".")
            msg = f"{sid}.{version}.{exp}"
            good = hmac.new(ctx.settings.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
            if not hmac.compare_digest(sig, good) or int(exp) < time.time():
                raise ValueError
            p = db.execute(select(Prototype).where(Prototype.story_id == int(sid), Prototype.version == int(version))).scalar()
            if not p:
                raise ValueError
        except ValueError:
            return HTMLResponse("<!doctype html><title>Preview</title><p>This preview link is invalid or has expired. Reload the story to get a fresh one.</p>", status_code=403)
        csp = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
               f"connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors {ctx.settings.portal_origin}; sandbox allow-scripts")
        return HTMLResponse(p.html, headers={"Content-Security-Policy": csp, "X-Content-Type-Options": "nosniff",
                                             "Referrer-Policy": "no-referrer", "Cache-Control": "private, no-store"})

    @app.post(f"{api}/stories/{{key}}/annotations", status_code=201)
    def annotation_add(key: str, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        a = refine.annotate(ctx, db, get_story(db, key), actor, body.get("selector", ""), body.get("comment", ""), body.get("prototypeVersion"))
        return {"id": a.id}

    @app.post(f"{api}/stories/{{key}}/annotations/{{annotation_id}}/resolve")
    def annotation_resolve(key: str, annotation_id: int, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        a = refine.resolve_annotation(ctx, db, get_story(db, key), actor, annotation_id)
        return {"id": a.id, "resolved": a.resolved}

    @app.post(f"{api}/stories/{{key}}/approve")
    def story_approve(key: str, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        res = refine.approve(ctx, db, get_story(db, key), actor, body)
        drive(db)
        return {**res, "state": get_story(db, key).state}

    @app.post(f"{api}/stories/{{key}}/dispatch", status_code=202)
    def story_dispatch(key: str, body: dict = Body(default={}), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        s = get_story(db, key)
        res = delivery.dispatch(ctx, db, s, actor, body.get("engines"))
        return {"state": s.state, **res, "tasks": [task_json(t) for t in delivery.story_tasks(db, s)]}

    @app.get(f"{api}/stories/{{key}}/runs")
    def story_runs(key: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        s = get_story(db, key)
        runs = db.execute(select(AgentRun).where(AgentRun.story_id == s.id).order_by(AgentRun.id.desc())).scalars().all()
        return {"tasks": [task_json(t) for t in delivery.story_tasks(db, s)], "runs": [run_json(db, r, brief=True) for r in runs]}

    @app.post(f"{api}/stories/{{key}}/commits")
    def story_commit(key: str, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        res = delivery.ingest_commit(ctx, db, get_story(db, key), actor, body.get("commit", ""), body.get("prUrl", ""), body.get("source", ""))
        drive(db)
        return res

    @app.post(f"{api}/stories/{{key}}/gates")
    def story_gate(key: str, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        res = delivery.record_ci_gate(ctx, db, get_story(db, key), actor, body.get("gate", ""), body.get("result", ""),
                                      body.get("commit", ""), body.get("summary", ""), body.get("evidence"))
        drive(db)
        return res

    @app.post(f"{api}/stories/{{key}}/uat")
    def story_uat(key: str, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        res = delivery.submit_uat(ctx, db, get_story(db, key), actor, body.get("decision", ""), body.get("commit", ""),
                                  body.get("feedback", ""), bool(body.get("materialDesignChange")))
        drive(db)
        return {**res, "state": get_story(db, key).state}

    @app.post(f"{api}/stories/{{key}}/evidence/sync")
    def story_evidence_sync(key: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        return delivery.sync_evidence(ctx, db, get_story(db, key), actor)

    @app.get(f"{api}/stories/{{key}}/evidence")
    def story_evidence(key: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        s = get_story(db, key)
        arts = db.execute(select(Artifact).where(Artifact.story_id == s.id).order_by(Artifact.id.desc())).scalars().all()
        return {"gates": gate_status(db, s), "traceability": delivery.traceability(db, s),
                "bundle": delivery.build_bundle(db, s) if s.current_prototype_version else None,
                "artifacts": [{"id": a.id, "kind": a.kind, "uri": a.uri, "sha256": a.sha256, "retentionClass": a.retention_class,
                               "createdAt": iso(a.created_at)} for a in arts]}

    @app.get(f"{api}/artifacts/{{artifact_id}}")
    def artifact_get(artifact_id: int, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        a = db.get(Artifact, artifact_id)
        if not a or db.get(Story, a.story_id).tenant_id != ctx.settings.tenant_id:
            raise DomainError(404, "NOT_FOUND", "artifact not found")
        return {"id": a.id, "kind": a.kind, "uri": a.uri, "sha256": a.sha256, "content": a.content, "createdAt": iso(a.created_at)}

    @app.post(f"{api}/stories/{{key}}/transition")
    def story_transition(key: str, body: dict = Body(default={}), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        return delivery.request_transition(ctx, db, get_story(db, key), actor, body.get("target", ""))

    @app.post(f"{api}/stories/{{key}}/pause")
    def story_pause(key: str, body: dict = Body(default={}), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        require(actor, "story.pause")
        s = get_story(db, key)
        transition(db, s, "PAUSED", actor.name, reason=body.get("reason", "paused by operator"))
        db.commit()
        return {"state": s.state}

    @app.post(f"{api}/stories/{{key}}/resume")
    def story_resume(key: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        require(actor, "story.pause")
        s = get_story(db, key)
        if s.state != "PAUSED":
            raise DomainError(409, "NOT_PAUSED", f"{s.key} is {s.state}")
        resume(db, s, actor.name)
        delivery.schedule(ctx, db, s, actor.name)
        db.commit()
        return {"state": s.state}

    @app.post(f"{api}/stories/{{key}}/cancel")
    def story_cancel(key: str, body: dict = Body(default={}), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        require(actor, "story.pause")
        s = get_story(db, key)
        reason = body.get("reason") or "cancelled by operator"
        delivery.cancel_story_runs(ctx, db, s, actor.name, reason)
        transition(db, s, "CANCELLED", actor.name, reason=reason)
        delivery.schedule_all(ctx, db, actor.name)
        db.commit()
        return {"state": s.state}

    # ------------------------------------------------------------ runs
    @app.get(f"{api}/runs")
    def runs_all(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        rows = db.execute(select(AgentRun).join(Story, Story.id == AgentRun.story_id)
                          .where(Story.tenant_id == ctx.settings.tenant_id).order_by(AgentRun.id.desc()).limit(200)).scalars().all()
        waiting = db.execute(select(PlanTask).join(Story, Story.id == PlanTask.story_id)
                             .where(Story.tenant_id == ctx.settings.tenant_id, PlanTask.status == "WAITING")).scalars().all()
        return {"runs": [run_json(db, r, brief=True) for r in rows],
                "queue": [{"story": db.get(Story, t.story_id).key, **task_json(t)} for t in waiting],
                "activeSessions": delivery._active_runs(db, ctx.settings.tenant_id),
                "maxActiveSessions": ctx.policy.agents.max_active_sessions, "engines": engines_json(db)}

    @app.post(f"{api}/runs/{{run_id}}/attach")
    def run_attach(run_id: int, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        return run_json(db, delivery.attach_run(ctx, db, get_run(db, run_id), actor, body.get("url", ""), body.get("externalId", "")))

    @app.post(f"{api}/runs/{{run_id}}/report")
    def run_report(run_id: int, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        try:
            cost = float(body.get("cost") or 0)
        except (TypeError, ValueError):
            raise DomainError(400, "BAD_COST", "cost must be a number")
        if cost < 0:
            raise DomainError(400, "BAD_COST", "cost must not be negative")
        r = delivery.report_run(ctx, db, get_run(db, run_id), actor, body.get("state", ""), commit=body.get("commit", ""),
                                pr_url=body.get("prUrl", ""), cost=cost, failure=body.get("failure", ""))
        drive(db)
        return run_json(db, r)

    @app.post(f"{api}/runs/{{run_id}}/cancel")
    def run_cancel(run_id: int, body: dict = Body(default={}), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        return run_json(db, delivery.cancel_run(ctx, db, get_run(db, run_id), actor, body.get("reason", "")))

    # ------------------------------------------------------------ audit, metrics, admin
    @app.get(f"{api}/audit")
    def audit_list(subject: str = "", limit: int = 200, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        q = select(AuditEvent).where(AuditEvent.tenant_id == ctx.settings.tenant_id)
        if subject:
            q = q.where(AuditEvent.subject == subject)
        rows = db.execute(q.order_by(AuditEvent.id.desc()).limit(min(max(limit, 1), 1000))).scalars().all()
        return {"events": [audit_json(e) for e in rows]}

    @app.get(f"{api}/audit/verify")
    def audit_verify(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        return verify_audit_chain(db, ctx.settings.tenant_id)

    @app.get(f"{api}/metrics/kpis")
    def metrics(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        humans = {u.name for u in ctx.settings.users if u.human}
        return delivery.kpis(db, ctx.settings.tenant_id, humans)

    # ------------------------------------------------------------ inbox, playbooks, wiki
    @app.get(f"{api}/inbox")
    def inbox_get(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        items = autopilot.inbox(ctx, db, actor)
        return {"items": items, "actionable": sum(1 for i in items if i["canAct"]),
                "autopilot": ctx.policy.autopilot.model_dump()}

    @app.get(f"{api}/playbooks")
    def playbooks_get(actor: Actor = Depends(get_actor)):
        mapped = ctx.policy.agents.devin_playbooks
        return {"playbooks": [{**pb, "devinPlaybookId": mapped.get(pb["id"], "")} for pb in load_playbooks().values()],
                "note": "Playbooks are files under playbooks/ and change through pull requests."}

    @app.get(f"{api}/knowledge")
    def knowledge_list(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        rows = db.execute(select(KnowledgeNote).where(KnowledgeNote.tenant_id == ctx.settings.tenant_id)
                          .order_by(KnowledgeNote.status.desc(), KnowledgeNote.id.desc())).scalars().all()
        return {"notes": [note_json(n) for n in rows if n.status != "archived"]}

    @app.get(f"{api}/knowledge/export")
    def knowledge_export(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        """The active wiki as one Markdown document, for pasting into Devin Knowledge or a repo wiki."""
        rows = db.execute(select(KnowledgeNote).where(KnowledgeNote.tenant_id == ctx.settings.tenant_id,
                                                      KnowledgeNote.status == "active").order_by(KnowledgeNote.id)).scalars().all()
        return {"markdown": render_notes(rows), "notes": len(rows)}

    @app.post(f"{api}/knowledge", status_code=201)
    def knowledge_add(body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        require(actor, "knowledge.manage")
        title, text = (body.get("title") or "").strip(), (body.get("body") or "").strip()
        if not title or not text:
            raise DomainError(400, "BAD_NOTE", "A wiki note needs a title and a body")
        if len(text) > 4000:
            raise DomainError(400, "BAD_NOTE", "Keep a wiki note under 4000 characters; split longer guidance")
        tags = [str(t).strip().lower() for t in body.get("tags", []) if str(t).strip()][:10]
        n = KnowledgeNote(tenant_id=ctx.settings.tenant_id, title=title[:200], body=text, tags=tags, source="manual",
                          status="active", created_by=actor.name, created_at=utcnow())
        db.add(n)
        db.flush()
        audit(db, ctx.settings.tenant_id, "wiki", "KNOWLEDGE_ADDED", actor.name, detail={"id": n.id, "title": n.title, "tags": tags})
        db.commit()
        return note_json(n)

    @app.post(f"{api}/knowledge/{{note_id}}/status")
    def knowledge_status(note_id: int, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        require(actor, "knowledge.manage")
        n = db.get(KnowledgeNote, note_id)
        if not n or n.tenant_id != ctx.settings.tenant_id:
            raise DomainError(404, "NOT_FOUND", "note not found")
        status = body.get("status")
        if status not in ("active", "archived"):
            raise DomainError(400, "BAD_STATUS", "status must be active or archived")
        if not actor.human:
            raise DomainError(403, "HUMAN_REQUIRED", "Only a person can decide what becomes standing guidance for agents")
        if body.get("body"):
            n.body = str(body["body"]).strip()[:4000]
        n.status = status
        audit(db, ctx.settings.tenant_id, "wiki", "KNOWLEDGE_" + status.upper(), actor.name, detail={"id": n.id, "title": n.title})
        db.commit()
        return note_json(n)

    @app.get(f"{api}/stories/{{key}}/spec-brief")
    def spec_brief(key: str, engine: str = "devin", db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        return refine.build_spec_brief(ctx, db, get_story(db, key), engine)

    @app.get(f"{api}/stories/{{key}}/prototype-brief")
    def prototype_brief(key: str, engine: str = "copilot_vscode", db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        return refine.build_prototype_brief(ctx, db, get_story(db, key), engine)

    @app.get(f"{api}/stories/{{key}}/test-plan")
    def story_test_plan(key: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        return delivery.test_plan(db, get_story(db, key))

    # ------------------------------------------------------------ Git and CI
    @app.post(f"{api}/webhooks/github")
    async def github_webhook(request: Request):
        raw = await request.body()
        secret = ctx.settings.github_webhook_secret
        if not secret and not ctx.settings.dev_mode:
            raise DomainError(503, "WEBHOOK_NOT_CONFIGURED", "Set ASDP_GITHUB_WEBHOOK_SECRET to accept GitHub webhooks")
        if secret and not integrations.verify_signature(secret, raw, request.headers.get("x-hub-signature-256", "")):
            raise DomainError(401, "BAD_SIGNATURE", "GitHub webhook signature does not match")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            raise DomainError(400, "BAD_WEBHOOK", "payload is not JSON")
        with ctx.SessionLocal() as db:
            res = integrations.handle_github(ctx, db, request.headers.get("x-github-event", ""),
                                             request.headers.get("x-github-delivery", ""), payload)
            drive(db)
            return res

    @app.post(f"{api}/stories/{{key}}/ci/junit")
    async def story_junit(key: str, request: Request, commit: str, gate: str = "G4", runUrl: str = "",
                          actor: Actor = Depends(get_actor)):
        raw = (await request.body()).decode("utf-8", errors="replace")
        with ctx.SessionLocal() as db:
            res = integrations.ingest_junit(ctx, db, get_story(db, key), actor, gate, commit, raw, runUrl)
            drive(db)
            return res

    @app.get(f"{api}/admin/config")
    def admin_config(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        ws = ctx.workspace(db)
        db.commit()
        return {"policy": ctx.policy.model_dump(), "engines": engines_json(db), "jira": ctx.jira.capabilities(),
                "generation": ctx.generation_status,
                "autopilot": ctx.policy.autopilot.model_dump(), "ci": ctx.policy.ci.model_dump(),
                "team": ctx.policy.team.model_dump(),
                "integrations": {"githubWebhookSigned": bool(ctx.settings.github_webhook_secret),
                                 "automationToken": bool(ctx.settings.automation_token)},
                "workspace": {"name": ws.name, "project": ws.jira_project, "paused": ws.paused, "pausedReason": ws.paused_reason},
                "authMode": ctx.settings.auth_mode, "devMode": ctx.settings.dev_mode,
                "database": ctx.settings.database_url.split(":", 1)[0],
                "roles": {p: sorted(r) for p, r in PERMISSIONS.items()},
                "canAdmin": bool(set(actor.roles) & PERMISSIONS["admin"]),
                "mockJira": isinstance(ctx.jira, MockJira) and ctx.settings.dev_mode}

    @app.put(f"{api}/admin/engines")
    def admin_engines(body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        """Enable any subset of devin / copilot_vscode / claude_vscode - but never none."""
        require(actor, "admin")
        ws = ctx.workspace(db)
        merged = ctx.providers.enabled(ws.engines)
        for k, v in body.items():
            if k not in ENGINES:
                raise DomainError(400, "UNKNOWN_ENGINE", f"Unknown engine '{k}'; valid: {', '.join(ENGINES)}")
            merged[k] = bool(v)
        try:
            validate_engines(merged)
        except ValueError as e:
            raise DomainError(409, "NO_ENGINE", str(e))
        ws.engines = merged
        audit(db, ctx.settings.tenant_id, "workspace", "ENGINES_CHANGED", actor.name, detail=merged)
        db.commit()
        return {"engines": engines_json(db)}

    @app.post(f"{api}/admin/pause")
    def admin_pause(body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        """Workspace kill switch: no new analysis or dispatch while paused."""
        require(actor, "admin")
        ws = ctx.workspace(db)
        ws.paused, ws.paused_reason = bool(body.get("paused")), (body.get("reason") or "")[:250]
        audit(db, ctx.settings.tenant_id, "workspace", "WORKSPACE_PAUSED" if ws.paused else "WORKSPACE_RESUMED", actor.name,
              detail={"reason": ws.paused_reason})
        if not ws.paused:
            delivery.schedule_all(ctx, db, actor.name)
        db.commit()
        return {"paused": ws.paused, "reason": ws.paused_reason}

    @app.post(f"{api}/admin/reconcile")
    def admin_reconcile(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
        require(actor, "admin")
        return delivery.reconcile(ctx, db)

    # ------------------------------------------------------------ live events
    @app.get(f"{api}/events/stream")
    async def events(request: Request, after: int = 0, actor: Actor = Depends(get_actor)):
        async def gen():
            last = after
            if not last:
                with ctx.SessionLocal() as db:
                    last = db.execute(select(AuditEvent.id).order_by(AuditEvent.id.desc()).limit(1)).scalar() or 0
            yield "retry: 3000\n\n"
            idle = 0
            while not await request.is_disconnected():
                with ctx.SessionLocal() as db:
                    rows = db.execute(select(AuditEvent).where(AuditEvent.tenant_id == ctx.settings.tenant_id, AuditEvent.id > last)
                                      .order_by(AuditEvent.id).limit(100)).scalars().all()
                for e in rows:
                    last = e.id
                    yield f"id: {e.id}\nevent: audit\ndata: {json.dumps(audit_json(e))}\n\n"
                idle = 0 if rows else idle + 1
                if idle >= 15:
                    idle = 0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------ dev-only mock Jira controls
    if ctx.settings.dev_mode and isinstance(ctx.jira, MockJira):
        mock: MockJira = ctx.jira

        @app.get(f"{api}/dev/mock-jira")
        def mock_state(actor: Actor = Depends(get_actor)):
            return {"sprint": mock.sprint, "failWrites": mock.fail_writes, "failAfterWrite": mock.fail_after_write,
                    "issues": [{"key": i.key, "status": i.status, "labels": i.labels, "criteria": i.acceptance_criteria} for i in mock.issues.values()],
                    "comments": mock.comments}

        @app.post(f"{api}/dev/mock-jira/issues/{{key}}")
        def mock_edit(key: str, body: dict = Body(...), actor: Actor = Depends(get_actor)):
            if key not in mock.issues:
                raise DomainError(404, "NOT_FOUND", f"mock issue {key} not found")
            allowed = {"summary", "description", "acceptance_criteria", "status", "labels", "assignee", "priority", "links"}
            mock.edit(key, **{k: v for k, v in body.items() if k in allowed})
            return {"ok": True}

        @app.post(f"{api}/dev/mock-jira/sprint")
        def mock_sprint(body: dict = Body(...), actor: Actor = Depends(get_actor)):
            mock.close_sprint() if body.get("state") == "closed" else mock.reopen_sprint()
            return {"sprint": mock.sprint}

        @app.post(f"{api}/dev/mock-jira/faults")
        def mock_faults(body: dict = Body(...), actor: Actor = Depends(get_actor)):
            mock.fail_writes = int(body.get("failWrites", 0))
            mock.fail_after_write = int(body.get("failAfterWrite", 0))
            return {"failWrites": mock.fail_writes, "failAfterWrite": mock.fail_after_write}

    return app


def _background(ctx: AppContext, stop: threading.Event) -> None:
    """Periodic Jira reconciliation and provider polling (spec 13.2)."""
    last_sync = 0.0
    while not stop.wait(15):
        try:
            with ctx.SessionLocal() as db:
                if time.time() - last_sync > ctx.policy.jira.reconcile_minutes * 60:
                    last_sync = time.time()
                    sync.sync_sprint(ctx, db, SYSTEM)
                delivery.reconcile(ctx, db)
                autopilot.tick(ctx, db)
        except Exception as e:  # keep the loop alive; the next tick retries
            print(f"[asdp] background tick failed: {e}")


def app_factory() -> FastAPI:
    return create_app()
