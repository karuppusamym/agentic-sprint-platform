"""Playable prototype generation and deterministic checks (gate G2).

Prototype contract (applies to generated AND uploaded artifacts):
  * one self-contained HTML document, no external network references
  * screens are elements with  data-screen="<id>"; exactly one has data-start
  * navigation controls carry  data-nav="<screen id>"
  * testable controls carry    data-testid="<id>"
  * scenario result regions expose each mocked state as  data-state="success|empty|error|loading"
  * a visible element with data-simulated marks the artifact as simulated behaviour

The checker walks every mandatory journey of the test manifest over the parsed DOM. It is
deterministic and cheap; a real-browser Playwright run is an additional, optional layer
(policy prototype.require_browser_pass) that needs a provisioned runner.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Protocol

REQUIRED_STATES = ["success", "empty", "error", "loading"]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SECRET_PATTERNS = [r"AKIA[0-9A-Z]{16}", r"ghp_[A-Za-z0-9]{30,}", r"xox[baprs]-[A-Za-z0-9-]{10,}",
                   r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"sk-[A-Za-z0-9_-]{24,}",
                   r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{12,}['\"]"]
NETWORK_CALLS = [r"\bfetch\s*\(", r"XMLHttpRequest", r"\bWebSocket\s*\(", r"\bEventSource\s*\(", r"sendBeacon",
                 r"\bimport\s*\(", r"window\.open\s*\(", r"location\.(href|assign|replace)"]


class Generator(Protocol):
    name: str

    def generate(self, spec: dict, attempt: int, failed_rules: list[dict]) -> tuple[str, dict]: ...


def build_manifest(spec: dict) -> dict:
    journeys = []
    for c in spec["criteria"]:
        cid = c["id"]
        journeys.append({
            "id": f"J-{cid}", "criterion_id": cid, "mandatory": True, "title": c.get("then", c["text"])[:90],
            "steps": [
                {"op": "start"},
                {"op": "click", "testid": f"nav-{cid}"},
                {"op": "expect_screen", "screen": f"sc-{cid}"},
                {"op": "click", "testid": f"{cid}-action"},
                {"op": "expect_states", "testid": f"{cid}-result", "states": REQUIRED_STATES},
                {"op": "click", "testid": f"{cid}-back"},
                {"op": "expect_screen", "screen": "home"},
            ]})
    return {"start_screen": "home", "journeys": journeys,
            "scenarios": [{"id": s["id"], "criterion_id": s["criterion_id"], "title": s["title"],
                           "variants": s["variants"]} for s in spec.get("scenarios", [])]}


_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2430;--mute:#5b6675;--line:#d9dee6;--accent:#2f5bd8;--ok:#1a7f4b;--warn:#a15c00;--err:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1b212a;--ink:#e8ecf2;--mute:#9aa6b6;--line:#2c3542;--accent:#7da2ff;--ok:#5fd093;--warn:#f0b35a;--err:#ff8a80}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
header{display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--card)}
header h1{font-size:16px;margin:0;flex:1}
.badge{font-size:11px;font-weight:700;letter-spacing:.06em;padding:3px 8px;border-radius:999px;border:1px solid var(--warn);color:var(--warn)}
main{max-width:820px;margin:0 auto;padding:16px}
[data-screen]{display:none}[data-screen].on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:12px}
button,select,input{font:inherit;padding:8px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
button{cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.list button{display:block;width:100%;text-align:left;margin-bottom:8px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}
.gwt{margin:0;padding-left:18px;color:var(--mute)}
[data-state]{display:none;padding:12px;border-radius:8px;border:1px solid var(--line);margin-top:10px}
[data-state].on{display:block}
[data-state=success]{border-color:var(--ok)}[data-state=error]{border-color:var(--err)}[data-state=empty]{border-style:dashed}
.done{color:var(--ok);font-weight:600}.mute{color:var(--mute)}label{font-weight:600}
"""

