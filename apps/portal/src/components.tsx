import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api, errMsg } from './api';
import type { AdminConfig, AuditEvent, Engine, GateChip, GateDetail, Me, Run, Task } from './api';

// ---------------------------------------------------------------- app context
export interface AppState {
  user: string;
  me: Me | null;
  config: AdminConfig | null;
  tick: number;
  toast(kind: 'ok' | 'error', message: string): void;
  /** Run a mutation: toast the outcome (server message on failure) and refresh every live view. */
  act<T>(fn: () => Promise<T>, ok?: string | ((r: T) => string)): Promise<T | undefined>;
  can(permission: string): boolean;
  /** Plain-language reason the current user lacks a permission. */
  needs(permission: string): string;
}
export const AppCtx = createContext<AppState | null>(null);
export function useApp(): AppState {
  const ctx = useContext(AppCtx);
  if (!ctx) throw new Error('AppCtx missing');
  return ctx;
}

/** Fetch on mount, when deps change, on every server event (tick) and every 5 s as a fallback. */
export function useLive<T>(fetcher: () => Promise<T>, deps: unknown[] = [], enabled = true) {
  const { tick, user } = useApp();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fn = useRef(fetcher);
  fn.current = fetcher;
  const seq = useRef(0);
  const load = useCallback(async () => {
    const n = ++seq.current;
    try {
      const d = await fn.current();
      if (n === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (n === seq.current) setError(errMsg(e));
    }
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setData(null), deps);
  useEffect(() => {
    if (!enabled) return;
    void load();
    const id = window.setInterval(() => {
      if (!document.hidden) void load();
    }, 5000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, user, enabled, load, ...deps]);
  return { data, error, reload: load };
}

// ---------------------------------------------------------------- formatting
export function fmtTime(s: string | null | undefined): string {
  if (!s) return '—';
  const d = new Date(/(Z|[+-]\d\d:\d\d)$/.test(s) ? s : s + 'Z'); // the API sometimes omits the UTC suffix
  return isNaN(d.getTime()) ? s : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
}
export const short = (hash: string | null | undefined, n = 10) => (hash ? hash.slice(0, n) : '—');
export const money = (n: number) => `$${(n || 0).toFixed(2)}`;
export const pretty = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v, null, 1));
export const safeUrl = (u: string | null | undefined) => (u && /^https?:\/\//i.test(u) ? u : null);

export function ExtLink({ href, children }: { href: string | null | undefined; children?: ReactNode }) {
  const u = safeUrl(href);
  if (!u) return <span className="muted">{href || '—'}</span>;
  return (
    <a href={u} target="_blank" rel="noreferrer noopener">
      {children ?? u}
    </a>
  );
}

// ---------------------------------------------------------------- small pieces
const STATE_TONE: Record<string, string> = {
  DONE: 'ok', DONE_ELIGIBLE: 'ok', ELIGIBLE: 'info', HUMAN_REVIEW: 'attn', UAT: 'attn', PLAN_READY: 'attn', EVIDENCE_SYNC: 'attn',
  NEEDS_HUMAN_INPUT: 'bad', NEEDS_REFINEMENT: 'warn', BLOCKED: 'bad', FAILED: 'bad', CANCELLED: 'mute', PAUSED: 'warn',
  CARRYOVER: 'warn', DISCOVERED: 'mute', CHANGE_REQUESTED: 'warn', REWORK: 'warn',
  SUCCEEDED: 'ok', RUNNING: 'info', QUEUED: 'info', AWAITING_MANUAL_LAUNCH: 'attn', WAITING: 'warn', PENDING: 'mute',
  PASS: 'ok', FAIL: 'bad', STALE: 'warn', OK: 'ok', APPROVE: 'ok', REQUEST_CHANGES: 'warn', SUPERSEDED: 'mute',
};
export function Badge({ children, tone, title }: { children: ReactNode; tone?: string; title?: string }) {
  const t = tone ?? (typeof children === 'string' ? STATE_TONE[children] : undefined) ?? 'info';
  return (
    <span className={`badge tone-${t}`} title={title}>
      {children}
    </span>
  );
}
export const human = (s: string) => s.replace(/_/g, ' ').toLowerCase();

const GLYPH: Record<string, string> = { PASS: '✓', FAIL: '✗', PENDING: '·', STALE: '↻' };
/** Nine individual gate chips. Status is always spelled out (glyph + tooltip + screen-reader text). */
export function GateChips({ gates, titles }: { gates: GateChip[]; titles?: Record<string, string> }) {
  return (
    <span className="gates" role="list" aria-label="Quality gates">
      {gates.map((g) => (
        <span key={g.gate} role="listitem" className={`gate gate-${g.status}`}
              title={`${g.gate}${titles?.[g.gate] ? ' ' + titles[g.gate] : ''}: ${g.status}`}>
          {g.gate}
          <span aria-hidden="true">{GLYPH[g.status] ?? '?'}</span>
          <span className="sr-only"> {g.status}</span>
        </span>
      ))}
    </span>
  );
}
export const GATE_TITLES: Record<string, string> = {
  G0: 'Eligibility', G1: 'Spec completeness', G2: 'Playable prototype', G3: 'Human approval', G4: 'Code quality',
  G5: 'Integration / security', G6: 'Staging fidelity', G7: 'Human UAT', G8: 'Evidence + Jira',
};
export function GateLegend() {
  return (
    <span className="muted small">
      Gates: <span className="gate gate-PASS">✓ pass</span> <span className="gate gate-FAIL">✗ fail</span>{' '}
      <span className="gate gate-PENDING">· pending</span> <span className="gate gate-STALE">↻ stale</span>
    </span>
  );
}
export function GateChecklist({ gates }: { gates: GateDetail[] }) {
  return (
    <ul className="gatelist">
      {gates.map((g) => (
        <li key={g.gate}>
          <div className="row">
            <strong>{g.gate}</strong> <span className="grow">{g.title}</span> <Badge>{g.status}</Badge>
          </div>
          {(g.summary || g.actor) && (
            <div className="muted small">
              {g.summary}
              {g.actor && <> · by {g.actor}</>}
              {g.commit && <> · commit <code>{short(g.commit)}</code></>}
              {g.checked_at && <> · {fmtTime(g.checked_at)}</>}
            </div>
          )}
          {g.status === 'STALE' && <div className="small warn-text">Stale: the thing this result judged has changed; it must be re-established.</div>}
        </li>
      ))}
    </ul>
  );
}

export function Panel({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className ?? ''}`}>
      {(title || actions) && (
        <header className="panel-head">
          <h2>{title}</h2>
          <div className="row">{actions}</div>
        </header>
      )}
      {children}
    </section>
  );
}
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="muted small">{hint}</span>}
    </label>
  );
}
export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="banner bad" role="alert">
      <strong>Error:</strong> {message}
    </div>
  );
}
export const Loading = () => <p className="muted" role="status">Loading…</p>;
export const Empty = ({ children }: { children: ReactNode }) => <p className="muted small">{children}</p>;

/** A button that is never hidden: when not allowed it is disabled and says why (tooltip + optional inline text). */
export function ActionButton(props: {
  onAct: () => Promise<unknown> | void; allowed?: boolean; reason?: string; children: ReactNode; kind?: 'primary' | 'danger';
  showReason?: boolean; type?: 'button' | 'submit';
  /** When set, the first click only asks this question inline; the action runs on the explicit confirmation. */
  confirm?: string;
}) {
  const { allowed = true, reason, kind, showReason } = props;
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true; // StrictMode runs effects twice in dev; re-arm after the simulated unmount
    return () => { alive.current = false; };
  }, []);
  return (
    <>
      <button type={props.type ?? 'button'} className={kind ? `btn ${kind}` : 'btn'} disabled={!allowed || busy}
              title={!allowed ? reason : undefined} aria-busy={busy}
              onClick={async () => {
                if (props.confirm && !asking) return setAsking(true);
                setAsking(false);
                setBusy(true);
                try {
                  await props.onAct();
                } finally {
                  if (alive.current) setBusy(false);
                }
              }}>
        {asking ? 'Yes, confirm' : props.children}
      </button>
      {asking && (
        <span className="confirm" role="alert">
          {props.confirm} <button type="button" className="btn small" onClick={() => setAsking(false)}>No, keep as is</button>
        </span>
      )}
      {!allowed && showReason && reason && <span className="muted small why">{reason}</span>}
    </>
  );
}

export function CopyPre({ text, label = 'Copy' }: { text: string; label?: string }) {
  const { toast } = useApp();
  return (
    <div className="copypre">
      <button type="button" className="btn small" onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast('ok', 'Copied to clipboard');
        } catch {
          toast('error', 'Clipboard is not available; select the text and copy it manually.');
        }
      }}>
        {label}
      </button>
      <pre tabIndex={0}>{text}</pre>
    </div>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: ReactNode }[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} type="button" role="tab" id={`tab-${t.id}`} aria-selected={active === t.id}
                aria-controls={`tabpanel-${t.id}`} className={active === t.id ? 'tab active' : 'tab'} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function EngineSummary({ engines }: { engines: Engine[] }) {
  return (
    <span className="chips">
      {engines.map((e) => (
        <span key={e.engine} className={`chip ${e.enabled ? '' : 'off'}`} title={e.note}>
          {e.label}: {e.enabled ? `enabled, ${e.mode}` : e.mode === 'unavailable' ? 'unavailable' : 'disabled'}
        </span>
      ))}
    </span>
  );
}

export function Timeline({ events }: { events: AuditEvent[] }) {
  if (!events.length) return <Empty>No audit events yet.</Empty>;
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr><th>When</th><th>Event</th><th>Actor</th><th>State change</th><th>Detail</th></tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td className="nowrap">{fmtTime(e.timestamp)}</td>
              <td><code>{e.event}</code></td>
              <td>{e.actor}</td>
              <td className="nowrap">{e.oldState || e.newState ? `${e.oldState || '—'} → ${e.newState || '—'}` : ''}</td>
              <td className="small detail">{e.detail && Object.keys(e.detail).length ? JSON.stringify(e.detail) : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- tasks + runs (shared by story workspace and sessions page)
export function TaskTable({ tasks, showStory }: { tasks: Task[]; showStory?: boolean }) {
  if (!tasks.length) return <Empty>No tasks.</Empty>;
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>{showStory && <th>Story</th>}<th>Task</th><th>Kind</th><th>Status</th><th>Engine</th><th>Depends on</th><th>Owner paths</th><th>Blocker</th></tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id}>
              {showStory && <td>{t.story && <Link to={`/stories/${t.story}`}>{t.story}</Link>}</td>}
              <td><strong>{t.key}</strong><div className="small muted">{t.title}</div></td>
              <td>{t.kind}</td>
              <td><Badge>{t.status}</Badge></td>
              <td>{t.engine}</td>
              <td>{t.deps.join(', ') || '—'}</td>
              <td className="small"><code>{t.ownerPaths.join(' ')}</code></td>
              <td className="small">{t.blocker || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const ACTIVE = ['QUEUED', 'AWAITING_MANUAL_LAUNCH', 'RUNNING'];
const MODE_NOTE: Record<string, string> = {
  manual: 'Manual handoff: a person launches and reports this session. The portal does not drive the engine.',
  simulated: 'Simulated demo engine: produces no code.',
  api: 'Dispatched through the engine API.',
};

export function RunCard({ run, showStory }: { run: Run; showStory?: boolean }) {
  const { act, can, needs } = useApp();
  const active = ACTIVE.includes(run.state);
  const awaiting = run.state === 'AWAITING_MANUAL_LAUNCH';
  const [url, setUrl] = useState('');
  const [rep, setRep] = useState({ state: 'SUCCEEDED', commit: '', prUrl: '', cost: '', failure: '' });
  const [open, setOpen] = useState(awaiting);
  const canReport = can('run.report');
  const canCancel = can('run.cancel');
  return (
    <article className="run">
      <div className="row wrap">
        <strong>Run #{run.id}</strong>
        {showStory && <Link to={`/stories/${run.story}`}>{run.story}</Link>}
        <span>task <code>{run.task}</code> ({run.taskKind})</span>
        <span>{run.engineLabel}</span>
        <Badge tone={run.mode === 'api' ? 'info' : 'warn'} title={MODE_NOTE[run.mode]}>{`mode: ${run.mode}`}</Badge>
        <Badge>{run.state}</Badge>
        <span className="grow" />
        <span className="muted small">attempt {run.attempt} · {money(run.cost)} · by {run.createdBy} · {fmtTime(run.createdAt)}</span>
      </div>
      <dl className="kv">
        <dt>Branch</dt><dd><code>{run.branch}</code></dd>
        <dt>Session</dt><dd>{run.externalUrl ? <ExtLink href={run.externalUrl} /> : <span className="muted">none attached</span>}</dd>
        {run.commit && (<><dt>Commit</dt><dd><code>{run.commit}</code></dd></>)}
        {run.prUrl && (<><dt>PR</dt><dd><ExtLink href={run.prUrl} /></dd></>)}
        <dt>Last event</dt><dd>{run.lastEvent || '—'}</dd>
        {run.failure && (<><dt>Failure</dt><dd className="bad-text">{run.failure}</dd></>)}
        <dt>Allowed paths</dt><dd><code>{run.allowedPaths.join(' ')}</code></dd>
      </dl>
      {active && (
        <button type="button" className="btn small" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Hide' : 'Show'} task package and actions
        </button>
      )}
      {active && open && (
        <div className="run-actions">
          {awaiting && (
            <p className="banner attn">
              Awaiting manual launch. {run.attended
                ? 'Attended engine: check out the branch, paste the task package into the editor agent, then report the resulting commit here.'
                : 'Launch the session yourself with the task package below, then attach its real URL before reporting a result.'}
            </p>
          )}
          {run.brief && <CopyPre text={run.brief} label="Copy task package" />}
          {awaiting && !run.attended && (
            <form className="formrow" onSubmit={(e) => e.preventDefault()}>
              <Field label="Session URL (https)">
                <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://app.devin.ai/sessions/…" />
              </Field>
              <ActionButton allowed={canReport} reason={needs('run.report')} showReason
                            onAct={() => act(() => api.attachRun(run.id, url.trim()), 'Session attached; run is now RUNNING')}>
                Attach session URL
              </ActionButton>
            </form>
          )}
          <form className="formgrid" onSubmit={(e) => e.preventDefault()}>
            <Field label="Result">
              <select value={rep.state} onChange={(e) => setRep({ ...rep, state: e.target.value })}>
                <option value="SUCCEEDED">SUCCEEDED</option>
                <option value="FAILED">FAILED</option>
              </select>
            </Field>
            <Field label="Head commit (7–40 hex)">
              <input value={rep.commit} onChange={(e) => setRep({ ...rep, commit: e.target.value })} placeholder="a1b2c3d" />
            </Field>
            <Field label="PR URL">
              <input value={rep.prUrl} onChange={(e) => setRep({ ...rep, prUrl: e.target.value })} placeholder="https://github.com/org/repo/pull/1" />
            </Field>
            <Field label="Cost (USD)">
              <input type="number" min="0" step="0.01" value={rep.cost} onChange={(e) => setRep({ ...rep, cost: e.target.value })} />
            </Field>
            <Field label="Failure reason (if failed)">
              <input value={rep.failure} onChange={(e) => setRep({ ...rep, failure: e.target.value })} />
            </Field>
            <div className="row wrap end">
              <ActionButton kind="primary" allowed={canReport} reason={needs('run.report')} showReason
                            onAct={() => act(() => api.reportRun(run.id, { state: rep.state, commit: rep.commit.trim(), prUrl: rep.prUrl.trim(),
                              cost: Number(rep.cost) || 0, failure: rep.failure }), (r) => `Run #${r.id} reported ${r.state}`)}>
                Report result
              </ActionButton>
              <ActionButton kind="danger" allowed={canCancel} reason={needs('run.cancel')} showReason
                            onAct={() => act(() => api.cancelRun(run.id, 'cancelled from portal'), 'Run cancelled')}>
                Cancel run
              </ActionButton>
            </div>
          </form>
        </div>
      )}
    </article>
  );
}
