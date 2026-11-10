"""Test harness: a real app on a temp-file SQLite database with the in-process mock Jira."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from asdp.config import Policy, Settings  # noqa: E402
from asdp.context import AppContext  # noqa: E402
from asdp.jira import MockJira  # noqa: E402
from asdp.main import create_app  # noqa: E402

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
CI_EVIDENCE = {"ci_run_url": "https://ci.example/run/1", "tests_passed": 42, "tests_failed": 0}


class Harness:
    def __init__(self, tmp_path, policy: Policy | None = None, dev_mode: bool = True, llm=None):
        self.jira = MockJira()
        self.default_user = "lena"
        settings = Settings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}", inline_jobs=True, dev_mode=dev_mode)
        self.ctx = AppContext(settings=settings, policy=policy or Policy(), jira=self.jira, llm=llm)
        self.client = TestClient(create_app(self.ctx))

    def call(self, method: str, path: str, user: str = "", **kw):
        return self.client.request(method, "/api/v1" + path, headers={"X-User": user or self.default_user}, **kw)

    def ok(self, method: str, path: str, user: str = "", **kw) -> dict:
        r = self.call(method, path, user, **kw)
        assert r.status_code < 300, f"{method} {path} as {user} -> {r.status_code} {r.text}"
        return r.json()

    def err(self, method: str, path: str, status: int, code: str, user: str = "", **kw) -> dict:
        r = self.call(method, path, user, **kw)
        assert r.status_code == status, f"{method} {path} as {user} -> {r.status_code} {r.text}"
        body = r.json()["error"]
        assert body["code"] == code, body
        return body

    def story(self, key: str, user: str = "") -> dict:
        return self.ok("GET", f"/stories/{key}", user)

    def gates(self, key: str) -> dict:
        return {g["gate"]: g["status"] for g in self.story(key)["gates"]}

    # ---- flow helpers
    def sync(self):
        return self.ok("POST", "/sprints/sync", "dan")

    def to_review(self, key: str) -> dict:
        self.sync()
        self.ok("POST", f"/stories/{key}/analyze", "dan")
        s = self.story(key)
        assert s["state"] == "HUMAN_REVIEW", s["state"]
        return s

    def approval_body(self, key: str, decision: str = "APPROVE", comment: str = "Approved scenarios") -> dict:
        s = self.story(key)
        return {"decision": decision, "specVersion": s["spec"]["version"], "specSha256": s["spec"]["sha256"],
                "prototypeVersion": s["prototype"]["version"], "prototypeSha256": s["prototype"]["sha256"],
                "criteriaSha256": s["spec"]["criteriaSha256"], "reviewComment": comment, "expectedStoryRevision": s["revision"]}

    def to_plan_ready(self, key: str) -> dict:
        self.to_review(key)
        self.ok("POST", f"/stories/{key}/approve", "priya", json=self.approval_body(key))
        s = self.story(key)
        assert s["state"] == "PLAN_READY", s["state"]
        return s

    def finish_runs(self, key: str, commit: str = COMMIT_A, max_rounds: int = 10) -> None:
        """Play the developer: attach + report every launched run until implementation completes."""
        for _ in range(max_rounds):
            s = self.story(key)
            active = [r for r in s["runs"] if r["state"] in ("AWAITING_MANUAL_LAUNCH", "RUNNING")]
            if not active:
                return
            for r in active:
                if r["state"] == "AWAITING_MANUAL_LAUNCH" and not r["attended"]:
                    self.ok("POST", f"/runs/{r['id']}/attach", "dan", json={"url": f"https://app.devin.ai/sessions/s{r['id']}"})
                self.ok("POST", f"/runs/{r['id']}/report", "dan",
                        json={"state": "SUCCEEDED", "commit": commit, "prUrl": f"https://git.example/pr/{r['id']}", "cost": 2.0})
        raise AssertionError("runs did not finish")

    def to_ci(self, key: str, commit: str = COMMIT_A) -> dict:
        self.to_plan_ready(key)
        self.ok("POST", f"/stories/{key}/dispatch", "lena")
        self.finish_runs(key, commit)
        s = self.story(key)
        assert s["state"] == "CI_VALIDATION", s["state"]
        return s

    def gate(self, key: str, gate: str, result: str = "PASS", commit: str = COMMIT_A, user: str = "svc-automation"):
        return self.ok("POST", f"/stories/{key}/gates", user,
                       json={"gate": gate, "result": result, "commit": commit, "summary": f"{gate} {result}", "evidence": CI_EVIDENCE})

    def to_uat(self, key: str, commit: str = COMMIT_A) -> dict:
        self.to_ci(key, commit)
        for g in ("G4", "G5", "G6"):
            self.gate(key, g, commit=commit)
        s = self.story(key)
        assert s["state"] == "UAT", s["state"]
        return s

    def to_done_eligible(self, key: str, commit: str = COMMIT_A) -> dict:
        self.to_uat(key, commit)
        self.ok("POST", f"/stories/{key}/uat", "quinn", json={"decision": "PASS", "commit": commit})
        self.ok("POST", f"/stories/{key}/evidence/sync", "dan")
        s = self.story(key)
        assert s["state"] == "DONE_ELIGIBLE", s["state"]
        return s


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


@pytest.fixture
def make_harness(tmp_path):
    def _make(policy: Policy | None = None, **kw):
        return Harness(tmp_path, policy, **kw)
    return _make
