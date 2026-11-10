import { useEffect, useState } from 'react';
import { API_BASE, api } from '../api';
import { ActionButton, Badge, CopyPre, Empty, ErrorBox, Field, Loading, Panel, pretty, useApp, useLive } from '../components';

/** How much of each person's attention a finished story costs: the numbers a two-person team steers by. */
const HEADLINE: [string, string, (v: number) => string][] = [
  ['human_touches_per_done_story_median', 'Human actions per finished story (median)', (v) => String(v)],
  ['human_wait_hours_per_done_story_median', 'Hours a finished story waited on people (median)', (v) => `${v} h`],
  ['machine_hours_per_done_story_median', 'Hours a finished story was worked by automation (median)', (v) => `${v} h`],
  ['share_of_lead_time_waiting_on_people', 'Share of lead time spent waiting on people', (v) => `${Math.round(v * 100)}%`],
];
const OnOff = ({ on, yes = 'on', no = 'off' }: { on: boolean | undefined; yes?: string; no?: string }) =>
  <Badge tone={on ? 'ok' : 'mute'}>{on ? yes : no}</Badge>;

const KPI_LABELS: Record<string, string> = {
  validated_done: 'Stories validated Done', lead_time_hours_median: 'Median lead time (hours)', first_pass_uat_rate: 'First-pass UAT rate',
  uat_tested_stories: 'Stories UAT-tested', automated_repair_success_rate: 'Automated repair success rate', repair_tasks: 'Repair tasks',
  prototype_versions_before_approval_median: 'Median prototype versions before approval', agent_cost_total_usd: 'Agent cost total (USD)',
  agent_cost_per_accepted_story_usd: 'Agent cost per accepted story (USD)',
};

