import { api } from '../api';
import { Empty, EngineSummary, ErrorBox, Loading, Panel, RunCard, TaskTable, useLive } from '../components';

const ACTIVE = ['QUEUED', 'AWAITING_MANUAL_LAUNCH', 'RUNNING'];

export default function Sessions() {
  const { data, error } = useLive(() => api.runs());
  if (error && !data) return <ErrorBox message={error} />;
  if (!data) return <Loading />;
  const active = data.runs.filter((r) => ACTIVE.includes(r.state));
  const finished = data.runs.filter((r) => !ACTIVE.includes(r.state));
  return (
    <>
      <Panel title="Agent sessions">
        {error && <ErrorBox message={error} />}
        <dl className="facts">
          <div><dt>Capacity</dt><dd>{data.activeSessions} of {data.maxActiveSessions} sessions active</dd></div>
          <div><dt>Engines</dt><dd><EngineSummary engines={data.engines} /></dd></div>
        </dl>
      </Panel>
      <Panel title={`Waiting queue (${data.queue.length})`}>
        {data.queue.length ? <TaskTable tasks={data.queue} showStory /> : <Empty>No tasks are waiting for capacity, a path lock or a retry.</Empty>}
      </Panel>
      <Panel title={`Active runs (${active.length})`}>
        {active.length ? active.map((r) => <RunCard key={r.id} run={r} showStory />) : <Empty>No active runs. Dispatch a planned story from its workspace.</Empty>}
      </Panel>
      <Panel title={`Finished runs (${finished.length})`}>
        {finished.length ? finished.map((r) => <RunCard key={r.id} run={r} showStory />) : <Empty>No finished runs yet.</Empty>}
      </Panel>
    </>
  );
}
