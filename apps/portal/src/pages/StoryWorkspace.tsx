import { useEffect, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, errMsg } from '../api';
import type { AllowedActions, ArtifactRow, FailedRule, JunitResult, Prototype, SpecBrief, SpecDiff, SpecItem, StoryDetail, TestPlan, TestResult } from '../api';
import {
  ActionButton, Badge, CopyPre, Empty, ErrorBox, ExtLink, Field, GateChecklist, Loading, Panel, RunCard, Tabs, TaskTable, Timeline,
  fmtTime, money, pretty, short, useApp, useLive,
} from '../components';

const PERM: Record<keyof AllowedActions, string> = {
  sync: 'story.sync', analyze: 'story.analyze', revise: 'story.revise', annotate: 'story.annotate', approve: 'story.approve',
  dispatch: 'story.dispatch', ingestCommit: 'commit.ingest', recordGate: 'gate.record', uat: 'story.uat', syncEvidence: 'evidence.sync',
  transition: 'story.transition', pause: 'story.pause', resume: 'story.pause', cancel: 'story.pause', reportRun: 'run.report', cancelRun: 'run.cancel',
};
const WHEN: Partial<Record<keyof AllowedActions, string>> = {
  analyze: 'the story is ELIGIBLE', revise: 'a spec exists and the story is in review or later', annotate: 'a prototype exists',
  approve: 'the story is in HUMAN_REVIEW', dispatch: 'the plan is ready (PLAN_READY) or tasks are waiting',
  ingestCommit: 'approved work is under validation and has a head commit', recordGate: 'the story is in CI_VALIDATION or STAGING',
  uat: 'the story is in UAT', syncEvidence: 'the story is in EVIDENCE_SYNC', transition: 'the story is DONE_ELIGIBLE', resume: 'the story is PAUSED',
  pause: 'the story is still in flight', cancel: 'the story is not finished',
};

/** allowed + plain-language reason for a story action, from the server's allowedActions and the user's permissions. */
function useGate(story: StoryDetail) {
  const { can, needs } = useApp();
  return (a: keyof AllowedActions) => {
    const allowed = story.allowedActions[a];
    const reason = allowed ? '' : !can(PERM[a]) ? needs(PERM[a]) : `Available when ${WHEN[a] ?? 'the story reaches the right state'}; it is ${story.state} now.`;
    return { allowed, reason };
  };
}

