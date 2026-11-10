import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
import { API_BASE, api, errMsg, eventsUrl, getUser, setUser as persistUser } from './api';
import type { AdminConfig, Me, User } from './api';
import { AppCtx, useLive } from './components';
import type { AppState } from './components';
import Inbox from './pages/Inbox';
import Knowledge from './pages/Knowledge';
import CommandCenter from './pages/CommandCenter';
import StoryWorkspace from './pages/StoryWorkspace';
import Sessions from './pages/Sessions';
import Admin from './pages/Admin';

interface Toast { id: number; kind: 'ok' | 'error'; message: string }

export default function App() {
  const [user, setUserState] = useState(getUser());
  const [users, setUsers] = useState<User[]>([]);
  const [me, setMe] = useState<Me | null>(null);
  const [config, setConfig] = useState<AdminConfig | null>(null);
  const [tick, setTick] = useState(0);
  const [live, setLive] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [bootError, setBootError] = useState<string | null>(null);
  const nextId = useRef(1);

  const toast = useCallback((kind: 'ok' | 'error', message: string) => {
    const id = nextId.current++;
    setToasts((t) => [...t.slice(-4), { id, kind, message }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'error' ? 12000 : 5000);
  }, []);

  useEffect(() => {
    api.users().then(setUsers).catch((e) => setBootError(errMsg(e)));
  }, []);

  // identity + config follow the selected user and every server event
  useEffect(() => {
    let stale = false;
    Promise.all([api.me(), api.adminConfig()])
      .then(([m, c]) => {
        if (stale) return;
        setMe(m);
        setConfig(c);
        setBootError(null);
      })
      .catch((e) => !stale && setBootError(errMsg(e)));
    return () => {
      stale = true;
    };
  }, [user, tick]);

  // live updates: any audit event triggers a debounced refetch of the current view
  useEffect(() => {
    let timer: number | undefined;
    const es = new EventSource(eventsUrl(user));
    es.addEventListener('audit', () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setTick((t) => t + 1), 400);
    });
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    return () => {
      es.close();
      window.clearTimeout(timer);
      setLive(false);
    };
  }, [user]);

  const ctx = useMemo<AppState>(() => {
    const can = (p: string) => !!me?.permissions.includes(p);
    return {
      user, me, config, tick, toast, can,
      needs: (p) => (can(p) ? '' : `Needs role: ${(config?.roles[p] ?? []).join(', ') || p}. You are ${user} (${me?.roles.join(', ') || '…'}).`),
      act: async (fn, ok) => {
        try {
          const r = await fn();
          if (ok) toast('ok', typeof ok === 'function' ? ok(r) : ok);
          return r;
        } catch (e) {
          toast('error', errMsg(e));
          return undefined;
        } finally {
          setTick((t) => t + 1);
        }
      },
    };
  }, [user, me, config, tick, toast]);

  const ws = config?.workspace;
  return (
    <AppCtx.Provider value={ctx}>
      <a className="skip" href="#main">Skip to content</a>
      <header className="topbar">
        <span className="brand">Agentic Sprint Delivery</span>
        <nav aria-label="Primary">
          <NavLink to="/" end>Inbox<InboxCount /></NavLink>
          <NavLink to="/sprint">Sprint</NavLink>
          <NavLink to="/sessions">Agent sessions</NavLink>
          <NavLink to="/knowledge">Playbooks &amp; wiki</NavLink>
          <NavLink to="/admin">Administration</NavLink>
        </nav>
        <span className="grow" />
        <span className="small muted" title={`API: ${API_BASE}`}>
          Updates: {live ? 'live' : 'polling every 5 s'}
        </span>
        <label className="userpick">
          <span>Acting as</span>
          <select value={user} onChange={(e) => {
            persistUser(e.target.value);
            setMe(null);
            setUserState(e.target.value);
          }}>
            {!users.some((u) => u.name === user) && <option value={user}>{user}</option>}
            {users.map((u) => (
              <option key={u.name} value={u.name}>
                {u.display} — {u.roles.join(', ')}
              </option>
            ))}
          </select>
        </label>
      </header>
      {ws?.paused && (
        <div className="banner bad" role="status">
          <strong>Workspace paused.</strong> No new analysis or dispatch will start. Reason: {ws.pausedReason || 'not given'}
        </div>
      )}
      {bootError && (
        <div className="banner bad" role="alert">
          <strong>API problem:</strong> {bootError}
        </div>
      )}
      <main id="main">
        <Routes>
          <Route path="/" element={<Inbox />} />
          <Route path="/sprint" element={<CommandCenter />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/stories/:key" element={<StoryWorkspace />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
      </main>
      <div className="toasts" aria-live="polite" aria-atomic="false">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} role={t.kind === 'error' ? 'alert' : 'status'}>
            <span>{t.kind === 'error' ? 'Error: ' : 'Done: '}{t.message}</span>
            <button type="button" className="btn small" aria-label="Dismiss notification"
                    onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}>
              ×
            </button>
          </div>
        ))}
      </div>
    </AppCtx.Provider>
  );
}

/** Count of inbox items the current user can act on, next to the Inbox nav link. Hidden when zero. */
function InboxCount() {
  const { data } = useLive(() => api.inbox());
  if (!data?.actionable) return null;
  return (
    <span className="navcount">
      {data.actionable}
      <span className="sr-only"> item(s) need you</span>
    </span>
  );
}