_JS = """
(function(){
  var visited={};
  function show(id){
    document.querySelectorAll('[data-screen]').forEach(function(s){s.classList.toggle('on',s.getAttribute('data-screen')===id)});
    var h=document.querySelector('[data-screen="'+id+'"] h2'); if(h){h.setAttribute('tabindex','-1');h.focus();}
  }
  function setState(cid,state){
    var box=document.querySelector('[data-testid="'+cid+'-result"]');
    box.querySelectorAll('[data-state]').forEach(function(s){s.classList.toggle('on',s.getAttribute('data-state')===state)});
  }
  document.addEventListener('click',function(e){
    var nav=e.target.closest('[data-nav]');
    if(nav){show(nav.getAttribute('data-nav'));return;}
    var act=e.target.closest('[data-action]');
    if(act){
      var cid=act.getAttribute('data-action');
      var variant=document.querySelector('[data-testid="'+cid+'-variant"]').value;
      setState(cid,'loading');
      setTimeout(function(){
        setState(cid,variant);
        if(variant==='success'){visited[cid]=true;var m=document.querySelector('[data-testid="mark-'+cid+'"]');if(m){m.textContent='exercised';m.className='done';}}
      },450);
    }
  });
  show('home');
})();
"""


class TemplateGenerator:
    """Deterministic generator: one interactive scenario screen per acceptance criterion,
    with selectable mocked outcomes. Richer prototypes can be uploaded as a revision
    (POST /prototype/revise with prototypeHtml) and go through the same checks."""
    name = "template"

    def generate(self, spec, attempt, failed_rules):
        e = html.escape
        crit = spec["criteria"]
        nav = "".join(
            f'<button data-testid="nav-{c["id"]}" data-nav="sc-{c["id"]}">{e(c["id"])} &middot; {e(c.get("then", c["text"])[:90])} '
            f'<span class="mute" data-testid="mark-{c["id"]}">not exercised</span></button>' for c in crit)
        screens = ""
        for c in crit:
            cid = c["id"]
            screens += f"""
<section data-screen="sc-{cid}" aria-labelledby="h-{cid}">
  <div class="card">
    <h2 id="h-{cid}">{e(cid)} &middot; scenario</h2>
    <ul class="gwt"><li><strong>Given</strong> {e(c.get('given', ''))}</li><li><strong>When</strong> {e(c.get('when', ''))}</li><li><strong>Then</strong> {e(c.get('then', ''))}</li></ul>
    <div class="row">
      <label for="in-{cid}">Input</label>
      <input id="in-{cid}" data-testid="{cid}-input" type="text" placeholder="Sample input for this scenario">
    </div>
    <div class="row">
      <label for="var-{cid}">Mocked response</label>
      <select id="var-{cid}" data-testid="{cid}-variant">
        <option value="success">success</option><option value="empty">empty</option><option value="error">error</option>
      </select>
      <button class="primary" data-testid="{cid}-action" data-action="{cid}">Run: {e(c.get('when', 'perform action')[:60])}</button>
    </div>
    <div data-testid="{cid}-result" aria-live="polite">
      <div data-state="loading">Working&hellip;</div>
      <div data-state="success"><strong>Expected outcome reached:</strong> {e(c.get('then', ''))}</div>
      <div data-state="empty">Nothing to show yet. The request succeeded but returned no data.</div>
      <div data-state="error"><strong>Something went wrong.</strong> Nothing was saved. Choose a response and run again to retry.</div>
    </div>
    <div class="row"><button data-testid="{cid}-back" data-nav="home">Back to scenarios</button></div>
  </div>
</section>"""
        doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(spec.get('story_key', ''))} prototype</title><style>{_CSS}</style></head>
<body>
<header><h1>{e(spec.get('story_key', ''))} &middot; {e(spec.get('title', ''))}</h1><span class="badge" data-simulated>SIMULATED</span></header>
<main>
<section data-screen="home" data-start aria-labelledby="h-home">
  <div class="card"><h2 id="h-home">Scenarios</h2>
  <p class="mute">Every scenario below is interactive and runs on mocked state. Pick a mocked response, run the action, and check the outcome.</p>
  <div class="list">{nav}</div></div>
