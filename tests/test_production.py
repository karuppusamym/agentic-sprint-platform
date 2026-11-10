"""Production hardening: audit chain under concurrency, startup safety checks, SSO proxy sign-in,
request size limits, recovery after a restart, and pull requests from forks."""
import threading
import time

import pytest
from sqlalchemy import select

from asdp.config import Settings, production_problems
from asdp.core import audit, verify_audit_chain
from asdp.models import Story

from conftest import COMMIT_A
from test_scale import gh, pr

KEY = "WF-142"
LONG = "x" * 40


def test_audit_chain_stays_intact_with_concurrent_writers(h):
    """A second writer that starts while the first has an uncommitted event must link to that event,
    not to the one before it."""
    first = h.ctx.SessionLocal()
    audit(first, "default", "X", "FIRST", "t")  # holds the write lock, not committed yet

    def second():
        with h.ctx.SessionLocal() as db:
            audit(db, "default", "X", "SECOND", "t")
            db.commit()

    t = threading.Thread(target=second)
    t.start()
    time.sleep(0.5)
    first.commit()
    first.close()
    t.join(timeout=30)
    with h.ctx.SessionLocal() as db:
        assert verify_audit_chain(db, "default")["ok"] is True


def test_server_refuses_unsafe_shared_settings():
    assert production_problems(Settings()) == []  # dev mode on one machine is allowed
    bad = production_problems(Settings(dev_mode=False))
    assert len(bad) == 2 and "ASDP_SECRET" in bad[0] and "ASDP_AUTH_MODE" in bad[1]
    no_proxy_secret = production_problems(Settings(dev_mode=False, secret=LONG, auth_mode="proxy"))
    assert len(no_proxy_secret) == 1 and "ASDP_PROXY_SECRET" in no_proxy_secret[0]
    assert production_problems(Settings(dev_mode=False, secret=LONG, auth_mode="proxy", proxy_secret="p" * 20)) == []


def test_auth_mode_typo_never_falls_back_to_dev_sign_in(monkeypatch, tmp_path):
    monkeypatch.setenv("ASDP_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("ASDP_AUTH_MODE", "Proxy")
    assert Settings.from_env().auth_mode == "proxy"
    monkeypatch.setenv("ASDP_AUTH_MODE", "sso")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_proxy_sign_in_needs_the_proxy_secret_and_tolerates_a_forwarded_bearer(h):
    s = h.ctx.settings
    s.auth_mode, s.proxy_secret, s.automation_token = "proxy", "proxy-shared-value", "ci-token-value"
    who = {"X-Forwarded-User": "maria", "X-Forwarded-Groups": "developer,unknown-group"}
    # reached directly, without the proxy's secret: refused even with identity headers
    assert h.client.get("/api/v1/me", headers=who).status_code == 401
    ok = h.client.get("/api/v1/me", headers={**who, "X-Proxy-Secret": "proxy-shared-value"})
    assert ok.status_code == 200 and ok.json()["name"] == "maria" and ok.json()["roles"] == ["developer"]
    # an SSO proxy that forwards the user's own bearer token must not lock everyone out
    fwd = h.client.get("/api/v1/me", headers={**who, "X-Proxy-Secret": "proxy-shared-value", "Authorization": "Bearer user-jwt"})
    assert fwd.status_code == 200 and fwd.json()["name"] == "maria"
    # the CI token still works and is never a person
    ci = h.client.get("/api/v1/me", headers={"Authorization": "Bearer ci-token-value"})
    assert ci.json()["human"] is False
    # the dev user header means nothing in proxy mode
    assert h.client.get("/api/v1/me", headers={"X-User": "lena", "X-Proxy-Secret": "proxy-shared-value"}).status_code == 401


def test_wrong_ci_token_is_refused_in_dev_sign_in(h):
    h.ctx.settings.automation_token = "ci-token-value"
    r = h.client.get("/api/v1/me", headers={"Authorization": "Bearer nope", "X-User": "lena"})
    assert r.status_code == 401


def test_oversized_bodies_are_refused(h):
    h.ctx.settings.max_body_bytes = 2000
    r = h.call("POST", f"/stories/{KEY}/ci/junit?commit={COMMIT_A}", "svc-automation", content="<x>" + "a" * 5000 + "</x>")
    assert r.status_code == 413 and r.json()["error"]["code"] == "BODY_TOO_LARGE"


def test_readiness_checks_the_database(h):
    assert h.client.get("/healthz").json() == {"ok": True}
    assert h.client.get("/readyz").json() == {"ok": True, "database": "ok"}


def test_story_orphaned_by_a_restart_fails_visibly_and_can_be_retried(h):
    h.sync()
    with h.ctx.SessionLocal() as db:  # what a restart mid-refinement leaves behind: transient state, no job, no lease
        s = db.execute(select(Story).where(Story.key == KEY)).scalar_one()
        s.state = "SPEC_CHECK"
        db.commit()
    h.err("POST", f"/stories/{KEY}/retry", 409, "NOT_FAILED", "dan")
    assert h.ok("POST", "/admin/reconcile", "lena").get("orphans_failed") == 1
    s = h.story(KEY, "dan")
    assert s["state"] == "FAILED" and "interrupted" in s["blockers"][0] and s["allowedActions"]["retry"] is True
    assert h.story(KEY, "quinn")["allowedActions"]["retry"] is False  # needs the analyze permission
    assert h.ok("POST", f"/stories/{KEY}/retry", "dan")["state"] == "ELIGIBLE"
    h.ok("POST", f"/stories/{KEY}/analyze", "dan")
    assert h.story(KEY)["state"] == "HUMAN_REVIEW"
    assert h.ok("GET", "/audit/verify")["ok"] is True


def test_a_running_refinement_is_not_treated_as_orphaned(h):
    h.sync()
    with h.ctx.SessionLocal() as db:
        s = db.execute(select(Story).where(Story.key == KEY)).scalar_one()
        s.state, s.lease_owner = "SPEC_CHECK", "run-live"
        from asdp.core import utcnow
        from datetime import timedelta
        s.lease_expiry = utcnow() + timedelta(minutes=5)
        db.commit()
    assert "orphans_failed" not in h.ok("POST", "/admin/reconcile", "lena")
    assert h.story(KEY)["state"] == "SPEC_CHECK"


def test_pull_request_from_a_fork_never_moves_a_story(h):
    h.to_plan_ready(KEY)
    h.ok("POST", f"/stories/{KEY}/dispatch", "lena")
    run = h.story(KEY)["runs"][0]
    payload = pr(run["branch"], COMMIT_A)
    payload["pull_request"]["head"]["repo"] = {"full_name": "stranger/product"}
    payload["pull_request"]["base"] = {"repo": {"full_name": "office/product"}}
    res = gh(h, "pull_request", payload).json()
    assert res["handled"] is False and "fork" in res["reason"]
    s = h.story(KEY)
    assert s["state"] == "IMPLEMENTING" and s["headCommit"] == ""
    payload["pull_request"]["head"]["repo"] = {"full_name": "office/product"}  # same repository: accepted
    assert gh(h, "pull_request", payload).json()["result"] == "run_completed"
