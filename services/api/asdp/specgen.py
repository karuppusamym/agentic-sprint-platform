"""Specification generation and deterministic completeness checks (gate G1).

The default generator is rule-based and deterministic so the control plane works with no model
access at all. `SpecGenerator` is the extension point for an LLM-backed generator; whatever
produces the spec, the *checks* below stay deterministic and are the only thing gates trust.
"""
from __future__ import annotations

import re
from typing import Protocol

from .core import sha256_json, sha256_text

VAGUE_TERMS = ["fast", "quick", "quickly", "user-friendly", "user friendly", "intuitive", "appropriate",
               "appropriately", "properly", "as needed", "easy", "easily", "nice", "good", "robust", "seamless",
               "reasonable", "modern", "better", "improve", "improved", "efficient", "works well", "should work",
               "and so on", "simple", "clean"]
PLACEHOLDERS = [r"\btbd\b", r"\btba\b", r"\betc\b\.?", r"\?\?+", r"\bto be decided\b", r"\bto be defined\b"]
INJECTION_PATTERNS = [r"ignore (all )?(previous|prior|above) instructions", r"disregard .{0,30}instructions",
                      r"you are now", r"system prompt", r"mark (this|the) (story|issue|ticket) as done",
                      r"without (running )?tests", r"exfiltrat", r"reveal .{0,20}(secret|token|key)"]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def assess_criterion(text: str) -> list[str]:
    """Return the reasons a criterion is not machine-verifiable (empty list = measurable)."""
    problems, low = [], normalize(text)
    if len(low.split()) < 5:
        problems.append("too short to describe an observable outcome")
    for pat in PLACEHOLDERS:
        if re.search(pat, low):
            problems.append("contains a placeholder (TBD / etc.)")
            break
    vague = [t for t in VAGUE_TERMS if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", low)]
    if vague and not re.search(r"\d", low):
        problems.append(f"subjective wording without a measurable threshold: {', '.join(sorted(set(vague)))}")
    return problems


def scan_injection(texts: list[str]) -> list[str]:
    """Flag instruction-like text in untrusted Jira content. Flags are warnings shown to humans;
    the content is always treated as data and quoted, never obeyed."""
    hits = []
    for t in texts:
        for pat in INJECTION_PATTERNS:
            if re.search(pat, t or "", re.I):
                hits.append(t.strip()[:160])
                break
    return hits


def assign_criterion_ids(criteria: list[str], known: dict[str, str]) -> tuple[list[dict], dict[str, str]]:
    """Stable ids: a criterion keeps its id for as long as its (normalized) wording is unchanged."""
    known = dict(known)
    nxt = max([int(v.split("-")[1]) for v in known.values()] or [0]) + 1
    out = []
    for text in criteria:
        h = sha256_text(normalize(text))[:16]
        if h not in known:
            known[h] = f"AC-{nxt:02d}"
            nxt += 1
        out.append({"id": known[h], "text": text.strip()})
    return out, known


def criteria_digest(criteria: list[dict]) -> str:
    return sha256_json([{"id": c["id"], "text": normalize(c["text"])} for c in criteria])


def parse_gwt(text: str) -> dict:
    m = re.match(r"\s*given\s+(.*?),?\s+when\s+(.*?),?\s+then\s+(.*)$", text.strip(), re.I | re.S)
    if m:
        return {"given": m.group(1).strip(), "when": m.group(2).strip(), "then": m.group(3).strip().rstrip(".")}
    return {"given": "the feature is available", "when": "the user exercises this behaviour", "then": text.strip().rstrip(".")}


def infer_domains(labels: list[str], issue_type: str) -> list[str]:
    order = ["ui", "backend", "data"]
    aliases = {"ux": "ui", "frontend": "ui", "navigation": "ui", "api": "backend", "service": "backend",
               "db": "data", "schema-migration": "data"}
    found = {aliases.get(l.lower(), l.lower()) for l in labels}
    doms = [d for d in order if d in found]
    return doms or ["backend"]


class SpecGenerator(Protocol):
    name: str

    def generate(self, story_key: str, fields: dict, criteria: list[dict], version: int,
                 previous: dict | None, feedback: list[dict]) -> dict: ...


class RuleBasedSpecGenerator:
    name = "rule-based"

    def generate(self, story_key, fields, criteria, version, previous, feedback):
        crit, reqs, rules, errors, scenarios, tests, api = [], [], [], [], [], [], []
        domains = infer_domains(fields.get("labels", []), fields.get("issue_type", "Story"))
        for n, c in enumerate(criteria, 1):
            num = c["id"].split("-")[1]
            g = parse_gwt(c["text"])
            crit.append({**c, **g, "measurable": not assess_criterion(c["text"])})
            reqs.append({"id": f"FR-{num}", "type": "functional", "criterion_ids": [c["id"]],
                         "text": f"When {g['when']}, the system shall ensure that {g['then']}."})
            if re.search(r"\b(must|only|cannot|never|no |not |rejected|exactly|at least|at most)\b", c["text"], re.I):
                rules.append({"id": f"BR-{num}", "source": c["id"], "text": g["then"]})
            errors.append({"id": f"ER-{num}", "criterion_id": c["id"],
                           "text": f"If '{g['when']}' fails or returns no data, the user sees a recoverable error or an explicit empty state; nothing is saved silently."})
            scenarios.append({"id": f"SC-{num}", "criterion_id": c["id"], "title": g["then"][:90],
                              "steps": [f"Given {g['given']}", f"When {g['when']}", f"Then {g['then']}"],
                              "variants": ["success", "empty", "error", "loading"]})
            tests.append({"id": f"TC-{num}-P", "criterion_id": c["id"], "kind": "prototype-journey",
                          "journey_id": f"J-{c['id']}", "description": f"Prototype journey proves the interaction for {c['id']}"})
            tests.append({"id": f"TC-{num}-E", "criterion_id": c["id"], "kind": "staging-e2e",
                          "description": f"Automated end-to-end check on staging: {g['then']}"})
            api.append({"id": f"API-{num}", "criterion_id": c["id"], "method": "POST", "path": f"/mock/{c['id'].lower()}",
                        "variants": ["success", "empty", "error"], "note": "Mock contract used by the prototype; replace with the real contract in the contract task."})
        change_log = list((previous or {}).get("change_log", []))
        for fb in feedback:
            change_log.append({"version": version, **fb})
        assumptions = [{"id": "AS-01", "text": "Jira acceptance criteria are the source of truth; anything not stated there is out of scope."},
                       {"id": "AS-02", "text": "The prototype uses mocked state only and never calls a live or production service."}]
        for i, fb in enumerate([f for f in change_log if f.get("kind") == "assumption"], 3):
            assumptions.append({"id": f"AS-{i:02d}", "text": fb["text"]})
        untrusted = [fields.get("description", "")] + fields.get("comments", []) + [c["text"] for c in criteria]
        return {
            "story_key": story_key, "title": fields.get("summary", ""), "summary": fields.get("description", "")[:2000],
            "issue_type": fields.get("issue_type", "Story"), "domains": domains,
            "labels": list(fields.get("labels", [])), "components": list(fields.get("components", [])),
            "criteria": crit, "requirements": reqs,
            "nonfunctional": [
                {"id": "NFR-01", "text": "Every interactive control is keyboard reachable and has an accessible name."},
                {"id": "NFR-02", "text": "Loading, empty and error states are explicit for every user-visible operation."},
                {"id": "NFR-03", "text": "No secrets, tokens or customer data appear in prototype artifacts, prompts or Jira comments."},
            ],
            "business_rules": rules, "assumptions": assumptions, "error_cases": errors,
            "dependencies": fields.get("links", []),
            "state_model": {"states": ["idle", "loading", "success", "empty", "error"],
                            "transitions": [["idle", "loading"], ["loading", "success"], ["loading", "empty"],
                                            ["loading", "error"], ["error", "loading"], ["success", "idle"]]},
            "api_proposal": api, "scenarios": scenarios, "test_cases": tests,
            "change_log": change_log, "injection_flags": scan_injection(untrusted),
        }


ENRICHMENT_LISTS = ("business_rules", "assumptions", "nonfunctional", "error_cases", "open_questions")
_PREFIX = {"business_rules": "BR", "assumptions": "AS", "nonfunctional": "NFR", "error_cases": "ER", "open_questions": "Q"}
_CRIT_KEY = {"business_rules": "source", "error_cases": "criterion_id"}
RISK_LEVELS = ("low", "medium", "high")


def validate_enrichment(data) -> dict:
    """Shape-check an enrichment produced by an agent (Devin, Claude, Copilot) before it is stored.
    Raises ValueError with a plain message. Text is length-capped; unknown keys are dropped."""
    if not isinstance(data, dict):
        raise ValueError("enrichment must be a JSON object")
    out: dict = {}
    for key in ENRICHMENT_LISTS + ("screens", "code_impact"):
        items = data.get(key, [])
        if not isinstance(items, list) or len(items) > 40 or not all(isinstance(i, dict) for i in items):
            raise ValueError(f"enrichment.{key} must be a list of at most 40 objects")
        out[key] = items
    for key in ENRICHMENT_LISTS:
        out[key] = [{"text": str(i.get("text", ""))[:600], "criterion_id": str(i.get("criterion_id", ""))[:12]} for i in out[key]]
    out["screens"] = [{"name": str(s.get("name", ""))[:80], "purpose": str(s.get("purpose", ""))[:300],
                       "criterion_ids": [str(c)[:12] for c in s.get("criterion_ids", []) if isinstance(c, str)][:20]}
                      for s in out["screens"]]
    out["code_impact"] = [{"path": str(c.get("path", ""))[:200], "change": str(c.get("change", ""))[:400],
                           "risk": c.get("risk") if c.get("risk") in RISK_LEVELS else "medium"} for c in out["code_impact"]]
    if not any(out.values()):
        raise ValueError("enrichment is empty")
    return out


def merge_enrichment(spec: dict, data: dict, origin: str) -> dict:
    """Add an agent's findings to a spec. Criteria and their mapping are never touched, and any
    reference to a criterion id that does not exist is dropped."""
    valid = {c["id"] for c in spec["criteria"]}
    tag = "".join(ch for ch in origin.upper() if ch.isalpha())[:1] or "X"
    for key in ENRICHMENT_LISTS:
        rows = list(spec.get(key, []))
        for item in data.get(key, []) or []:
            text = (item.get("text") or "").strip()
            if not text or any(text.lower() == r["text"].lower() for r in rows):
                continue
            row = {"id": f"{_PREFIX[key]}-{tag}{len(rows) + 1:02d}", "text": text, "origin": origin}
            if key in _CRIT_KEY:
                if item.get("criterion_id") not in valid:
                    continue
                row[_CRIT_KEY[key]] = item["criterion_id"]
            rows.append(row)
        spec[key] = rows
    screens = [{"name": s["name"], "purpose": s.get("purpose", ""), "origin": origin,
                "criterion_ids": [c for c in s.get("criterion_ids", []) if c in valid]}
               for s in data.get("screens", []) or [] if s.get("name")]
    if screens:
        spec["screens"] = screens
    impact = list(spec.get("code_impact", []))
    for c in data.get("code_impact", []) or []:
        path = (c.get("path") or "").strip().replace("\\", "/")
        if not path or ".." in path or path.startswith("/") or any(i["path"] == path for i in impact):
            continue
        impact.append({"path": path, "change": c.get("change", ""), "risk": c.get("risk", "medium"), "origin": origin})
    if impact:
        spec["code_impact"] = impact
    return spec


def check_spec(spec: dict, live_criteria_digest: str) -> dict:
    """Gate G1. Returns failed rule ids, never an overall score."""
    failed = []

    def fail(rule: str, msg: str, ref: str = ""):
        failed.append({"rule": rule, "message": msg, "ref": ref})

    crit = spec.get("criteria", [])
    if not crit:
        fail("S-006", "The story has no acceptance criteria")
    covered_req = {cid for r in spec.get("requirements", []) for cid in r.get("criterion_ids", [])}
    covered_sc = {s["criterion_id"] for s in spec.get("scenarios", [])}
    covered_tc = {t["criterion_id"] for t in spec.get("test_cases", [])}
    for c in crit:
        if c["id"] not in covered_req:
            fail("S-001", "Criterion is not mapped to a requirement", c["id"])
        if c["id"] not in covered_sc:
            fail("S-002", "Criterion has no demo scenario", c["id"])
        if c["id"] not in covered_tc:
            fail("S-003", "Criterion has no test case", c["id"])
        if assess_criterion(c["text"]):
            fail("S-004", "Criterion is not measurable: " + "; ".join(assess_criterion(c["text"])), c["id"])
    if criteria_digest([{"id": c["id"], "text": c["text"]} for c in crit]) != live_criteria_digest:
        fail("S-005", "Spec criteria no longer match the latest Jira snapshot")
    return {"passed": not failed, "failed_rules": failed,
            "rules_evaluated": ["S-001", "S-002", "S-003", "S-004", "S-005", "S-006"]}


def diff_specs(a: dict, b: dict) -> list[dict]:
    """Section-level diff between two spec versions."""
    out = []
    for section in sorted(set(a) | set(b)):
        va, vb = a.get(section), b.get(section)
        if va == vb:
            continue
        if isinstance(va, list) and isinstance(vb, list) and all(isinstance(x, dict) and "id" in x for x in va + vb):
            ia, ib = {x["id"]: x for x in va}, {x["id"]: x for x in vb}
            out.append({"section": section,
                        "added": [ib[k] for k in ib if k not in ia],
                        "removed": [ia[k] for k in ia if k not in ib],
                        "changed": [{"id": k, "from": ia[k], "to": ib[k]} for k in ia if k in ib and ia[k] != ib[k]]})
        else:
            out.append({"section": section, "from": va, "to": vb})
    return out
