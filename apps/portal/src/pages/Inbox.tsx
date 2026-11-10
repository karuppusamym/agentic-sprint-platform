import { Link } from 'react-router-dom';
import { api } from '../api';
import type { Autopilot, InboxItem } from '../api';
import { Badge, Empty, ErrorBox, Loading, Panel, fmtTime, useApp, useLive } from '../components';

/** Plain-words label, the button text and where the button goes, per inbox item kind. */
const KIND: Record<string, { label: string; go: string; tab?: string }> = {
  approve: { label: 'Approve spec and prototype', go: 'Review and approve' },
  uat: { label: 'Acceptance test', go: 'Open acceptance test', tab: 'uat' },
  close: { label: 'Authorise Done', go: 'Authorise Done', tab: 'evidence' },
  decide: { label: 'Decision needed', go: 'Open story' },
  clarify: { label: 'Clarify the story in Jira', go: 'See what is unclear' },
  publish: { label: 'Publish evidence to Jira', go: 'Publish evidence', tab: 'evidence' },
  dispatch: { label: 'Start the agents', go: 'Dispatch plan' },
  launch: { label: 'Launch an agent session', go: 'Open task package', tab: 'sessions' },
  lesson: { label: 'Proposed wiki note', go: 'Review note' },
};

export function waited(minutes: number): string {
  const m = Math.max(0, Math.round(minutes));
  if (m < 1) return 'under a minute';
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h${m % 60 ? ` ${m % 60} min` : ''}`;
  const d = Math.floor(h / 24);
  return `${d} d${h % 24 ? ` ${h % 24} h` : ''}`;
}

function target(i: InboxItem): string {
  if (i.kind === 'lesson' || !i.story) return '/knowledge';
  const tab = KIND[i.kind]?.tab;
  return `/stories/${encodeURIComponent(i.story)}${tab ? `?tab=${tab}` : ''}`;
}

function Item({ item }: { item: InboxItem }) {
  const k = KIND[item.kind] ?? { label: item.title, go: 'Open' };
  return (
    <li>
      <div className="what">
        <div className="row wrap">
          <Badge tone={item.canAct ? 'attn' : 'mute'}>{k.label}</Badge>
          {item.story && <Link to={`/stories/${encodeURIComponent(item.story)}`}><strong>{item.story}</strong></Link>}
          <span>{item.summary}</span>
        </div>
        <div className="small">{item.title !== k.label && <strong>{item.title}. </strong>}{item.detail}</div>
        {!item.canAct && item.reason && <div className="small warn-text">Waiting on someone else: {item.reason}</div>}
      </div>
      <span className="small muted nowrap" title={`Since ${fmtTime(item.since)}`}>Waiting {waited(item.ageMinutes)}</span>
      <Link className={item.canAct ? 'btn primary' : 'btn'} to={target(item)}>
        {item.canAct ? k.go : 'View'}<span className="sr-only">{item.story ? `: ${item.story}` : `: ${item.summary}`}</span>
      </Link>
    </li>
  );
}

export function AutopilotStrip({ ap }: { ap: Autopilot }) {
  if (!ap.enabled) {
    return (
      <p className="banner warn" role="status">
        <strong>Autopilot is off.</strong> Analysis, dispatch and evidence publishing all wait for a person; they appear below when they are due.
      </p>
    );
  }
  const auto = [
    ap.auto_analyze && `analyzes eligible stories (at most ${ap.max_wip} in flight)`,
    ap.auto_dispatch && 'dispatches approved plans to the agents',
    ap.auto_publish_evidence && 'publishes evidence to Jira after acceptance',
  ].filter(Boolean) as string[];
  const manual = [!ap.auto_analyze && 'analysis', !ap.auto_dispatch && 'dispatch', !ap.auto_publish_evidence && 'evidence publishing'].filter(Boolean) as string[];
  return (
    <p className="banner info" role="status">
      <strong>Autopilot is on.</strong> Without anyone clicking, it {auto.length ? auto.join(', ') : 'does nothing yet'}.
      {manual.length > 0 && <> Still manual: {manual.join(', ')}.</>} It never approves, signs an acceptance test or closes a story.
    </p>
  );
}

export default function Inbox() {
  const { user, me } = useApp();
  const { data, error } = useLive(() => api.inbox());
  if (error && !data) return <ErrorBox message={error} />;
  if (!data) return <Loading />;
  const mine = data.items.filter((i) => i.canAct);
  const others = data.items.filter((i) => !i.canAct);
  return (
    <>
      <Panel title={`Needs you (${mine.length})`} actions={<span className="small muted">Acting as {user}{me ? ` (${me.roles.join(', ')})` : ''}</span>}>
        {error && <ErrorBox message={error} />}
        <AutopilotStrip ap={data.autopilot} />
        {mine.length
          ? <ul className="inbox" aria-label="Items that need you">{mine.map((i, n) => <Item key={`${i.kind}:${i.story}:${i.runId}:${i.noteId}:${n}`} item={i} />)}</ul>
          : <p>Nothing needs you right now.</p>}
      </Panel>
      <Panel title={`Waiting on someone else (${others.length})`}>
        {others.length
          ? <ul className="inbox" aria-label="Items waiting on someone else">{others.map((i, n) => <Item key={`${i.kind}:${i.story}:${i.runId}:${i.noteId}:${n}`} item={i} />)}</ul>
          : <Empty>Nothing is waiting on other people.</Empty>}
        <p className="small muted">Looking for everything in the sprint? See <Link to="/sprint">Sprint</Link>.</p>
      </Panel>
    </>
  );
}
