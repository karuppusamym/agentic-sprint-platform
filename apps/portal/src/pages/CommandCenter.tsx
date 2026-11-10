import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { ActionButton, Badge, EngineSummary, ErrorBox, GATE_TITLES, GateChips, GateLegend, Loading, Panel, fmtTime, useApp, useLive } from '../components';

const ATTENTION = ['NEEDS_HUMAN_INPUT', 'NEEDS_REFINEMENT', 'BLOCKED', 'FAILED', 'HUMAN_REVIEW', 'UAT', 'PLAN_READY', 'EVIDENCE_SYNC', 'DONE_ELIGIBLE', 'CARRYOVER', 'PAUSED'];

export default function CommandCenter() {
  const { act, can, needs } = useApp();
  const { data, error } = useLive(() => api.sprint());
  const [q, setQ] = useState('');
  const [stage, setStage] = useState('');
  const [onlyAttention, setOnlyAttention] = useState(false);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data?.stories ?? []).filter((s) => {
      if (stage && s.stage !== stage) return false;
      if (onlyAttention && !(s.blockers.length || s.awaitingManualLaunch || ATTENTION.includes(s.state) || s.gates.some((g) => g.status === 'FAIL' || g.status === 'STALE'))) return false;
      return !needle || [s.key, s.summary, s.owner, s.state, s.issueType, ...s.labels].join(' ').toLowerCase().includes(needle);
    });
  }, [data, q, stage, onlyAttention]);

  if (error && !data) return <ErrorBox message={error} />;
  if (!data) return <Loading />;
  const { sprint, jira, queue } = data;

  return (
    <>
      <Panel title="Sprint command center" actions={
        <>
          <ActionButton allowed={can('story.sync')} reason={needs('story.sync')}
                        onAct={() => act(() => api.syncSprint(), (r) => `Synced ${r.synced} issue(s) from Jira`)}>
            Sync from Jira
          </ActionButton>
          <ActionButton kind="primary" allowed={can('story.analyze')} reason={needs('story.analyze')}
                        onAct={() => act(() => api.bulkAnalyze(), (r) =>
                          `Analysis started for ${r.started.length} story(ies)` + (r.skipped.length ? `; skipped ${r.skipped.map((x) => `${x.key} (${x.reason})`).join('; ')}` : ''))}>
            Bulk analyze eligible
          </ActionButton>
        </>
      }>
        {error && <ErrorBox message={error} />}
        <dl className="facts">
          <div><dt>Sprint</dt><dd>{sprint ? <>{sprint.name} <Badge tone={sprint.state === 'active' ? 'ok' : 'warn'}>{sprint.state}</Badge> <span className="muted small">{sprint.start} → {sprint.end}</span></> : 'No sprint snapshot yet — run Sync'}</dd></div>
          <div><dt>Last Jira sync</dt><dd>{fmtTime(data.lastSyncAt)} {data.stale && <Badge tone="warn">stale — sync before acting</Badge>}</dd></div>
          <div><dt>Jira adapter</dt><dd><Badge tone={jira.adapter === 'mock' ? 'warn' : 'info'}>{jira.adapter}</Badge> {jira.write ? 'read + write' : 'read only'}, {jira.verified ? 'verified' : 'not verified'} <span className="muted small">— {jira.note}</span></dd></div>
          <div><dt>Agent sessions</dt><dd>{queue.activeSessions} of {queue.maxActiveSessions} active{queue.activeSessions >= queue.maxActiveSessions && <> <Badge tone="warn">at capacity — new tasks queue</Badge></>}</dd></div>
          <div><dt>Engines</dt><dd><EngineSummary engines={data.engines} /></dd></div>
        </dl>
      </Panel>

      <Panel title={`Stories (${rows.length} of ${data.stories.length})`} actions={<GateLegend />}>
        <div className="filters">
          <label className="field inline"><span className="field-label">Search</span>
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="key, summary, owner, label…" />
          </label>
          <label className="field inline"><span className="field-label">Stage</span>
            <select value={stage} onChange={(e) => setStage(e.target.value)}>
              <option value="">All stages</option>
              {data.stages.map((s) => <option key={s.name}>{s.name}</option>)}
            </select>
          </label>
          <label className="check"><input type="checkbox" checked={onlyAttention} onChange={(e) => setOnlyAttention(e.target.checked)} /> Only blocked / needs attention</label>
        </div>
        <div className="tablewrap">
          <table className="stories">
            <thead>
              <tr><th>Key</th><th>Summary</th><th>Type</th><th>Stage / state</th><th>Owner</th><th>Priority</th><th>Gates G0–G8</th><th>Agents</th><th>Blockers &amp; warnings</th></tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.key}>
                  <td className="nowrap"><Link to={`/stories/${s.key}`}><strong>{s.key}</strong></Link></td>
                  <td>{s.summary}{!s.inActiveSprint && <> <Badge tone="warn">not in active sprint</Badge></>}</td>
                  <td>{s.issueType}</td>
                  <td><div className="small muted">{s.stage}</div><Badge>{s.state}</Badge></td>
                  <td>{s.owner || '—'}</td>
                  <td>{s.priority}</td>
                  <td><GateChips gates={s.gates} titles={GATE_TITLES} /></td>
                  <td className="small nowrap">
                    {s.activeRuns} active · {s.queuedTasks} queued
                    {s.awaitingManualLaunch > 0 && <div><Badge tone="attn">{`${s.awaitingManualLaunch} awaiting manual launch`}</Badge></div>}
                  </td>
                  <td className="small">
                    {s.blockers.length > 0 && (
                      <div className="bad-text" title={s.blockers.join('\n')}>
                        Blocked: {s.blockers[0]}{s.blockers.length > 1 && <span className="muted"> (+{s.blockers.length - 1} more)</span>}
                      </div>
                    )}
                    {s.warnings.map((w, i) => <div key={i} className="warn-text">Warning: {w}</div>)}
                    {!s.blockers.length && !s.warnings.length && <span className="muted">none</span>}
                  </td>
                </tr>
              ))}
              {!rows.length && (
                <tr><td colSpan={9} className="muted">{data.stories.length ? 'No stories match the filters.' : 'No stories yet. Use “Sync from Jira” to load the active sprint.'}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