</section>
{screens}
</main>
<script>{_JS}</script>
</body></html>"""
        return doc, build_manifest(spec)


# ---------------------------------------------------------------- checker

class _Dom(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[dict] = []
        self.elements: list[dict] = []
        self.scripts: list[str] = []
        self.html_lang = None
        self._in_script = False
        self.labels_for: set[str] = set()

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        screen = a.get("data-screen") or next((el["screen"] for el in reversed(self.stack) if el["screen"]), None)
        el = {"tag": tag, "attrs": a, "screen": screen, "text": "", "parent": self.stack[-1] if self.stack else None}
        self.elements.append(el)
        if tag == "html":
            self.html_lang = a.get("lang")
        if tag == "label" and a.get("for"):
            self.labels_for.add(a["for"])
        if tag == "script":
            self._in_script = True
        if tag not in VOID:
            self.stack.append(el)

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self._in_script:
            self.scripts.append(data)
            return
        for el in self.stack:
            el["text"] += data


def check_prototype(doc: str, manifest: dict, spec: dict) -> dict:
    """Gate G2. Returns specific failed rule ids with references, never an overall rating."""
    failed: list[dict] = []

    def fail(rule: str, msg: str, ref: str = ""):
        failed.append({"rule": rule, "message": msg, "ref": ref})

    dom = _Dom()
    try:
        dom.feed(doc)
    except Exception as ex:  # malformed document
        fail("P-000", f"Document could not be parsed: {ex}")
        return {"passed": False, "failed_rules": failed, "journeys": [], "checker": "static-dom"}

    screens = {el["attrs"]["data-screen"] for el in dom.elements if "data-screen" in el["attrs"]}
    starts = [el["attrs"]["data-screen"] for el in dom.elements if "data-screen" in el["attrs"] and "data-start" in el["attrs"]]
    by_testid: dict[str, dict] = {}
    for el in dom.elements:
        tid = el["attrs"].get("data-testid")
        if tid:
            by_testid.setdefault(tid, el)

    # P-001 every mandatory criterion has a journey
    journeys = manifest.get("journeys", [])
    covered = {j["criterion_id"] for j in journeys}
    for c in spec.get("criteria", []):
        if c["id"] not in covered:
            fail("P-001", "No journey covers this acceptance criterion", c["id"])

    # P-002 navigation targets resolve
    edges: dict[str, set[str]] = {}
    for el in dom.elements:
        target = el["attrs"].get("data-nav")
        if target is None:
            continue
        if target not in screens:
            fail("P-002", f"Navigation target '{target}' does not exist",
                 el["attrs"].get("data-testid", el["tag"]))
        elif el["screen"]:
            edges.setdefault(el["screen"], set()).add(target)

    # P-010 start screen
    start = manifest.get("start_screen") or (starts[0] if starts else None)
    if len(starts) != 1 or start not in screens:
        fail("P-010", "Exactly one start screen (data-start) is required and must match the manifest")

    # P-003 journeys
    journey_results = []
    for j in journeys:
        current, ok, trace = None, True, []
        for n, step in enumerate(j["steps"], 1):
            op, err = step["op"], None
            if op == "start":
                current = start
            elif op == "click":
                el = by_testid.get(step["testid"])
                if not el:
                    err = f"control '{step['testid']}' not found"
                elif el["screen"] and el["screen"] != current:
                    err = f"control '{step['testid']}' is on screen '{el['screen']}', not the current screen '{current}'"
                elif "disabled" in el["attrs"]:
                    err = f"control '{step['testid']}' is disabled"
                elif "data-nav" in el["attrs"]:
                    if el["attrs"]["data-nav"] not in screens:
                        err = f"clicking '{step['testid']}' navigates to missing screen '{el['attrs']['data-nav']}'"
                    else:
                        current = el["attrs"]["data-nav"]
                elif "data-action" not in el["attrs"] and el["tag"] not in ("button", "a", "input", "select"):
                    err = f"'{step['testid']}' is not an interactive control"
            elif op == "expect_screen":
                if current != step["screen"]:
                    err = f"expected screen '{step['screen']}', on '{current}'"
            elif op == "expect_states":
                box = by_testid.get(step["testid"])
                if not box:
                    err = f"result region '{step['testid']}' not found"
                else:
                    have = {el["attrs"]["data-state"] for el in dom.elements
                            if "data-state" in el["attrs"] and _inside(el, box)}
                    missing = [s for s in step["states"] if s not in have]
                    if missing:
                        fail("P-007", f"Missing mocked states: {', '.join(missing)}", j["criterion_id"])
                        err = f"states missing: {', '.join(missing)}"
            trace.append({"step": n, "op": op, "ok": err is None, "detail": err or ""})
            if err:
                ok = False
                if not (op == "expect_states"):
                    fail("P-003", f"Journey {j['id']} step {n}: {err}", j["criterion_id"])
                break
        journey_results.append({"id": j["id"], "criterion_id": j["criterion_id"], "passed": ok, "trace": trace})

    # P-004 reachability
    if start in screens:
        seen, todo = {start}, [start]
        while todo:
            for nxt in edges.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        for s in sorted(screens - seen):
            fail("P-004", "Screen is not reachable from the start screen", s)

    # P-005 mock-only: no external references or network calls
    for el in dom.elements:
        for attr in ("src", "href", "action", "data", "poster", "srcset"):
            v = el["attrs"].get(attr, "")
            if re.match(r"\s*(https?:)?//", v, re.I) or v.strip().lower().startswith(("javascript:", "ws:", "wss:")):
                fail("P-005", f"External reference in {el['tag']}[{attr}]: {v[:80]}", el["tag"])
        if el["tag"] in ("iframe", "object", "embed", "base") or (el["tag"] == "meta" and el["attrs"].get("http-equiv", "").lower() == "refresh"):
            fail("P-005", f"<{el['tag']}> is not allowed in a prototype", el["tag"])
    script = "\n".join(dom.scripts)
    for pat in NETWORK_CALLS:
        if re.search(pat, script):
            fail("P-005", f"Script uses a network/navigation API: {pat}", "script")
    if re.search(r"url\(\s*['\"]?\s*(https?:)?//", doc, re.I) or re.search(r"@import", doc, re.I):
        fail("P-005", "Stylesheet references an external resource", "style")

    # P-006 secrets
    for pat in SECRET_PATTERNS:
        if re.search(pat, doc):
            fail("P-006", "Artifact contains a secret-like string", pat[:24])

    # P-008 simulated badge
    if not any("data-simulated" in el["attrs"] and el["text"].strip() for el in dom.elements):
        fail("P-008", "A visible SIMULATED badge (data-simulated) is required")

    # P-009 accessibility basics
    if not dom.html_lang:
        fail("P-009", "<html> has no lang attribute", "html")
    for el in dom.elements:
        a, tag = el["attrs"], el["tag"]
        ref = a.get("data-testid") or a.get("id") or tag
        if tag == "img" and "alt" not in a:
            fail("P-009", "Image has no alt text", ref)
        if tag == "button" and not (el["text"].strip() or a.get("aria-label")):
            fail("P-009", "Button has no accessible name", ref)
        if tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            if not (a.get("aria-label") or a.get("aria-labelledby") or a.get("id") in dom.labels_for
                    or (el["parent"] and _has_ancestor(el, "label"))):
                fail("P-009", "Form control has no label", ref)

    return {"passed": not failed, "failed_rules": failed, "journeys": journey_results, "checker": "static-dom",
            "rules_evaluated": ["P-001", "P-002", "P-003", "P-004", "P-005", "P-006", "P-007", "P-008", "P-009", "P-010"],
            "browser_check": {"ran": False, "reason": "No Playwright runner is provisioned; static DOM journey checks only."}}


def _inside(el: dict, ancestor: dict) -> bool:
    p = el["parent"]
    while p is not None:
        if p is ancestor:
            return True
        p = p["parent"]
    return False


def _has_ancestor(el: dict, tag: str) -> bool:
    p = el["parent"]
    while p is not None:
        if p["tag"] == tag:
            return True
        p = p["parent"]
    return False


class FaultInjectingGenerator:
    """Dev/demo wrapper: a story labelled 'proto-fault-once' gets one broken-navigation build
    (repaired on the next attempt); 'proto-fault-always' never repairs. Lets you watch the
    bounded revision loop without a real generator failure."""
    name = "template+fault-injection"

    def __init__(self, inner: Generator):
        self.inner = inner

    def generate(self, spec, attempt, failed_rules):
        doc, manifest = self.inner.generate(spec, attempt, failed_rules)
        labels = spec.get("labels", [])
        if "proto-fault-always" in labels or ("proto-fault-once" in labels and attempt == 0):
            first = spec["criteria"][0]["id"]
            doc = doc.replace(f'data-nav="sc-{first}"', 'data-nav="sc-missing"', 1)
        return doc, manifest
