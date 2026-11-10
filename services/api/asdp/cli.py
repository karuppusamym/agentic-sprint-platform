"""Command line helper for teams whose only model access is a VS Code chat extension.

    python -m asdp.cli doctor                      check Jira, Devin, GitHub and engine configuration
    python -m asdp.cli pull WF-142 --kind spec     write the chat package into .asdp/WF-142/ of the current repo
    python -m asdp.cli push WF-142 --kind spec     send the chat's answer back to the portal

`pull` and `push` talk to the running API (ASDP_API_URL, default http://localhost:8000) as ASDP_USER.
Nothing here calls a model: the chat in VS Code (Copilot or Claude) or Devin does that.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

from .config import Policy, Settings, load_policy, production_problems
from .jira import JiraCloud, JiraDataCenter, JiraError

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"
KINDS = {"spec": ("spec-brief", "spec-brief.md", "enrichment.json"),
         "prototype": ("prototype-brief", "prototype-brief.md", "prototype.html")}


# ---------------------------------------------------------------- doctor

def doctor(settings: Settings, policy: Policy, offline: bool = False, jira_client: httpx.Client | None = None,
           devin_client: httpx.Client | None = None) -> list[dict]:
    """Configuration and connection checks. Never prints a secret, only whether it is set."""
    rows: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        rows.append({"check": name, "status": status, "detail": detail})

    # models
    if policy.generation.provider == "template":
        add("Model access", OK, "generation.provider=template: the platform calls no model API. Specs and prototypes are "
                                "improved through VS Code chat or Devin (pull / push, or the story page).")
    else:
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        add("Model access", OK if has_key else FAIL,
            "generation.provider=claude and ANTHROPIC_API_KEY is set." if has_key else
            "generation.provider=claude needs the Anthropic SDK and ANTHROPIC_API_KEY. Without API access set generation.provider: template.")

    # engines
    on = [e for e, v in policy.agents.engines.items() if v]
    add("Engines", OK, "enabled: " + ", ".join(on) + f"; primary={policy.agents.primary}, attended={policy.agents.attended_engine}")
    for role, eng in (("primary", policy.agents.primary), ("attended_engine", policy.agents.attended_engine)):
        if not policy.agents.engines.get(eng):
            add(f"Engine {role}", WARN, f"agents.{role}={eng} is not enabled; the platform falls back to another enabled engine.")

    # Devin
    if not policy.agents.engines.get("devin"):
        add("Devin", SKIP, "engine disabled")
    elif not policy.agents.devin_api_enabled:
        add("Devin", OK, "manual mode: no API key needed. A person starts each session from the brief and the pull request "
                         "completes the run. Set agents.devin_api_enabled: true and ASDP_DEVIN_API_KEY to start sessions automatically.")
    elif not settings.devin_api_key:
        add("Devin", FAIL, "agents.devin_api_enabled is true but ASDP_DEVIN_API_KEY is not set.")
    elif offline:
        add("Devin", SKIP, "key is set; connection not tested (--offline)")
    else:
        try:
            c = devin_client or httpx.Client(base_url=settings.devin_api_base, timeout=20,
                                             headers={"Authorization": f"Bearer {settings.devin_api_key}"})
            r = c.get("/sessions", params={"limit": 1})
            if r.status_code in (401, 403):
                add("Devin", FAIL, f"{settings.devin_api_base} rejected the key ({r.status_code}). Check the key and that your plan includes the API.")
            elif r.status_code >= 400:
                add("Devin", WARN, f"{settings.devin_api_base} answered {r.status_code} to a session list; the key was not rejected "
                                   "but the endpoint may differ for your organisation (ASDP_DEVIN_API_BASE).")
            else:
                add("Devin", OK, f"key accepted by {settings.devin_api_base}")
        except httpx.HTTPError as e:
            add("Devin", FAIL, f"cannot reach {settings.devin_api_base}: {e}. Check the proxy / firewall.")

    # Jira
    src = policy.jira.source
    if src == "mock":
        add("Jira", WARN, "jira.source=mock: sample stories only, no real Jira is contacted. Set jira.source to cloud or datacenter.")
    else:
        need = {"cloud": ["jira_base_url", "jira_email", "jira_api_token"], "datacenter": ["jira_base_url", "jira_api_token"]}.get(src, [])
        missing = ["ASDP_" + n.upper() for n in need if not getattr(settings, n)]
        if missing:
            add("Jira", FAIL, f"jira.source={src} needs " + ", ".join(missing))
        elif offline:
            add("Jira", SKIP, "credentials are set; connection not tested (--offline)")
        else:
            try:
                j = (JiraCloud(settings.jira_base_url, settings.jira_email, settings.jira_api_token, settings.jira_sprint_field,
                               settings.jira_criteria_field, policy.jira.write_enabled, client=jira_client) if src == "cloud" else
                     JiraDataCenter(settings.jira_base_url, settings.jira_api_token, settings.jira_sprint_field,
                                    settings.jira_criteria_field, policy.jira.write_enabled, client=jira_client))
                add("Jira sign-in", OK, f"authenticated as {j.whoami()}")
                issues = j.search_active_sprint(policy.jira.discovery_jql)
                add("Jira sprint query", OK if issues else WARN,
                    f"{len(issues)} issue(s) from: {policy.jira.discovery_jql}" + ("" if issues else " (no open sprint, or the JQL / project is wrong)"))
                if issues:
                    with_sprint = sum(1 for i in issues if i.sprint)
                    add("Jira sprint field", OK if with_sprint else FAIL,
                        f"{with_sprint}/{len(issues)} issues carry a sprint in {settings.jira_sprint_field}" +
                        ("" if with_sprint else ". Set ASDP_JIRA_SPRINT_FIELD to your Sprint custom field id."))
                    with_ac = sum(1 for i in issues if i.acceptance_criteria)
                    add("Jira acceptance criteria", OK if with_ac else WARN,
                        f"{with_ac}/{len(issues)} issues have criteria the platform can read" +
                        ("" if with_ac else ". Put them under an 'Acceptance Criteria' heading in the description, "
                                            "or set ASDP_JIRA_CRITERIA_FIELD to the custom field id."))
                    label = policy.jira.require_label
                    ready = sum(1 for i in issues if i.status in policy.jira.eligible_statuses and (not label or label in i.labels))
                    add("Jira eligible stories", OK if ready else WARN,
                        f"{ready} issue(s) are in {policy.jira.eligible_statuses}" + (f" with label '{label}'" if label else ""))
                add("Jira write-back", OK if policy.jira.write_enabled else WARN,
                    "enabled: evidence comments and the Done transition are written" if policy.jira.write_enabled else
                    "jira.write_enabled=false: read only. Turn it on once reading is confirmed.")
            except JiraError as e:
                add("Jira", FAIL, f"{settings.jira_base_url}: {str(e)[:240]}")
            except Exception as e:  # DomainError from a bad configuration
                add("Jira", FAIL, str(getattr(e, "message", e))[:240])

    # Git and CI
    add("GitHub webhook", OK if settings.github_webhook_secret else WARN,
        "ASDP_GITHUB_WEBHOOK_SECRET is set" if settings.github_webhook_secret else
        "ASDP_GITHUB_WEBHOOK_SECRET is not set: pull requests and check runs will not complete runs or gates automatically "
        "(a person reports them in the portal instead).")
    add("CI token", OK if settings.automation_token else WARN,
        "ASDP_AUTOMATION_TOKEN is set" if settings.automation_token else
        "ASDP_AUTOMATION_TOKEN is not set: CI cannot upload JUnit results.")

    # security posture
    for problem in production_problems(settings):  # the server will not start with these
        add("Production settings", FAIL, problem)
    if settings.dev_mode and settings.secret == "dev-only-change-me":
        add("Signing secret", WARN, "ASDP_SECRET is the built-in default; set a long random value before sharing the portal.")
    if settings.dev_mode and settings.auth_mode == "dev":
        add("Sign-in", WARN, "auth_mode=dev trusts the X-User header. Fine on one machine; put it behind your SSO proxy "
                             "(ASDP_AUTH_MODE=proxy, ASDP_DEV_MODE=false) before others use it.")
    add("Team file", OK if Path(settings.users_path).exists() else WARN,
        f"{len(settings.users)} user(s) from {settings.users_path}" if Path(settings.users_path).exists() else
        "config/users.yaml not found: the built-in demo users are active.")
    return rows


def print_rows(rows: list[dict]) -> int:
    for r in rows:
        print(f"[{r['status']:<4}] {r['check']}: {r['detail']}")
    fails = sum(1 for r in rows if r["status"] == FAIL)
    print(f"\n{fails} failed, {sum(1 for r in rows if r['status'] == WARN)} warning(s).")
    return 1 if fails else 0


# ---------------------------------------------------------------- pull / push

def api_client() -> httpx.Client:
    return httpx.Client(base_url=os.environ.get("ASDP_API_URL", "http://localhost:8000").rstrip("/") + "/api/v1", timeout=60,
                        headers={"X-User": os.environ.get("ASDP_USER", "")})


def _check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:300]
        raise SystemExit(f"API {r.status_code}: {msg}")
    return r.json()


def pull(client: httpx.Client, key: str, kind: str, engine: str, out: Path) -> Path:
    """Write the chat package for a story into <repo>/.asdp/<KEY>/ so the chat can read it as a file."""
    folder = out / key
    folder.mkdir(parents=True, exist_ok=True)
    if kind == "task":
        runs = [r for r in _check(client.get(f"/stories/{key}/runs"))["runs"] if r["state"] == "AWAITING_MANUAL_LAUNCH"]
        if not runs:
            raise SystemExit(f"{key} has no task waiting to be launched")
        for r in runs:
            (folder / f"task-{r['task']}.md").write_text(r.get("brief", ""), encoding="utf-8")
        return folder
    route, name, _ = KINDS[kind]
    d = _check(client.get(f"/stories/{key}/{route}", params={"engine": engine}))
    path = folder / name
    path.write_text(d["brief"], encoding="utf-8")
    return path


def extract(text: str, kind: str):
    """Chat answers often wrap the result in a code fence or add a sentence around it."""
    m = re.search(r"```(?:json|html)?\s*\n(.*?)```", text, re.S)
    body = (m.group(1) if m else text).strip()
    if kind == "prototype":
        i = body.lower().find("<!doctype")
        return body[i:] if i > 0 else body
    try:
        return json.loads(body)
    except ValueError:
        a, b = body.find("{"), body.rfind("}")
        if a < 0 or b <= a:
            raise SystemExit("The file does not contain a JSON object")
        try:
            return json.loads(body[a:b + 1])
        except ValueError as e:
            raise SystemExit(f"The JSON in the file is not valid: {e}")


def push(client: httpx.Client, key: str, kind: str, engine: str, file: Path, note: str = "") -> dict:
    if not file.is_file():
        raise SystemExit(f"{file} not found")
    data = extract(file.read_text(encoding="utf-8"), kind)
    body = ({"prototypeHtml": data, "feedback": note or f"Prototype written in {engine}"} if kind == "prototype"
            else {"enrichment": data, "origin": engine, "feedback": note})
    return _check(client.post(f"/stories/{key}/prototype/revise", json=body))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="asdp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor", help="check configuration and connections")
    d.add_argument("--offline", action="store_true", help="check configuration only; contact nothing")
    for name in ("pull", "push"):
        p = sub.add_parser(name)
        p.add_argument("key")
        p.add_argument("--kind", choices=["spec", "prototype"] + (["task"] if name == "pull" else []), default="spec")
        p.add_argument("--engine", default="copilot_vscode")
        p.add_argument("--dir", default=".asdp", help="folder inside the repository (default .asdp)")
        if name == "push":
            p.add_argument("--file", default="")
            p.add_argument("--note", default="")
    a = ap.parse_args(argv)
    if a.cmd == "doctor":
        s = Settings.from_env()
        return print_rows(doctor(s, load_policy(s.policy_path), offline=a.offline))
    with api_client() as c:
        if a.cmd == "pull":
            print(f"Wrote {pull(c, a.key, a.kind, a.engine, Path(a.dir))}")
            if a.kind != "task":
                print(f"In the VS Code chat: follow {a.dir}/{a.key}/{KINDS[a.kind][1]} and save the result as "
                      f"{a.dir}/{a.key}/{KINDS[a.kind][2]}. Then run: python -m asdp.cli push {a.key} --kind {a.kind}")
        else:
            file = Path(a.file) if a.file else Path(a.dir) / a.key / KINDS[a.kind][2]
            print(f"Submitted: {push(c, a.key, a.kind, a.engine, file, a.note)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
