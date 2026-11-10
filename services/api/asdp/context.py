"""Application context: wiring of settings, policy, database and adapters."""
from __future__ import annotations

import threading
from typing import Callable

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import Policy, Settings, load_policy
from .jira import JiraCloud, JiraDataCenter, MockJira
from .knowledge import seed_wiki
from .llm import build_generators
from .models import Base, Workspace
from .prototype import FaultInjectingGenerator, TemplateGenerator
from .providers import ProviderRegistry
from .specgen import RuleBasedSpecGenerator


class AppContext:
    def __init__(self, settings: Settings | None = None, policy: Policy | None = None, jira=None, llm=None):
        self.settings = settings or Settings.from_env()
        self.policy = policy or load_policy(self.settings.policy_path)
        url = self.settings.database_url
        kw: dict = {}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                kw["poolclass"] = StaticPool
        self.engine = create_engine(url, **kw)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(self.engine, expire_on_commit=False)
        if jira is not None:
            self.jira = jira
        elif self.policy.jira.source == "cloud":
            s = self.settings
            self.jira = JiraCloud(s.jira_base_url, s.jira_email, s.jira_api_token, s.jira_sprint_field,
                                  s.jira_criteria_field, self.policy.jira.write_enabled)
        elif self.policy.jira.source == "datacenter":
            s = self.settings
            self.jira = JiraDataCenter(s.jira_base_url, s.jira_api_token, s.jira_sprint_field,
                                       s.jira_criteria_field, self.policy.jira.write_enabled)
        else:
            self.jira = MockJira()
        self.providers = ProviderRegistry(self.settings, self.policy)
        self.spec_generator, self.proto_generator, self.generation_status = build_generators(
            self.policy, RuleBasedSpecGenerator(), TemplateGenerator(), llm)
        with self.SessionLocal() as db:
            seed_wiki(db, self.settings.tenant_id)
            db.commit()
        if self.settings.dev_mode:
            self.proto_generator = FaultInjectingGenerator(self.proto_generator)

    def workspace(self, db: Session) -> Workspace:
        ws = db.execute(select(Workspace).where(Workspace.tenant_id == self.settings.tenant_id)).scalar()
        if ws is None:
            ws = Workspace(tenant_id=self.settings.tenant_id, name=f"{self.policy.jira.project} delivery",
                           jira_project=self.policy.jira.project, policy_version=self.policy.version, engines={})
            db.add(ws)
            db.flush()
        return ws

    def submit(self, fn: Callable, *args) -> None:
        """Run a workflow job. Mutating endpoints return immediately with a run id; the job
        continues here (in-process thread by default, inline for tests)."""
        if self.settings.inline_jobs:
            fn(self, *args)
        else:
            threading.Thread(target=fn, args=(self, *args), daemon=True).start()