export default function StoryWorkspace() {
  const { key = '' } = useParams();
  const { data: story, error, reload } = useLive(() => api.story(key), [key]);
  // the inbox links straight to a tab with ?tab=uat etc.
  const [params, setParams] = useSearchParams();
  const wanted = params.get('tab') ?? '';
  const tab = TAB_IDS.includes(wanted) ? wanted : 'sessions';
  const setTab = (id: string) => setParams(id === 'sessions' ? {} : { tab: id }, { replace: true });
  const tabsRef = useRef<HTMLElement | null>(null);
  const loaded = !!story;
  useEffect(() => {
    if (loaded && wanted) tabsRef.current?.scrollIntoView({ block: 'start' });
    // only when arriving with a tab in the URL, not on every tab click
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, key]);
  if (error && !story) return <><p><Link to="/">← Inbox</Link> · <Link to="/sprint">Sprint</Link></p><ErrorBox message={error} /></>;
  if (!story) return <Loading />;
  const everApproved = story.approvals.some((a) => a.decision === 'APPROVE');
  const awaiting = story.runs.filter((r) => r.state === 'AWAITING_MANUAL_LAUNCH').length;
  return (
    <>
      <header className="storyhead">
        <div className="row wrap">
          <Link to="/" className="small">← Inbox</Link>
          <Link to="/sprint" className="small">Sprint</Link>
          <h1>{story.key} <span className="h1sub">{story.summary}</span></h1>
        </div>
        <div className="row wrap strip">
          <Badge>{story.state}</Badge>
          <span className="muted">{story.stage}</span>
          <span>Owner <strong>{story.owner || '—'}</strong></span>
          <span>Jira <strong>{story.jiraStatus}</strong></span>
          <span>Spec <strong>{story.specVersion ? `v${story.specVersion}` : '—'}</strong></span>
          <span>Demo <strong>{story.prototypeVersion ? `v${story.prototypeVersion}` : '—'}</strong></span>
          <span>Head <code>{short(story.headCommit)}</code></span>
          <span>PR {story.prUrl ? <ExtLink href={story.prUrl}>open</ExtLink> : <span className="muted">—</span>}</span>
          <span>Cost <strong>{money(story.cost)}</strong> <span className="muted small">of {money(story.policy.maxPerStoryUsd)}</span></span>
          {story.baseline.valid
            ? <Badge tone="ok" title="The approval's three digests match the current spec, prototype and Jira criteria">Baseline valid</Badge>
            : everApproved
              ? <Badge tone="warn" title="Spec, prototype or Jira criteria changed after approval. Re-approval is required.">Baseline stale — re-approve</Badge>
              : <Badge tone="mute">No approved baseline</Badge>}
          {!story.inActiveSprint && <Badge tone="warn">Not in active sprint</Badge>}
          {story.leased && <Badge tone="info">Automation working…</Badge>}
        </div>
        {story.blockers.map((b, i) => <div key={i} className="banner bad"><strong>Blocked:</strong> {b}</div>)}
        {story.warnings.map((w, i) => <div key={i} className="banner warn"><strong>Warning:</strong> {w}</div>)}
        {error && <ErrorBox message={error} />}
      </header>

      <div className="cols">
        <div className="col"><SourcePanel story={story} /><SpecPanel story={story} /></div>
        <div className="col"><DemoPanel story={story} reload={reload} /></div>
        <div className="col"><ControlsPanel story={story} /></div>
      </div>

      <section ref={tabsRef} className="panel bottom">
        <Tabs active={tab} onChange={setTab} tabs={[
          { id: 'sessions', label: `Agent sessions (${story.runs.length})${awaiting ? ` · ${awaiting} awaiting launch` : ''}` },
          { id: 'ci', label: 'CI & gates' }, { id: 'uat', label: 'UAT' }, { id: 'evidence', label: 'Evidence' },
          { id: 'audit', label: `Audit timeline (${story.timeline.length})` },
        ]} />
        <div role="tabpanel" id={`tabpanel-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === 'sessions' && (
            <>
              <h3>Tasks</h3>
              <TaskTable tasks={story.tasks} />
              <h3>Runs</h3>
              {story.runs.length ? story.runs.map((r) => <RunCard key={r.id} run={r} />) : <Empty>No agent runs yet. Runs appear after an approved plan is dispatched.</Empty>}
            </>
          )}
          {tab === 'ci' && <CiTab story={story} />}
          {tab === 'uat' && <UatTab story={story} />}
          {tab === 'evidence' && <EvidenceTab story={story} />}
          {tab === 'audit' && <Timeline events={story.timeline} />}
        </div>
      </section>
    </>
  );
}

const TAB_IDS = ['sessions', 'ci', 'uat', 'evidence', 'audit'];

// ---------------------------------------------------------------- left: story + spec
function SourcePanel({ story }: { story: StoryDetail }) {
  const src = story.source;
  return (
    <Panel title="Story & context">
      <p className="muted small">Jira text is untrusted reference data. Fetched {fmtTime(src.fetchedAt)}; Jira updated {src.jiraUpdated || '—'}.</p>
      <h3>Description</h3>
      <p className="prewrap">{src.description || <span className="muted">No description.</span>}</p>
      <h3>Acceptance criteria <span className="muted small">digest <code title={src.criteriaDigest}>{short(src.criteriaDigest)}</code></span></h3>
      {src.criteria.length ? (
        <ul className="idlist">{src.criteria.map((c) => <li key={c.id}><code>{c.id}</code> {c.text}</li>)}</ul>
      ) : <Empty>No acceptance criteria in Jira.</Empty>}
      <h3>Links &amp; dependencies</h3>
      {src.links.length ? (
        <ul className="idlist">{src.links.map((l, i) => <li key={i}>{l.type} <strong>{l.key}</strong> {l.status && <Badge tone="mute">{l.status}</Badge>}</li>)}</ul>
      ) : <Empty>None.</Empty>}
      <h3>Readiness: {story.readiness.replace(/_/g, ' ')}</h3>
      {src.readinessReasons.length
        ? <ul className="idlist">{src.readinessReasons.map((r, i) => <li key={i} className="bad-text">{r}</li>)}</ul>
        : <Empty>No readiness problems found.</Empty>}
      <p className="small muted">Labels: {story.labels.join(', ') || '—'} · Components: {story.components.join(', ') || '—'}</p>
    </Panel>
  );
}

function Items({ title, items }: { title: string; items?: SpecItem[] }) {
  if (!items?.length) return null;
  return (
    <details open={title === 'Requirements'}>
      <summary>{title} ({items.length})</summary>
      <ul className="idlist">
        {items.map((it) => {
          const refs = (it.criterion_ids as string[] | undefined)?.join(', ') || (it.criterion_id as string | undefined) || (it.source as string | undefined);
          return (
            <li key={it.id}>
              <code>{it.id}</code> {it.text ?? it.description ?? ''}{' '}
              {typeof it.kind === 'string' && <span className="muted small">[{it.kind}]</span>} {refs && <span className="muted small">→ {refs}</span>}
              <Origin of={it.origin} />
            </li>
          );
        })}
      </ul>
    </details>
  );
}

/** Who contributed an item (claude, devin, …). Items from the base generator carry no origin. */
function Origin({ of }: { of?: unknown }) {
  return typeof of === 'string' && of ? <> <Badge tone="mute" title={`Added by ${of}`}>{`from ${of}`}</Badge></> : null;
}

function FailedRules({ rules }: { rules?: FailedRule[] }) {
  if (!rules?.length) return null;
  return (
    <div className="banner bad">
      <strong>{rules.length} failed rule(s)</strong>
      <ul className="idlist">{rules.map((f, i) => <li key={i}><code>{f.rule}</code> {f.message}{f.ref && <span className="muted"> ({f.ref})</span>}</li>)}</ul>
    </div>
  );
}

function SpecPanel({ story }: { story: StoryDetail }) {
  const spec = story.spec;
  const [diffTo, setDiffTo] = useState<number | null>(null);
  const [diff, setDiff] = useState<SpecDiff | null>(null);
  const [diffErr, setDiffErr] = useState('');
  useEffect(() => {
    setDiff(null);
    setDiffErr('');
    if (diffTo && diffTo > 1) api.specDiff(story.key, diffTo - 1, diffTo).then(setDiff).catch((e) => setDiffErr(errMsg(e)));
  }, [story.key, diffTo]);
  if (!spec) return <Panel title="Specification"><Empty>No spec yet. Analyze an eligible story to generate one.</Empty></Panel>;
  const c = spec.content;
  return (
    <Panel title={`Specification v${spec.version}`} actions={<Badge>{spec.status}</Badge>}>
      <p className="small muted">sha256 <code title={spec.sha256}>{short(spec.sha256)}</code> · by {spec.createdBy} · {fmtTime(spec.createdAt)}</p>
      {spec.checkResult.passed === false && <FailedRules rules={spec.checkResult.failed_rules} />}
      {spec.checkResult.passed && <p className="small">Spec checks passed ({spec.checkResult.rules_evaluated?.join(', ')}).</p>}
      <Items title="Requirements" items={c.requirements} />
      <Items title="Non-functional requirements" items={c.nonfunctional} />
      <Items title="Business rules" items={c.business_rules} />
      <Items title="Assumptions" items={c.assumptions} />
      <Items title="Error cases" items={c.error_cases} />
      <Items title="Test cases" items={c.test_cases} />
      <Items title="Open questions" items={c.open_questions} />
      {!!c.screens?.length && (
        <details>
          <summary>Screens ({c.screens.length})</summary>
          <ul className="idlist">
            {c.screens.map((sc, i) => (
              <li key={i}>
                <strong>{sc.name}</strong> {sc.purpose}{' '}
                {!!sc.criterion_ids?.length && <span className="muted small">→ {sc.criterion_ids.join(', ')}</span>}
                <Origin of={sc.origin} />
              </li>
            ))}
          </ul>
        </details>
      )}
      {!!c.code_impact?.length && (
        <details open>
          <summary>Code impact ({c.code_impact.length})</summary>
          <div className="tablewrap">
            <table>
              <thead><tr><th>Path</th><th>Change</th><th>Risk</th></tr></thead>
              <tbody>
                {c.code_impact.map((ci, i) => (
                  <tr key={i}>
                    <td><code>{ci.path}</code><Origin of={ci.origin} /></td>
                    <td className="small">{ci.change}</td>
                    <td><Badge tone={ci.risk === 'high' ? 'bad' : ci.risk === 'low' ? 'ok' : 'warn'}>{`${ci.risk} risk`}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {c.generation && (
        <p className={c.generation.fallback ? 'banner warn small' : 'small muted'}>
          Generated by <strong>{c.generation.generator ?? 'unknown'}</strong>
          {c.generation.fallback && <> as a <strong>fallback</strong>{c.generation.reason ? `: ${c.generation.reason}` : ''}</>}.
        </p>
      )}
      <details>
        <summary>Change log ({c.change_log?.length ?? 0})</summary>
        {c.change_log?.length
          ? <ul className="idlist">{c.change_log.map((l, i) => <li key={i}><code>v{l.version}</code> {l.kind} by {l.author}: {l.text}</li>)}</ul>
          : <Empty>First version; no changes recorded.</Empty>}
      </details>
      <h3>Versions</h3>
      <ul className="idlist">
        {story.specVersions.map((v) => (
          <li key={v.version} className="row wrap">
            <strong>v{v.version}</strong> <Badge>{v.status}</Badge> <code title={v.sha256}>{short(v.sha256)}</code>
            <span className="muted small">{v.createdBy} · {fmtTime(v.createdAt)}</span>
            {v.checkResult.failed_rules?.length ? <span className="bad-text small">failed: {[...new Set(v.checkResult.failed_rules.map((f) => f.rule))].join(', ')}</span> : null}
            {v.version > 1 && (
              <button type="button" className="btn small" aria-pressed={diffTo === v.version} onClick={() => setDiffTo(diffTo === v.version ? null : v.version)}>
                {diffTo === v.version ? 'Hide diff' : `Diff vs v${v.version - 1}`}
              </button>
            )}
          </li>
        ))}
      </ul>
      {diffErr && <ErrorBox message={diffErr} />}
      {diff && (
        <div className="diff">
          <h3>Changes v{diff.from} → v{diff.to}</h3>
          {!diff.changes.length && <Empty>No differences.</Empty>}
          {diff.changes.map((ch) => (
            <details key={ch.section} open>
              <summary>{ch.section}</summary>
              {ch.added?.map((x, i) => <pre key={'a' + i} className="add">+ added   {pretty(x)}</pre>)}
              {ch.removed?.map((x, i) => <pre key={'r' + i} className="del">- removed {pretty(x)}</pre>)}
              {ch.changed?.map((x) => <div key={x.id}><pre className="del">- {x.id} was {pretty(x.from)}</pre><pre className="add">+ {x.id} now {pretty(x.to)}</pre></div>)}
              {'from' in ch && !ch.added && <><pre className="del">- was {pretty(ch.from)}</pre><pre className="add">+ now {pretty(ch.to)}</pre></>}
            </details>
          ))}
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------- center: playable demo
function CheckResults({ result }: { result: TestResult }) {
  if (!result || !Object.keys(result).length) return <Empty>Checks have not run for this version yet.</Empty>;
  return (
    <>
      <p>Overall: <Badge>{result.passed ? 'PASS' : 'FAIL'}</Badge> <span className="muted small">checker: {result.checker}</span></p>
      <FailedRules rules={result.failed_rules} />
      {result.journeys?.map((j) => (
        <details key={j.id} open={!j.passed}>
          <summary><code>{j.id}</code> for {j.criterion_id}: <Badge>{j.passed ? 'PASS' : 'FAIL'}</Badge></summary>
          <ol className="trace">
            {j.trace.map((t) => <li key={t.step} className={t.ok ? '' : 'bad-text'}>{t.op} — {t.ok ? 'ok' : 'FAILED'}{t.detail && `: ${t.detail}`}</li>)}
          </ol>
        </details>
      ))}
      {result.browser_check && (
        <p className="banner warn small">
          <strong>Browser check {result.browser_check.ran ? 'ran' : 'did not run'}.</strong> {result.browser_check.reason ?? pretty(result.browser_check)}
        </p>
      )}
    </>
  );
}

function DemoPanel({ story, reload }: { story: StoryDetail; reload: () => Promise<void> }) {
  const { act } = useApp();
  const gate = useGate(story);
  const current = story.prototype;
  const [picked, setPicked] = useState<number | null>(null);
  const [mobile, setMobile] = useState(false);
  const [compare, setCompare] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [other, setOther] = useState<Prototype | null>(null);
  const [selector, setSelector] = useState('');
  const [freeSel, setFreeSel] = useState('');
  const [comment, setComment] = useState('');
  // Preview URLs are re-signed on every poll; pin one per (story, version, reload) so the iframe does not reload itself.
  const urls = useRef<Record<string, string>>({});
  useEffect(() => { setPicked(null); setCompare(false); }, [story.key]);
  const version = picked ?? current?.version ?? 0;
  useEffect(() => {
    setOther(null);
    if (current && version && version !== current.version) api.prototype(story.key, version).then(setOther).catch(() => setOther(null));
  }, [story.key, version, current?.version]);
  if (!current) return <Panel title="Playable demo"><Empty>No prototype yet. It is generated after the spec passes its checks.</Empty></Panel>;

  const urlFor = (v: number) => {
    const k = `${story.key}:${v}:${nonce}`;
    const fresh = story.prototypeVersions.find((p) => p.version === v)?.previewUrl;
    if (!urls.current[k] && fresh) urls.current[k] = fresh;
    return urls.current[k];
  };
  // baseline.approvedPrototypeVersion is null whenever the baseline is stale, so fall back to the last APPROVE record.
  const approvedV = story.baseline.approvedPrototypeVersion ?? story.approvals.find((a) => a.decision === 'APPROVE')?.prototypeVersion ?? null;
  const canCompare = approvedV !== null && approvedV !== current.version;
  const shown = version === current.version ? current : other;
  const frame = (v: number, label: string) => (
    <figure className="frame">
      <figcaption>{label} <Badge tone="warn">SIMULATED</Badge></figcaption>
      <iframe key={`${v}:${nonce}`} title={`${label} of ${story.key}`} sandbox="allow-scripts" src={urlFor(v)}
              style={{ width: mobile ? 390 : '100%' }} />
    </figure>
  );
  const sel = selector === '__free' ? freeSel.trim() : selector;
  return (
    <Panel title="Playable demo" actions={<Badge tone="warn" title="Mocked state only. No live or production service is called.">SIMULATED — not the real product</Badge>}>
      <div className="row wrap toolbar">
        <label className="field inline"><span className="field-label">Version</span>
          <select value={version} onChange={(e) => setPicked(Number(e.target.value))}>
            {story.prototypeVersions.map((p) => (
              <option key={p.version} value={p.version}>
                v{p.version} · {p.status}{p.version === current.version ? ' · current' : ''}{p.version === approvedV ? ' · approved' : ''}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="seg">
          <legend className="sr-only">Viewport</legend>
          <button type="button" className="btn small" aria-pressed={!mobile} onClick={() => setMobile(false)}>Desktop</button>
          <button type="button" className="btn small" aria-pressed={mobile} onClick={() => setMobile(true)}>Mobile 390px</button>
        </fieldset>
        <button type="button" className="btn small" onClick={async () => { await reload(); setNonce((n) => n + 1); }}>Reload preview</button>
        <ExtLink href={urlFor(version)}>Open in new tab</ExtLink>
        <button type="button" className="btn small" disabled={!canCompare} aria-pressed={compare && canCompare}
                title={canCompare ? undefined : 'Available when the current version differs from the last approved version'}
                onClick={() => setCompare(!compare)}>
          Compare with approved{approvedV ? ` v${approvedV}` : ''}
        </button>
      </div>
      {compare && canCompare && approvedV !== null ? (
        <div className="compare">{frame(approvedV, `Approved v${approvedV}`)}{frame(current.version, `Current v${current.version}`)}</div>
      ) : frame(version, `Demo v${version}`)}
      <p className="small muted">
        sha256 <code title={shown?.sha256}>{short(shown?.sha256)}</code> · built from spec v{shown?.specVersion ?? '—'} · generator {shown?.generator ?? '—'} ·
        preview links expire after {Math.round(current.previewExpiresInSeconds / 60)} min (use Reload preview).
      </p>

      <h3>Check results for v{version}</h3>
      {shown ? <CheckResults result={shown.testResult} /> : <Loading />}

      <h3>Annotations ({story.annotations.filter((a) => !a.resolved).length} open)</h3>
      {story.annotations.length ? (
        <ul className="idlist">
          {story.annotations.map((a) => (
            <li key={a.id} className="row wrap">
              <Badge tone={a.resolved ? 'ok' : 'attn'}>{a.resolved ? 'resolved' : 'open'}</Badge>
              <span className="grow">{a.selector && <code>{a.selector}</code>} {a.comment} <span className="muted small">— {a.author}, v{a.prototypeVersion}, {fmtTime(a.createdAt)}</span></span>
              {!a.resolved && (
                <ActionButton {...gate('annotate')} onAct={() => act(() => api.resolveAnnotation(story.key, a.id), 'Annotation resolved')}>Resolve</ActionButton>
              )}
            </li>
          ))}
        </ul>
      ) : <Empty>No annotations. Open annotations block approval.</Empty>}
      <form className="formgrid" onSubmit={(e) => e.preventDefault()}>
        <Field label="Element">
          <select value={selector} onChange={(e) => setSelector(e.target.value)}>
            <option value="">(whole screen)</option>
            {current.testIds.map((t) => <option key={t} value={t}>{t}</option>)}
            <option value="__free">Other selector…</option>
          </select>
        </Field>
        {selector === '__free' && <Field label="Selector"><input value={freeSel} onChange={(e) => setFreeSel(e.target.value)} placeholder="e.g. .toolbar button" /></Field>}
        <Field label="Comment"><textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
        <div className="row wrap end">
          <ActionButton {...gate('annotate')} showReason onAct={async () => {
            const r = await act(() => api.annotate(story.key, { selector: sel, comment, prototypeVersion: version }), 'Annotation added');
            if (r) setComment('');
          }}>Add annotation</ActionButton>
        </div>
      </form>
    </Panel>
  );
}

// ---------------------------------------------------------------- right: controls
function ControlsPanel({ story }: { story: StoryDetail }) {
  const { act, config } = useApp();
  const gate = useGate(story);
  const [review, setReview] = useState('');
  const [feedback, setFeedback] = useState('');
  const [html, setHtml] = useState('');
  const [reason, setReason] = useState('');
  const [engines, setEngines] = useState<Record<string, string>>({});
  const { spec, prototype: proto, key } = story;
  const pickable = (config?.engines ?? []).filter((e) => e.enabled && e.mode !== 'unavailable');
  const open = story.tasks.filter((t) => t.status === 'PENDING' || t.status === 'WAITING');

  const decide = (decision: 'APPROVE' | 'REQUEST_CHANGES') => act(async () => {
    if (!spec || !proto) throw new Error('There is no spec and prototype to review yet.');
    const r = await api.approve(key, {
      decision, specVersion: spec.version, specSha256: spec.sha256, prototypeVersion: proto.version, prototypeSha256: proto.sha256,
      criteriaSha256: spec.criteriaSha256, reviewComment: review, expectedStoryRevision: story.revision,
    });
    setReview('');
    return r;
  }, (r) => (decision === 'APPROVE' ? `Approved. Story is now ${r.state}.` : `Changes requested. Story is now ${r.state}.`));

  return (
    <>
      <Panel title="Gate checklist">
        <GateChecklist gates={story.gateDetails} />
        <p className="small muted">All nine gates are shown individually. There is no aggregate score and no override.</p>
      </Panel>

      <Panel title="Actions">
        <div className="stack">
          <div className="row wrap">
            <ActionButton kind="primary" {...gate('analyze')} showReason onAct={() => act(() => api.analyze(key), 'Analysis started')}>Analyze</ActionButton>
          </div>
          <div className="row wrap">
            <ActionButton {...gate('sync')} onAct={() => act(() => api.syncStory(key), (r) => `Re-read from Jira. State: ${r.state}`)}>Sync story from Jira</ActionButton>
          </div>
        </div>

        <h3>Review &amp; approve</h3>
        {spec && proto ? (
          <dl className="kv sign" aria-label="What you sign">
            <dt>Spec v{spec.version}</dt><dd><code title={spec.sha256}>{short(spec.sha256, 16)}</code></dd>
            <dt>Demo v{proto.version}</dt><dd><code title={proto.sha256}>{short(proto.sha256, 16)}</code></dd>
            <dt>Jira criteria</dt><dd><code title={spec.criteriaSha256}>{short(spec.criteriaSha256, 16)}</code></dd>
            <dt>Story revision</dt><dd>{story.revision}</dd>
          </dl>
        ) : <Empty>Nothing to review yet.</Empty>}
        {spec && story.source.criteriaDigest !== spec.criteriaSha256 && (
          <p className="banner warn small">Jira acceptance criteria changed after this spec was generated. Request a revision instead of approving.</p>
        )}
        <p className="small muted">Your decision is bound to exactly these digests. If any of them changes, the approval becomes stale.</p>
        <Field label="Review comment (required when requesting changes)">
          <textarea rows={2} value={review} onChange={(e) => setReview(e.target.value)} />
        </Field>
        <div className="row wrap">
          <ActionButton kind="primary" {...gate('approve')} showReason onAct={() => decide('APPROVE')}>Approve these versions</ActionButton>
          <ActionButton {...gate('approve')} onAct={() => decide('REQUEST_CHANGES')}>Request changes</ActionButton>
        </div>

        <h3>Request revision</h3>
        {story.baseline.valid && <p className="banner warn small">This story has an approved baseline. A revision is a design change: active runs are cancelled and re-approval is required.</p>}
        <Field label="Feedback: what must change and why">
          <textarea rows={3} value={feedback} onChange={(e) => setFeedback(e.target.value)} />
        </Field>
        <details>
          <summary>Optional: paste replacement prototype HTML</summary>
          <Field label="Prototype HTML (single self-contained file, under 2 MB)">
            <textarea rows={5} className="mono" value={html} onChange={(e) => setHtml(e.target.value)} spellCheck={false} />
          </Field>
        </details>
        <div className="row wrap">
          <ActionButton {...gate('revise')} showReason onAct={async () => {
            const r = await act(() => api.revise(key, html.trim() ? { feedback, prototypeHtml: html } : { feedback }), 'Revision started');
            if (r) { setFeedback(''); setHtml(''); }
          }}>Request revision</ActionButton>
        </div>

        <DeepenSpec story={story} />

        <h3>Dispatch plan</h3>
        {story.tasks.length ? (
          <ul className="idlist tasks">
            {story.tasks.map((t) => {
              const editable = t.status === 'PENDING' || t.status === 'WAITING';
              return (
                <li key={t.id}>
                  <div className="row wrap"><strong>{t.key}</strong> <Badge>{t.status}</Badge> <span className="muted small">{t.kind}</span></div>
                  <div className="small">{t.title}</div>
                  <div className="small muted">Depends on: {t.deps.join(', ') || 'nothing'} · Owns: <code>{t.ownerPaths.join(' ')}</code></div>
                  {t.blocker && <div className="small warn-text">{t.blocker}</div>}
                  <label className="field"><span className="field-label">Engine for {t.key}</span>
                    <select disabled={!editable} value={engines[t.key] ?? t.engine} onChange={(e) => setEngines({ ...engines, [t.key]: e.target.value })}>
                      {!pickable.some((e) => e.engine === t.engine) && <option value={t.engine}>{t.engine}</option>}
                      {pickable.map((e) => <option key={e.engine} value={e.engine}>{e.label} ({e.mode})</option>)}
                    </select>
                  </label>
                </li>
              );
            })}
          </ul>
        ) : <Empty>No plan yet. The task plan is created automatically when the baseline is approved.</Empty>}
        <div className="row wrap">
          <ActionButton kind="primary" {...gate('dispatch')} showReason onAct={() => act(async () => {
            const changed: Record<string, string> = {};
            for (const t of open) if (engines[t.key] && engines[t.key] !== t.engine) changed[t.key] = engines[t.key];
            const r = await api.dispatch(key, changed);
            setEngines({});
            return r;
          }, (r) => `Dispatched: ${r.started.length} run(s) started, ${r.queued.length} task(s) queued. State: ${r.state}`)}>
            Dispatch {open.length ? `${open.length} open task(s)` : ''}
          </ActionButton>
        </div>

        <h3>Pause / resume / cancel</h3>
        <Field label="Reason (optional)"><input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <div className="row wrap">
          <ActionButton {...gate('pause')} onAct={() => act(() => api.pause(key, reason), 'Story paused')}>Pause</ActionButton>
          <ActionButton {...gate('resume')} onAct={() => act(() => api.resume(key), (r) => `Resumed. State: ${r.state}`)}>Resume</ActionButton>
          <ActionButton kind="danger" {...gate('cancel')} confirm={`Cancel ${key}? Active runs are cancelled and the story cannot be resumed.`}
                        onAct={() => act(() => api.cancel(key, reason), 'Story cancelled')}>Cancel story</ActionButton>
        </div>
      </Panel>

      <Panel title="Approval history">
        {story.approvals.length ? (
          <ul className="idlist">
            {story.approvals.map((a) => (
              <li key={a.id}>
                <div className="row wrap">
                  <Badge>{a.decision}</Badge>
                  {a.inForce ? <Badge tone="ok">in force</Badge> : <Badge tone="mute">not in force</Badge>}
                  <span>{a.actor} <span className="muted small">({a.roles.join(', ')})</span></span>
                  <span className="muted small">{fmtTime(a.timestamp)}</span>
                </div>
                <div className="small muted">
                  spec v{a.specVersion} <code title={a.specSha256}>{short(a.specSha256)}</code> · demo v{a.prototypeVersion} <code title={a.prototypeSha256}>{short(a.prototypeSha256)}</code> · criteria <code title={a.criteriaSha256}>{short(a.criteriaSha256)}</code>
                </div>
                {a.comment && <div className="small">“{a.comment}”</div>}
              </li>
            ))}
          </ul>
        ) : <Empty>No approval decisions recorded.</Empty>}
        <p className="small muted">Auto-refinement attempts {story.policy.autoAttempts}/{story.policy.autoRefinementMaxAttempts} · repair attempts {story.policy.repairAttempts}/{story.policy.autoRepairMaxAttempts}</p>
      </Panel>
    </>
  );
}

/** Hand a read-only analysis package to an engine that can see the repository, then paste its JSON back as a revision. */
function DeepenSpec({ story }: { story: StoryDetail }) {
  const { act, config } = useApp();
  const gate = useGate(story);
  const engines = (config?.engines ?? []).filter((e) => e.enabled && e.engine !== 'simulated');
  const [engine, setEngine] = useState('');
  const [brief, setBrief] = useState<SpecBrief | null>(null);
  const [proto, setProto] = useState<{ engineLabel: string; brief: string; file: string } | null>(null);
  const [briefErr, setBriefErr] = useState('');
  const [json, setJson] = useState('');
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const chosen = engines.some((e) => e.engine === engine) ? engine : engines[0]?.engine ?? '';
  useEffect(() => { setBrief(null); setProto(null); setBriefErr(''); setErr(''); }, [story.key, chosen]);
  const revise = gate('revise');
  return (
    <>
      <h3>Improve spec and prototype in VS Code chat or Devin</h3>
      <p className="small muted">
        The portal calls no model itself. Give a package to Copilot Chat, Claude Code or Devin, then bring the answer back: JSON findings go in the box below, and a prototype HTML file goes in “Request revision” above. Acceptance criteria are never changed.
      </p>
      <div className="formrow">
        <Field label="Engine">
          <select value={chosen} onChange={(e) => setEngine(e.target.value)}>
            {!engines.length && <option value="">No engine enabled</option>}
            {engines.map((e) => <option key={e.engine} value={e.engine}>{e.label}</option>)}
          </select>
        </Field>
        <ActionButton allowed={!!story.spec && !!chosen} reason={!story.spec ? 'There is no spec yet; analyze the story first.' : 'Enable an engine in Administration first.'}
                      onAct={async () => {
                        setBriefErr('');
                        try { setBrief(await api.specBrief(story.key, chosen)); } catch (e) { setBrief(null); setBriefErr(errMsg(e)); }
                      }}>
          Get analysis package
        </ActionButton>
        <ActionButton allowed={!!story.spec && !!chosen} reason={!story.spec ? 'There is no spec yet; analyze the story first.' : 'Enable an engine in Administration first.'}
                      onAct={async () => {
                        setBriefErr('');
                        try { setProto(await api.prototypeBrief(story.key, chosen)); } catch (e) { setProto(null); setBriefErr(errMsg(e)); }
                      }}>
          Get prototype package
        </ActionButton>
      </div>
      {briefErr && <ErrorBox message={briefErr} />}
      {brief && (
        <>
          <p className="small">
            Package for <strong>{brief.engineLabel}</strong> (playbook <code>{brief.playbook}</code>{brief.devinPlaybookId && <>, Devin playbook <code>{brief.devinPlaybookId}</code></>}). Read-only task: the engine must not change code.
          </p>
          <CopyPre text={brief.brief} label="Copy package" />
        </>
      )}
      {proto && (
        <>
          <p className="small">
            Prototype package for <strong>{proto.engineLabel}</strong>. The engine writes one HTML file (<code>{proto.file}</code>); paste it under “Request revision” above and it goes through the same checks as a generated prototype.
          </p>
          <CopyPre text={proto.brief} label="Copy prototype package" />
        </>
      )}
      <Field label={`JSON returned by ${engines.find((e) => e.engine === chosen)?.label ?? 'the engine'}`}>
        <textarea rows={5} className="mono" spellCheck={false} value={json} onChange={(e) => { setJson(e.target.value); setErr(''); }}
                  placeholder={'{"business_rules": [], "assumptions": [], "nonfunctional": [], "error_cases": [], "open_questions": [], "screens": [], "code_impact": []}'} />
      </Field>
      <Field label="Note (optional)"><input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      {err && <ErrorBox message={err} />}
      <div className="row wrap">
        <ActionButton allowed={revise.allowed && !!chosen && !!json.trim()} showReason
                      reason={!revise.allowed ? revise.reason : !chosen ? 'Enable an engine in Administration first.' : 'Paste the JSON the engine returned first.'}
                      onAct={async () => {
                        let enrichment: unknown;
                        try {
                          enrichment = JSON.parse(json);
                        } catch (e) {
                          setErr(`The pasted text is not valid JSON (${errMsg(e)}). Paste only the JSON object the engine returned.`);
                          return;
                        }
                        setErr('');
                        const r = await act(async () => {
                          try {
                            return await api.revise(story.key, { origin: chosen, enrichment, ...(note.trim() ? { feedback: note.trim() } : {}) });
                          } catch (e) {
                            setErr(errMsg(e));
                            throw e;
                          }
                        }, `Findings from ${chosen} submitted; a new spec version is being checked`);
                        if (r) { setJson(''); setNote(''); }
                      }}>
          Submit findings
        </ActionButton>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- bottom tabs
const SAMPLE_EVIDENCE = '{"ci_run_url":"https://ci.example/run/123","tests_passed":42,"tests_failed":0}';

function CiTab({ story }: { story: StoryDetail }) {
  const { act } = useApp();
  const gate = useGate(story);
  const [g, setG] = useState({ gate: 'G4', result: 'PASS', commit: story.headCommit, summary: '', evidence: SAMPLE_EVIDENCE });
  const [c, setC] = useState({ commit: '', prUrl: '', source: 'vscode' });
  useEffect(() => setG((x) => ({ ...x, commit: story.headCommit })), [story.headCommit]);
  useEffect(() => setG((x) => ({ ...x, gate: story.state === 'STAGING' ? 'G6' : x.gate === 'G6' ? 'G4' : x.gate })), [story.state]);
  return (
    <>
    <h3>Checks on head commit <code>{short(story.headCommit)}</code></h3>
    {story.checks?.length ? (
      <div className="tablewrap">
        <table>
          <thead><tr><th>Check</th><th>Conclusion</th><th>Link</th></tr></thead>
          <tbody>
            {story.checks.map((ch) => (
              <tr key={ch.name}>
                <td><code>{ch.name}</code></td>
                <td><Badge tone={ch.conclusion === 'success' ? 'ok' : ch.conclusion ? 'bad' : 'mute'}>{ch.conclusion || 'no conclusion'}</Badge></td>
                <td>{ch.url ? <ExtLink href={ch.url}>open run</ExtLink> : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : <Empty>No CI check runs received for this commit. They arrive through the GitHub webhook; a JUnit report can also be uploaded below.</Empty>}
    <div className="split">
      <JunitUpload story={story} />
      <TestPlanView story={story} />
    </div>
    <div className="split">
      <form className="formgrid" onSubmit={(e) => e.preventDefault()}>
        <h3>Record a CI / staging gate result</h3>
        <p className="small muted">G4 and G5 are recorded during CI_VALIDATION, G6 during STAGING, always for the exact head commit (<code>{short(story.headCommit)}</code>).</p>
        <Field label="Gate">
          <select value={g.gate} onChange={(e) => setG({ ...g, gate: e.target.value })}>
            <option value="G4">G4 Code quality</option><option value="G5">G5 Integration / security</option><option value="G6">G6 Staging fidelity</option>
          </select>
        </Field>
        <Field label="Result">
          <select value={g.result} onChange={(e) => setG({ ...g, result: e.target.value })}><option>PASS</option><option>FAIL</option></select>
        </Field>
        <Field label="Commit"><input value={g.commit} onChange={(e) => setG({ ...g, commit: e.target.value })} /></Field>
        <Field label="Summary"><input value={g.summary} onChange={(e) => setG({ ...g, summary: e.target.value })} placeholder="e.g. lint, types, 42 unit tests green" /></Field>
        <Field label="Evidence (JSON, required)" hint="An assertion is not evidence: include the CI run URL, report or counts.">
          <textarea rows={4} className="mono" spellCheck={false} value={g.evidence} onChange={(e) => setG({ ...g, evidence: e.target.value })} />
        </Field>
        <div className="row wrap">
          <ActionButton kind="primary" {...gate('recordGate')} showReason onAct={() => act(async () => {
            let evidence: unknown;
            try { evidence = JSON.parse(g.evidence); } catch { throw new Error('Evidence is not valid JSON. Fix it and try again.'); }
            return api.recordGate(story.key, { gate: g.gate, result: g.result, commit: g.commit.trim(), summary: g.summary, evidence });
          }, (r) => `${g.gate} recorded as ${g.result}. Story is now ${r.state}.`)}>Record gate result</ActionButton>
        </div>
      </form>
      <form className="formgrid" onSubmit={(e) => e.preventDefault()}>
        <h3>Ingest a new commit / PR</h3>
        <p className="small muted">A new head commit makes every commit-pinned gate (G4–G8) stale; CI and UAT must run again on it.</p>
        <Field label="Commit (7–40 hex)"><input value={c.commit} onChange={(e) => setC({ ...c, commit: e.target.value })} /></Field>
        <Field label="PR URL"><input value={c.prUrl} onChange={(e) => setC({ ...c, prUrl: e.target.value })} placeholder="https://github.com/org/repo/pull/1" /></Field>
        <Field label="Source"><input value={c.source} onChange={(e) => setC({ ...c, source: e.target.value })} placeholder="vscode, ci, manual…" /></Field>
        <div className="row wrap">
          <ActionButton {...gate('ingestCommit')} showReason onAct={() => act(() => api.ingestCommit(story.key, { commit: c.commit.trim(), prUrl: c.prUrl.trim(), source: c.source }),
            (r) => (r.changed ? `Commit ingested. Story is now ${r.state}; gates G4–G8 must be re-established.` : 'That commit is already the head; nothing changed.'))}>Ingest commit</ActionButton>
        </div>
      </form>
    </div>
    </>
  );
}

function JunitUpload({ story }: { story: StoryDetail }) {
  const { act } = useApp();
  const gate = useGate(story);
  const [j, setJ] = useState({ gate: story.state === 'STAGING' ? 'G6' : 'G4', commit: story.headCommit, xml: '' });
  const [file, setFile] = useState('');
  const [res, setRes] = useState<(JunitResult & { gate: string }) | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => setJ((x) => ({ ...x, commit: story.headCommit })), [story.headCommit]);
  useEffect(() => setJ((x) => ({ ...x, gate: story.state === 'STAGING' ? 'G6' : x.gate === 'G6' ? 'G4' : x.gate })), [story.state]);
  const record = gate('recordGate');
  const ready = !!j.xml.trim() && !!j.commit.trim();
  return (
    <form className="formgrid" onSubmit={(e) => e.preventDefault()}>
      <h3>Upload a JUnit report</h3>
      <p className="small muted">Tests are mapped to acceptance criteria by the id in their name (AC-01 …) and the gate is recorded for the commit. CI can post the same report with the automation token.</p>
      <Field label="Gate">
        <select value={j.gate} onChange={(e) => setJ({ ...j, gate: e.target.value })}>
          <option value="G4">G4 Code quality</option><option value="G5">G5 Integration / security</option><option value="G6">G6 Staging fidelity</option>
        </select>
      </Field>
      <Field label="Commit"><input value={j.commit} onChange={(e) => setJ({ ...j, commit: e.target.value })} /></Field>
      <Field label="Report file (.xml)" hint={file ? `Loaded ${file}` : undefined}>
        <input type="file" accept=".xml,text/xml,application/xml" onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          try { const text = await f.text(); setJ((x) => ({ ...x, xml: text })); setFile(f.name); setErr(''); } catch (ex) { setErr(`Could not read ${f.name}: ${errMsg(ex)}`); }
        }} />
      </Field>
      <Field label="JUnit XML (paste, or choose a file above)">
        <textarea rows={5} className="mono" spellCheck={false} value={j.xml} onChange={(e) => { setJ({ ...j, xml: e.target.value }); setFile(''); }}
                  placeholder={'<testsuite><testcase name="AC-01 shows the total" /></testsuite>'} />
      </Field>
      {err && <ErrorBox message={err} />}
      <div className="row wrap">
        <ActionButton kind="primary" allowed={record.allowed && ready} showReason
                      reason={!record.allowed ? record.reason : 'Provide the report XML and the commit it was run on.'}
                      onAct={async () => {
                        setErr('');
                        const r = await act(async () => {
                          try {
                            return await api.junit(story.key, j.commit.trim(), j.gate, j.xml);
                          } catch (e) {
                            setErr(errMsg(e));
                            throw e;
                          }
                        }, (x) => `JUnit report processed: ${j.gate} is ${x.result}`);
                        if (r) setRes({ ...r, gate: j.gate });
                      }}>
          Upload report
        </ActionButton>
      </div>
      {res && (
        <div role="status">
          <p><strong>{res.gate}</strong> <Badge>{res.result}</Badge> {res.summary}{res.state && <span className="muted small"> · story is now {res.state}</span>}</p>
          <div className="tablewrap">
            <table>
              <thead><tr><th>Criterion</th><th>Passed</th><th>Failed</th><th>Skipped</th><th>Tests</th></tr></thead>
              <tbody>
                {Object.entries(res.criteria).map(([id, c]) => (
                  <tr key={id}>
                    <td><code>{id}</code></td><td>{c.passed}</td><td className={c.failed ? 'bad-text' : ''}>{c.failed}</td><td>{c.skipped}</td>
                    <td className="small">{c.tests.length ? c.tests.join('; ') : <span className="warn-text">no test names this criterion</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </form>
  );
}

function TestPlanView({ story }: { story: StoryDetail }) {
  const [plan, setPlan] = useState<TestPlan | null>(null);
  const [err, setErr] = useState('');
  const version = story.spec?.version ?? 0;
  useEffect(() => {
    let stale = false;
    setPlan(null);
    setErr('');
    if (version) api.testPlan(story.key).then((p) => !stale && setPlan(p)).catch((e) => !stale && setErr(errMsg(e)));
    return () => { stale = true; };
  }, [story.key, version]);
  return (
    <div>
      <h3>Test plan{plan ? ` for spec v${plan.specVersion}` : ''}</h3>
      {!version && <Empty>No spec yet, so there is no test plan.</Empty>}
      {err && <ErrorBox message={err} />}
      {!!version && !plan && !err && <Loading />}
      {plan && (
        <>
          <p className="small muted">{plan.rule}</p>
          <div className="tablewrap">
            <table>
              <thead><tr><th>Criterion</th><th>Test name must contain</th><th>Error cases to cover</th></tr></thead>
              <tbody>
                {plan.criteria.map((c) => (
                  <tr key={c.id}>
                    <td><code>{c.id}</code> <span className="small">{c.text}</span></td>
                    <td><code>{c.testNameMustContain}</code></td>
                    <td className="small">{c.errorCases.length ? c.errorCases.join('; ') : <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details>
            <summary>End-to-end test skeleton (Playwright)</summary>
            <CopyPre text={plan.e2eSkeleton} label="Copy skeleton" />
          </details>
        </>
      )}
    </div>
  );
}

function UatTab({ story }: { story: StoryDetail }) {
  const { act } = useApp();
  const gate = useGate(story);
  const [u, setU] = useState({ decision: 'PASS', commit: story.headCommit, feedback: '', material: false });
  useEffect(() => setU((x) => ({ ...x, commit: story.headCommit })), [story.headCommit]);
  return (
    <form className="formgrid narrow" onSubmit={(e) => e.preventDefault()}>
      <h3>Human UAT decision</h3>
      <p className="small muted">UAT must be performed by a named person on the current build (<code>{short(story.headCommit)}</code>). A failed UAT needs feedback describing the defect.</p>
      <Field label="Decision">
        <select value={u.decision} onChange={(e) => setU({ ...u, decision: e.target.value })}><option>PASS</option><option>FAIL</option></select>
      </Field>
      <Field label="Commit tested"><input value={u.commit} onChange={(e) => setU({ ...u, commit: e.target.value })} /></Field>
      <Field label="Feedback"><textarea rows={3} value={u.feedback} onChange={(e) => setU({ ...u, feedback: e.target.value })} /></Field>
      <label className="check">
        <input type="checkbox" checked={u.material} onChange={(e) => setU({ ...u, material: e.target.checked })} />
        Material design change (sends the story back to spec / prototype approval instead of rework)
      </label>
      <div className="row wrap">
        <ActionButton kind="primary" {...gate('uat')} showReason onAct={() => act(() => api.uat(story.key, { decision: u.decision, commit: u.commit.trim(), feedback: u.feedback, materialDesignChange: u.material }),
          (r) => `UAT ${u.decision} recorded. Story is now ${r.state}.`)}>Submit UAT decision</ActionButton>
      </div>
    </form>
  );
}

const PROOFS: [keyof StoryDetail['traceability'][number]['proven'], string][] = [
  ['prototype', 'Prototype'], ['approved', 'Approved'], ['ci', 'CI'], ['staging', 'Staging'], ['uat', 'UAT'],
];
const YesNo = ({ v }: { v: boolean }) => <span className={v ? 'ok-text' : 'muted'}>{v ? '✓ yes' : '✗ no'}</span>;

function EvidenceTab({ story }: { story: StoryDetail }) {
  const { act } = useApp();
  const gate = useGate(story);
  const { data: ev, error } = useLive(() => api.evidence(story.key), [story.key]);
  const [art, setArt] = useState<ArtifactRow | null>(null);
  const [artErr, setArtErr] = useState('');
  return (
    <>
      <div className="row wrap">
        <ActionButton kind="primary" {...gate('syncEvidence')} showReason
                      onAct={() => act(() => api.syncEvidence(story.key), (r) => `Evidence comment ${r.remoteId} confirmed in Jira (attempt ${r.attempts}). State: ${r.state}`)}>
          Publish evidence to Jira
        </ActionButton>
        <ActionButton {...gate('transition')} showReason confirm={`Transition ${story.key} to Done in Jira? This is recorded under your name.`}
                      onAct={() => act(() => api.transition(story.key), (r) => `Jira status is now ${r.jiraStatus}. Story is ${r.state}.`)}>Transition to Done</ActionButton>
      </div>
      <h3>Traceability matrix</h3>
      {story.traceability.length ? (
        <div className="tablewrap">
          <table>
            <thead>
              <tr><th>Criterion</th><th>Requirements</th><th>Scenario</th><th>Tests</th><th>CI tests</th><th>Tasks</th>{PROOFS.map(([, l]) => <th key={l}>{l}</th>)}<th>Verified</th></tr>
            </thead>
            <tbody>
              {story.traceability.map((t) => (
                <tr key={t.criterion_id}>
                  <td><code>{t.criterion_id}</code> <span className="small">{t.text}</span></td>
                  <td>{t.requirements.join(', ') || '—'}</td><td>{t.scenario ?? '—'}</td><td>{t.tests.join(', ') || '—'}</td>
                  <td className="small">
                    {t.ciTests ? (
                      <>
                        <span className={t.ciTests.failed ? 'bad-text' : 'ok-text'}>{t.ciTests.passed} passed, {t.ciTests.failed} failed</span>
                        {t.ciTests.tests.length > 0 && <div className="muted">{t.ciTests.tests.join('; ')}</div>}
                      </>
                    ) : <span className="muted">no report</span>}
                  </td>
                  <td>{t.tasks.join(', ') || '—'}</td>
                  {PROOFS.map(([k]) => <td key={k}><YesNo v={t.proven[k]} /></td>)}
                  <td><Badge tone={t.verified ? 'ok' : 'mute'}>{t.verified ? 'VERIFIED' : 'NOT VERIFIED'}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <Empty>No spec yet, so there is nothing to trace.</Empty>}
      <p className="small muted">“Verified” needs all five proofs: deterministic evidence plus human sign-off, never an AI statement.</p>

      <h3>Jira sync receipts</h3>
      {story.jiraSync.length ? (
        <div className="tablewrap">
          <table>
            <thead><tr><th>Kind</th><th>Result</th><th>Remote id</th><th>Attempts</th><th>Operation key</th><th>Error</th><th>Updated</th></tr></thead>
            <tbody>
              {story.jiraSync.map((j) => (
                <tr key={j.operationKey}>
                  <td>{j.kind}</td><td><Badge tone={j.result === 'OK' ? 'ok' : 'bad'}>{j.result || 'PENDING'}</Badge></td><td>{j.remoteId || '—'}</td><td>{j.attempt}</td>
                  <td className="small"><code>{j.operationKey}</code></td><td className="small bad-text">{j.error}</td><td className="nowrap">{fmtTime(j.updatedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <Empty>Nothing has been written to Jira for this story.</Empty>}

      <h3>Artifacts</h3>
      {error && <ErrorBox message={error} />}
      {!ev ? !error && <Loading /> : ev.artifacts.length ? (
        <div className="tablewrap">
          <table>
            <thead><tr><th>Id</th><th>Kind</th><th>sha256</th><th>Created</th><th><span className="sr-only">View</span></th></tr></thead>
            <tbody>
              {ev.artifacts.map((a) => (
                <tr key={a.id}>
                  <td>{a.id}</td><td>{a.kind}</td><td><code title={a.sha256}>{short(a.sha256, 16)}</code></td><td className="nowrap">{fmtTime(a.createdAt)}</td>
                  <td><button type="button" className="btn small" aria-label={`View artifact ${a.id} (${a.kind})`} onClick={() => { setArtErr(''); api.artifact(a.id).then(setArt).catch((e) => setArtErr(errMsg(e))); }}>View</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <Empty>No artifacts stored.</Empty>}
      {artErr && <ErrorBox message={artErr} />}
      {art && (
        <div>
          <div className="row wrap"><strong>Artifact {art.id}: {art.kind}</strong> <code className="small">{art.uri}</code>
            <button type="button" className="btn small" onClick={() => setArt(null)}>Close</button></div>
          <CopyPre text={art.content ?? ''} />
        </div>
      )}
      {ev?.bundle != null && <details><summary>Evidence bundle preview (what would be published)</summary><CopyPre text={pretty(ev.bundle)} /></details>}
    </>
  );
}
