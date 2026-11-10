import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errMsg } from '../api';
import type { KnowledgeNote } from '../api';
import { ActionButton, Badge, CopyPre, Empty, ErrorBox, Field, Loading, Panel, fmtTime, short, useApp, useLive } from '../components';

const SOURCE: Record<string, string> = {
  file: 'file (docs/wiki, changes through pull requests)', manual: 'manual (written in the portal)', learned: 'learned (proposed from a failure)',
};

function NoteHead({ note }: { note: KnowledgeNote }) {
  return (
    <>
      <div className="row wrap">
        <strong>{note.title}</strong>
        <Badge tone={note.status === 'pending' ? 'attn' : 'ok'}>{note.status === 'pending' ? 'PENDING REVIEW' : 'ACTIVE'}</Badge>
        {note.storyKey && <Link to={`/stories/${encodeURIComponent(note.storyKey)}`}>{note.storyKey}</Link>}
      </div>
      <div className="small muted">
        Tags: {note.tags.length ? note.tags.join(', ') : 'none (applies to all work)'} · Source: {SOURCE[note.source] ?? note.source} · by {note.createdBy || '—'} · {fmtTime(note.createdAt)}
      </div>
    </>
  );
}

function PendingNote({ note }: { note: KnowledgeNote }) {
  const { act, can, needs } = useApp();
  const [body, setBody] = useState(note.body);
  const gate = { allowed: can('knowledge.manage'), reason: needs('knowledge.manage') };
  return (
    <li className="note" id={`note-${note.id}`}>
      <NoteHead note={note} />
      <Field label={`Note text for “${note.title}” (edit before activating)`}>
        <textarea rows={5} value={body} maxLength={4000} onChange={(e) => setBody(e.target.value)} />
      </Field>
      <div className="row wrap">
        <ActionButton kind="primary" {...gate} showReason
                      onAct={() => act(() => api.noteStatus(note.id, { status: 'active', body: body.trim() || undefined }), `“${note.title}” is now active guidance for agents`)}>
          Activate
        </ActionButton>
        <ActionButton {...gate} confirm={`Archive “${note.title}”? It will never be given to agents.`}
                      onAct={() => act(() => api.noteStatus(note.id, { status: 'archived' }), `“${note.title}” archived`)}>
          Archive
        </ActionButton>
      </div>
    </li>
  );
}

function ActiveNote({ note }: { note: KnowledgeNote }) {
  const { act, can, needs } = useApp();
  return (
    <li className="note" id={`note-${note.id}`}>
      <NoteHead note={note} />
      <details>
        <summary>Note text</summary>
        <pre tabIndex={0}>{note.body}</pre>
      </details>
      {note.source !== 'file' && (
        <div className="row wrap">
          <ActionButton allowed={can('knowledge.manage')} reason={needs('knowledge.manage')} showReason
                        confirm={`Archive “${note.title}”? Agents will stop receiving it.`}
                        onAct={() => act(() => api.noteStatus(note.id, { status: 'archived' }), `“${note.title}” archived`)}>
            Archive
          </ActionButton>
        </div>
      )}
    </li>
  );
}

export default function Knowledge() {
  const { act, can, needs } = useApp();
  const playbooks = useLive(() => api.playbooks());
  const wiki = useLive(() => api.knowledge());
  const [form, setForm] = useState({ title: '', body: '', tags: '' });
  const [md, setMd] = useState<{ markdown: string; notes: number } | null>(null);
  const [mdErr, setMdErr] = useState('');
  const notes = wiki.data?.notes ?? [];
  const pending = notes.filter((n) => n.status === 'pending');
  const active = notes.filter((n) => n.status === 'active');
  const ready = !!form.title.trim() && !!form.body.trim();

  return (
    <>
      <Panel title="Playbooks">
        <p className="small muted">
          The same playbook goes into every engine's task package (Devin, VS Code Copilot, VS Code Claude). {playbooks.data?.note ?? 'Playbooks change through pull requests.'}
        </p>
        {playbooks.error && <ErrorBox message={playbooks.error} />}
        {!playbooks.data ? !playbooks.error && <Loading /> : playbooks.data.playbooks.length ? (
          <ul className="idlist">
            {playbooks.data.playbooks.map((p) => (
              <li key={p.id}>
                <div className="row wrap">
                  <strong>{p.title}</strong> <code>{p.id}</code>
                  <span className="small muted">sha256 <code title={p.sha256}>{short(p.sha256, 8)}</code></span>
                  {p.devinPlaybookId
                    ? <span className="small">Devin playbook <code>{p.devinPlaybookId}</code></span>
                    : <span className="small muted">not mapped to a Devin playbook</span>}
                </div>
                <details>
                  <summary>Playbook text</summary>
                  <pre tabIndex={0}>{p.body}</pre>
                </details>
              </li>
            ))}
          </ul>
        ) : <Empty>No playbooks found under playbooks/.</Empty>}
      </Panel>

      <Panel title="Project wiki" actions={
        <button type="button" className="btn" onClick={() => { setMdErr(''); api.knowledgeExport().then(setMd).catch((e) => setMdErr(errMsg(e))); }}>
          Export as Markdown
        </button>
      }>
        <p className="small muted">
          Pending lessons are proposed from failed acceptance tests and exhausted repairs; they do nothing until a person activates them. Active notes are added to agent task packages when their tags match the work.
        </p>
        {wiki.error && <ErrorBox message={wiki.error} />}
        {mdErr && <ErrorBox message={mdErr} />}
        {md && (
          <div>
            <div className="row wrap">
              <strong>Markdown export ({md.notes} active note{md.notes === 1 ? '' : 's'})</strong>
              <button type="button" className="btn small" onClick={() => setMd(null)}>Close</button>
            </div>
            <CopyPre text={md.markdown || '(no active notes)'} />
          </div>
        )}
        {!wiki.data ? !wiki.error && <Loading /> : (
          <>
            <h3>Pending review ({pending.length})</h3>
            {pending.length ? <ul className="idlist">{pending.map((n) => <PendingNote key={n.id} note={n} />)}</ul> : <Empty>No proposed lessons are waiting for review.</Empty>}
            <h3>Active ({active.length})</h3>
            {active.length ? <ul className="idlist">{active.map((n) => <ActiveNote key={n.id} note={n} />)}</ul> : <Empty>No active notes yet.</Empty>}
          </>
        )}
      </Panel>

      <Panel title="Add note">
        <form className="formgrid narrow" onSubmit={(e) => e.preventDefault()}>
          <Field label="Title"><input value={form.title} maxLength={200} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <Field label="Tags (comma separated)" hint="Leave empty for guidance that applies to all work.">
            <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="e.g. billing, ui" />
          </Field>
          <Field label="Body (under 4000 characters)">
            <textarea rows={5} value={form.body} maxLength={4000} onChange={(e) => setForm({ ...form, body: e.target.value })} />
          </Field>
          <div className="row wrap">
            <ActionButton kind="primary" showReason allowed={can('knowledge.manage') && ready}
                          reason={can('knowledge.manage') ? 'A note needs a title and a body.' : needs('knowledge.manage')}
                          onAct={async () => {
                            const r = await act(() => api.addNote({
                              title: form.title.trim(), body: form.body.trim(), tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean),
                            }), (n) => `Note “${n.title}” added and active`);
                            if (r) setForm({ title: '', body: '', tags: '' });
                          }}>
              Add note
            </ActionButton>
          </div>
        </form>
      </Panel>
    </>
  );
}