export default function Admin() {
  const { act, can, needs, config } = useApp();
  const kpis = useLive(() => api.kpis());
  const verify = useLive(() => api.auditVerify());
  const [reason, setReason] = useState('');
  const admin = { allowed: can('admin'), reason: needs('admin') };
  if (!config) return <Loading />;
  const ws = config.workspace;
  return (
    <>
      <Panel title="Administration">
        <p className="small muted">
          Auth mode <strong>{config.authMode}</strong>{config.devMode && <> (<Badge tone="warn">dev mode</Badge> identities are not verified)</>} · database <strong>{config.database}</strong> ·
          Jira adapter <strong>{config.jira.adapter}</strong> ({config.jira.verified ? 'verified' : 'not verified'}) — {config.jira.note}
        </p>
        {!config.canAdmin && <p className="banner warn">You can view this page, but changes need one of the roles: {(config.roles.admin ?? []).join(', ')}.</p>}
      </Panel>

      <Panel title="Integration matrix">
        <div className="tablewrap">
          <table>
            <thead><tr><th>Engine</th><th>Enabled</th><th>Mode</th><th>Verified</th><th>Attended</th><th>What actually happens</th></tr></thead>
            <tbody>
              {config.engines.map((e) => {
                const fixed = e.mode === 'unavailable' || e.engine === 'simulated';
                return (
                  <tr key={e.engine}>
                    <td><strong>{e.label}</strong><div className="small muted"><code>{e.engine}</code></div></td>
                    <td className="nowrap">
                      <label className="check" title={fixed ? 'Controlled by policy.yaml, not switchable here' : !admin.allowed ? admin.reason : undefined}>
                        <input type="checkbox" checked={e.enabled} disabled={fixed || !admin.allowed}
                               onChange={(ev) => act(() => api.setEngines({ [e.engine]: ev.target.checked }), `${e.label} ${ev.target.checked ? 'enabled' : 'disabled'}`)} />
                        {e.enabled ? 'Enabled' : 'Disabled'}
                      </label>
                    </td>
                    <td><Badge tone={e.mode === 'api' ? 'info' : e.mode === 'unavailable' ? 'mute' : 'warn'}>{e.mode}</Badge></td>
                    <td>{e.verified ? '✓ verified' : '✗ not verified'}</td>
                    <td>{e.attended ? 'Attended' : 'Unattended'}</td>
                    <td className="small">{e.note}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="small muted">At least one engine must stay enabled; the server rejects disabling the last one.</p>
      </Panel>

      <div className="split">
        <Panel title="Generation">
          {config.generation ? (
            <>
              <dl className="kv">
                <dt>Configured provider</dt><dd>{config.generation.provider}</dd>
                <dt>Active now</dt>
                <dd>{config.generation.active} {config.generation.active !== config.generation.provider && <Badge tone="warn">fallback in use</Badge>}</dd>
                {config.generation.model && (<><dt>Model</dt><dd><code>{config.generation.model}</code></dd></>)}
              </dl>
              {config.generation.note && <p className="small">{config.generation.note}</p>}
            </>
          ) : <Empty>The API did not report a generation status.</Empty>}
        </Panel>
        <Panel title="Autopilot, CI checks and team (read-only)">
          <dl className="kv">
            <dt>Autopilot</dt><dd><OnOff on={config.autopilot?.enabled} /></dd>
            <dt>Analyze eligible stories</dt><dd><OnOff on={config.autopilot?.enabled && config.autopilot.auto_analyze} yes="automatic" no="manual" /></dd>
            <dt>Dispatch approved plans</dt><dd><OnOff on={config.autopilot?.enabled && config.autopilot.auto_dispatch} yes="automatic" no="manual" /></dd>
            <dt>Publish evidence to Jira</dt><dd><OnOff on={config.autopilot?.enabled && config.autopilot.auto_publish_evidence} yes="automatic" no="manual" /></dd>
            <dt>Stories in flight (max)</dt><dd>{config.autopilot?.max_wip ?? '—'}</dd>
            {Object.entries(config.ci?.checks ?? {}).map(([g, names]) => (
              <div key={g} style={{ display: 'contents' }}>
                <dt>{g} needs checks</dt><dd>{names.length ? names.map((n) => <code key={n}>{n}</code>).reduce<JSX.Element[]>((a, x, i) => (i ? [...a, <span key={`s${i}`}>, </span>, x] : [x]), []) : '—'}</dd>
              </div>
            ))}
            <dt>Four-eyes rule</dt>
            <dd><OnOff on={config.team?.four_eyes} /> {config.team?.four_eyes ? 'whoever approves a baseline cannot sign its acceptance test' : 'the approver may also sign the acceptance test'}</dd>
          </dl>
          <p className="small muted">Set in <code>config/policy.yaml</code> (<code>autopilot</code>, <code>ci.checks</code>, <code>team</code>); restart the API to change them.</p>
        </Panel>
      </div>

      <Panel title="Git and CI integration">
        <dl className="kv">
          <dt>GitHub webhook URL</dt><dd><code>{API_BASE}/api/v1/webhooks/github</code></dd>
          <dt>Webhook signature</dt>
          <dd>
            <OnOff on={config.integrations?.githubWebhookSigned} yes="verified" no="not configured" />{' '}
            <span className="small">{config.integrations?.githubWebhookSigned
              ? 'Deliveries are checked against the shared secret.'
              : <>Set <code>ASDP_GITHUB_WEBHOOK_SECRET</code> on the API to the secret entered in the GitHub webhook, then restart. Without it, webhooks are only accepted in dev mode.</>}</span>
          </dd>
          <dt>Automation token</dt>
          <dd>
            <OnOff on={config.integrations?.automationToken} yes="set" no="not set" />{' '}
            <span className="small">{config.integrations?.automationToken
              ? 'CI can post JUnit reports and gate results with a Bearer token.'
              : <>Set <code>ASDP_AUTOMATION_TOKEN</code> on the API and send it from CI as <code>Authorization: Bearer …</code>, then restart.</>}</span>
          </dd>
        </dl>
        <p className="small muted">Give GitHub the webhook URL with content type JSON and the events “Pull requests” and “Check runs”. The URL uses the API address this portal was built with; use the address GitHub can reach.</p>
      </Panel>

      <div className="split">
        <Panel title="Workspace kill switch">
          <p>{ws.name} ({ws.project}) is <Badge tone={ws.paused ? 'bad' : 'ok'}>{ws.paused ? 'PAUSED' : 'RUNNING'}</Badge>{ws.paused && ws.pausedReason && <> — {ws.pausedReason}</>}</p>
          <Field label="Reason"><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why automation is being paused" /></Field>
          <div className="row wrap">
            {ws.paused
              ? <ActionButton kind="primary" {...admin} showReason onAct={() => act(() => api.pauseWorkspace(false, ''), 'Workspace resumed')}>Resume workspace</ActionButton>
              : <ActionButton kind="danger" {...admin} showReason onAct={() => act(() => api.pauseWorkspace(true, reason), 'Workspace paused: no new analysis or dispatch')}>Pause workspace</ActionButton>}
            <ActionButton {...admin} onAct={() => act(() => api.reconcile(), (r) => `Reconciled: ${Object.entries(r).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(', ')}`)}>Reconcile now</ActionButton>
          </div>
        </Panel>
        <Panel title="Audit chain">
          {verify.error && <ErrorBox message={verify.error} />}
          {verify.data ? (
            <p>
              <Badge tone={verify.data.ok ? 'ok' : 'bad'}>{verify.data.ok ? 'INTACT' : 'BROKEN'}</Badge> {verify.data.events} event(s) verified against the hash chain.
              {!verify.data.ok && <strong className="bad-text"> Chain breaks at event {verify.data.broken_at}.</strong>}
            </p>
          ) : !verify.error && <Loading />}
          <button type="button" className="btn" onClick={() => void verify.reload()}>Verify again</button>
        </Panel>
      </div>

      <Panel title="KPIs">
        {kpis.error && <ErrorBox message={kpis.error} />}
        {kpis.data ? (
          <>
            <dl className="facts headline">
              {HEADLINE.map(([k, label, fmt]) => (
                <div key={k}><dt>{label}</dt><dd>{kpis.data![k] == null ? <span className="muted">no finished story yet</span> : fmt(Number(kpis.data![k]))}</dd></div>
              ))}
            </dl>
            <dl className="facts">
              {Object.entries(KPI_LABELS).map(([k, label]) => (
                <div key={k}><dt>{label}</dt><dd>{kpis.data![k] == null ? <span className="muted">no data yet</span> : k.endsWith('_rate') ? `${Math.round(Number(kpis.data![k]) * 100)}%` : String(kpis.data![k])}</dd></div>
              ))}
            </dl>
            <div className="split">
              <div><h3>Stories by state</h3><KV obj={kpis.data.stories_by_state} /></div>
              <div><h3>Runs by engine</h3><KV obj={kpis.data.runs_by_engine} /></div>
            </div>
            <h3>Not measured</h3>
            <ul className="idlist">{(kpis.data.not_measured ?? []).map((n) => <li key={n}><Badge tone="mute">not measured</Badge> {n}</li>)}</ul>
          </>
        ) : !kpis.error && <Loading />}
      </Panel>

      <div className="split">
        <Panel title="Policy (read-only)">
          <p className="small muted">Loaded from <code>config/policy.yaml</code>. Edit the file and restart the API to change it.</p>
          <CopyPre text={pretty(config.policy)} />
        </Panel>
        <Panel title="Roles & permissions">
          <div className="tablewrap">
            <table>
              <thead><tr><th>Permission</th><th>Roles</th></tr></thead>
              <tbody>{Object.entries(config.roles).map(([p, roles]) => <tr key={p}><td><code>{p}</code></td><td>{roles.join(', ')}</td></tr>)}</tbody>
            </table>
          </div>
          <p className="small muted">Approve, UAT and the Done transition additionally require a human identity; automation can never perform them.</p>
        </Panel>
      </div>

      {config.mockJira && <MockJiraPanel />}
    </>
  );
}

function KV({ obj }: { obj: unknown }) {
  const entries = Object.entries((obj as Record<string, unknown>) ?? {});
  if (!entries.length) return <Empty>No data yet.</Empty>;
  return (
    <dl className="kv">
      {entries.map(([k, v]) => (
        <div key={k} style={{ display: 'contents' }}>
          <dt>{k}</dt>
          <dd>{typeof v === 'object' && v ? Object.entries(v).map(([a, b]) => `${a} ${typeof b === 'number' ? Math.round(b * 100) / 100 : b}`).join(' · ') : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function MockJiraPanel() {
  const { act } = useApp();
  const { data, error } = useLive(() => api.mockJira());
  const [faults, setFaults] = useState({ failWrites: '0', failAfterWrite: '0' });
  const [key, setKey] = useState('');
  const [edit, setEdit] = useState({ criteria: '', labels: '', status: '' });
  const issue = data?.issues.find((i) => i.key === key);
  // load the form from the issue only when a different issue is chosen, so polling never overwrites typing
  useEffect(() => {
    if (issue) setEdit({ criteria: issue.criteria.join('\n'), labels: issue.labels.join(', '), status: issue.status });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, !!issue]);
  if (error && !data) return <Panel title="Mock Jira controls"><ErrorBox message={error} /></Panel>;
  if (!data) return <Panel title="Mock Jira controls"><Loading /></Panel>;
  const comments = Object.entries(data.comments);
  return (
    <Panel title="Mock Jira controls" actions={<Badge tone="warn">dev only — no real Jira tenant</Badge>}>
      <div className="split">
        <div>
          <h3>Sprint</h3>
          <p>{data.sprint.name} is <Badge tone={data.sprint.state === 'active' ? 'ok' : 'warn'}>{data.sprint.state ?? 'unknown'}</Badge></p>
          <div className="row wrap">
            <ActionButton onAct={() => act(() => api.mockSprint('closed'), 'Mock sprint closed. Sync to see carry-over.')}>Close sprint</ActionButton>
            <ActionButton onAct={() => act(() => api.mockSprint('active'), 'Mock sprint reopened. Sync to pick it up.')}>Reopen sprint</ActionButton>
          </div>
          <h3>Inject write faults</h3>
          <p className="small muted">Currently: fail next {data.failWrites} write(s); {data.failAfterWrite} write(s) succeed in Jira but report failure.</p>
          <div className="formrow">
            <Field label="Fail next N writes"><input type="number" min="0" value={faults.failWrites} onChange={(e) => setFaults({ ...faults, failWrites: e.target.value })} /></Field>
            <Field label="Fail after write (N)"><input type="number" min="0" value={faults.failAfterWrite} onChange={(e) => setFaults({ ...faults, failAfterWrite: e.target.value })} /></Field>
            <ActionButton onAct={() => act(() => api.mockFaults(Number(faults.failWrites) || 0, Number(faults.failAfterWrite) || 0), 'Fault injection updated')}>Apply faults</ActionButton>
          </div>
        </div>
        <form className="formgrid" onSubmit={(e) => e.preventDefault()}>
          <h3>Edit a mock issue</h3>
          <Field label="Issue">
            <select value={key} onChange={(e) => setKey(e.target.value)}>
              <option value="">Choose an issue…</option>
              {data.issues.map((i) => <option key={i.key} value={i.key}>{i.key} — {i.status}</option>)}
            </select>
          </Field>
          <Field label="Acceptance criteria (one per line)"><textarea rows={5} value={edit.criteria} disabled={!issue} onChange={(e) => setEdit({ ...edit, criteria: e.target.value })} /></Field>
          <Field label="Labels (comma separated)"><input value={edit.labels} disabled={!issue} onChange={(e) => setEdit({ ...edit, labels: e.target.value })} /></Field>
          <Field label="Status"><input value={edit.status} disabled={!issue} onChange={(e) => setEdit({ ...edit, status: e.target.value })} /></Field>
          <div className="row wrap">
            <ActionButton allowed={!!issue} reason="Choose an issue first" onAct={() => act(() => api.mockIssue(key, {
              acceptance_criteria: edit.criteria.split('\n').map((s) => s.trim()).filter(Boolean),
              labels: edit.labels.split(',').map((s) => s.trim()).filter(Boolean), status: edit.status.trim(),
            }), `${key} updated in mock Jira. Sync the story to see the effect (approvals may become stale).`)}>Save issue</ActionButton>
          </div>
        </form>
      </div>
      <h3>Comments written to mock Jira</h3>
      {comments.length ? comments.map(([k, list]) => (
        <details key={k}>
          <summary>{k} ({list.length})</summary>
          {list.map((c) => <CopyPre key={c.id} text={`#${c.id}\n${c.body}`} />)}
        </details>
      )) : <Empty>No comments have been written yet. Evidence comments appear here after “Publish evidence to Jira”.</Empty>}
    </Panel>
  );
}
