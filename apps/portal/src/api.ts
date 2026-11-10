// Typed fetch client for the ASDP API. Shapes mirror services/api/asdp/main.py serializers.

// Dev server: the API on localhost:8000. Production build: the same origin that serves the portal,
// unless VITE_API_BASE names another one at build time.
export const API_BASE: string =
  ((import.meta.env.VITE_API_BASE as string | undefined) ?? (import.meta.env.PROD ? '' : 'http://localhost:8000')).replace(/\/+$/, '');
const API = `${API_BASE}/api/v1`;
const USER_KEY = 'asdp.user';

let currentUser = 'lena';
try {
  currentUser = localStorage.getItem(USER_KEY) || 'lena';
} catch {
  /* storage unavailable: keep the default */
}
export const getUser = () => currentUser;
export function setUser(name: string) {
  currentUser = name;
  try {
    localStorage.setItem(USER_KEY, name);
  } catch {
    /* ignore */
  }
}
export const eventsUrl = (user: string) => `${API}/events/stream?user=${encodeURIComponent(user)}`;

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}
export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** `raw` sends `body` as-is with that content type (used for JUnit XML); otherwise the body is JSON. */
async function request<T>(method: string, path: string, body?: unknown, auth = true, raw?: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (auth) headers['X-User'] = currentUser;
  if (body !== undefined) headers['Content-Type'] = raw ?? 'application/json';
  let res: Response;
  try {
    res = await fetch(API + path, { method, headers, body: body === undefined ? undefined : raw ? String(body) : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, 'NETWORK', `Cannot reach the API at ${API_BASE}. Is the backend running?`);
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
    const detail = (data as { detail?: unknown } | null)?.detail;
    const message = err?.message || (detail ? `Request rejected: ${JSON.stringify(detail)}` : `${res.status} ${res.statusText}`);
    throw new ApiError(res.status, err?.code || 'HTTP_' + res.status, message, err?.details);
  }
  return data as T;
}
const get = <T,>(p: string) => request<T>('GET', p);
const post = <T,>(p: string, body: unknown = {}) => request<T>('POST', p, body);

// ---------------------------------------------------------------- types
export type GateStatus = 'PASS' | 'FAIL' | 'PENDING' | 'STALE';
export interface GateChip { gate: string; status: GateStatus }
export interface GateDetail extends GateChip {
  title: string; summary: string; actor: string; commit: string; checked_at: string | null; evidence_artifact_id: number | null;
}
export interface User { name: string; display: string; roles: string[]; human: boolean }
export interface Me { name: string; roles: string[]; human: boolean; authMode: string; permissions: string[] }
export interface Engine {
  engine: string; label: string; attended: boolean; api: boolean; mode: string; verified: boolean; enabled: boolean; note: string;
}
export interface JiraCaps { adapter: string; read: boolean; write: boolean; verified: boolean; note: string }
export interface Workspace { name: string; project: string; paused: boolean; pausedReason: string }

export interface StorySummary {
  key: string; summary: string; issueType: string; state: string; stage: string; jiraStatus: string; owner: string;
  priority: string; labels: string[]; components: string[]; readiness: string; warnings: string[]; blockers: string[];
  revision: number; specVersion: number; prototypeVersion: number; headCommit: string; prUrl: string; cost: number;
  inActiveSprint: boolean; leased: boolean; activeRuns: number; queuedTasks: number; awaitingManualLaunch: number;
  gates: GateChip[]; updatedAt: string | null;
}
export interface SprintView {
  workspace: Workspace;
  sprint: { id: string; name: string; state: string; start: string; end: string } | null;
  lastSyncAt: string | null; stale: boolean; jira: JiraCaps;
  stages: { name: string; states: string[] }[];
  queue: { activeSessions: number; maxActiveSessions: number };
  engines: Engine[]; stories: StorySummary[];
}

export interface Criterion { id: string; text: string }
export interface SpecItem { id: string; text?: string; description?: string; origin?: string; [k: string]: unknown }
export interface CodeImpact { path: string; change: string; risk: string; origin?: string }
export interface SpecScreen { name: string; purpose: string; criterion_ids?: string[]; origin?: string }
export interface SpecGeneration { generator?: string; fallback?: boolean; reason?: string }
export interface FailedRule { rule: string; message: string; ref?: string }
export interface SpecContent {
  criteria?: SpecItem[]; requirements?: SpecItem[]; nonfunctional?: SpecItem[]; business_rules?: SpecItem[];
  assumptions?: SpecItem[]; error_cases?: SpecItem[]; test_cases?: SpecItem[]; scenarios?: SpecItem[];
  change_log?: { version?: number; kind?: string; author?: string; text?: string; at?: string }[];
  open_questions?: SpecItem[]; screens?: SpecScreen[]; code_impact?: CodeImpact[]; generation?: SpecGeneration;
  injection_flags?: unknown[]; domains?: string[]; [k: string]: unknown;
}
export interface SpecVersion {
  version: number; sha256: string; criteriaSha256: string; status: string; createdBy: string; createdAt: string | null;
  checkResult: { passed?: boolean; failed_rules?: FailedRule[]; rules_evaluated?: string[] };
}
export interface Spec extends SpecVersion { content: SpecContent }
export interface SpecDiffChange {
  section: string; added?: unknown[]; removed?: unknown[]; changed?: { id: string; from: unknown; to: unknown }[];
  from?: unknown; to?: unknown;
}
export interface SpecDiff { from: number; to: number; changes: SpecDiffChange[] }

export interface TraceStep { step: number; op: string; ok: boolean; detail: string }
export interface JourneyResult { id: string; criterion_id: string; passed: boolean; trace: TraceStep[] }
export interface TestResult {
  passed?: boolean; failed_rules?: FailedRule[]; journeys?: JourneyResult[]; checker?: string;
  browser_check?: { ran: boolean; reason?: string; [k: string]: unknown };
}
export interface ProtoVersion {
  version: number; specVersion: number; sha256: string; generator: string; status: string; createdBy: string;
  createdAt: string | null; simulated: boolean; previewUrl: string; previewExpiresInSeconds: number;
}
export interface Prototype extends ProtoVersion {
  manifest: { journeys?: { id: string; title?: string; criterion_id?: string }[]; [k: string]: unknown };
  testResult: TestResult; testIds: string[];
}

export interface Approval {
  id: number; decision: string; actor: string; roles: string[]; comment: string; specVersion: number; prototypeVersion: number;
  specSha256: string; prototypeSha256: string; criteriaSha256: string; timestamp: string | null; inForce: boolean;
}
export interface Annotation {
  id: number; prototypeVersion: number; selector: string; comment: string; author: string; resolved: boolean; createdAt: string | null;
}
export interface Task {
  id: number; key: string; title: string; kind: string; deps: string[]; ownerPaths: string[]; engine: string; priority: number;
  status: string; blocker: string; detail: string; story?: string;
}
export interface Run {
  id: number; story: string; task: string; taskKind: string; engine: string; engineLabel: string; mode: string; state: string;
  externalId: string; externalUrl: string; branch: string; attempt: number; cost: number; traceId: string; prUrl: string;
  commit: string; lastEvent: string; failure: string; createdBy: string; createdAt: string | null; finishedAt: string | null;
  attended: boolean; allowedPaths: string[]; brief?: string;
}
export interface TraceRow {
  criterion_id: string; text: string; requirements: string[]; scenario: string | null; tests: string[]; tasks: string[];
  commit: string; proven: { prototype: boolean; approved: boolean; ci: boolean; staging: boolean; uat: boolean }; verified: boolean;
  ciTests?: { passed: number; failed: number; tests: string[] } | null;
}
export interface CheckRow { name: string; conclusion: string; url: string; commit: string }
export interface JiraSyncRow {
  operationKey: string; kind: string; result: string; remoteId: string; attempt: number; error: string; updatedAt: string | null;
}
export interface AuditEvent {
  id: number; subject: string; event: string; actor: string; oldState: string; newState: string;
  detail: Record<string, unknown>; timestamp: string | null; correlationId: string;
}
export interface AllowedActions {
  sync: boolean; analyze: boolean; revise: boolean; annotate: boolean; approve: boolean; dispatch: boolean; ingestCommit: boolean;
  recordGate: boolean; uat: boolean; syncEvidence: boolean; transition: boolean; pause: boolean; resume: boolean; retry: boolean; cancel: boolean;
  reportRun: boolean; cancelRun: boolean;
}
export interface StoryDetail extends StorySummary {
  source: {
    description: string; criteria: Criterion[]; links: { type?: string; key?: string; status?: string }[];
    attachments: unknown[]; jiraUpdated: string; sourceDigest: string; criteriaDigest: string; fetchedAt: string | null;
    readinessReasons: string[];
  };
  spec: Spec | null; specVersions: SpecVersion[];
  prototype: Prototype | null; prototypeVersions: ProtoVersion[];
  baseline: { valid: boolean; approvalId: number | null; approvedSpecVersion: number | null; approvedPrototypeVersion: number | null };
  approvals: Approval[]; annotations: Annotation[]; tasks: Task[]; runs: Run[]; gateDetails: GateDetail[];
  checks?: CheckRow[];
  traceability: TraceRow[]; jiraSync: JiraSyncRow[]; timeline: AuditEvent[]; allowedActions: AllowedActions;
  policy: { autoRefinementMaxAttempts: number; autoRepairMaxAttempts: number; maxPerStoryUsd: number; autoAttempts: number; repairAttempts: number };
}
export interface RunsView { runs: Run[]; queue: Task[]; activeSessions: number; maxActiveSessions: number; engines: Engine[] }
export interface ArtifactRow { id: number; kind: string; uri: string; sha256: string; retentionClass?: string; createdAt: string | null; content?: string }
export interface EvidenceView { gates: GateDetail[]; traceability: TraceRow[]; bundle: unknown; artifacts: ArtifactRow[] }
export interface AdminConfig {
  policy: Record<string, unknown>; engines: Engine[]; jira: JiraCaps; workspace: Workspace; authMode: string; devMode: boolean;
  database: string; roles: Record<string, string[]>; canAdmin: boolean; mockJira: boolean;
  generation?: { provider: string; active: string; model?: string; note?: string };
  autopilot?: Autopilot; ci?: { checks?: Record<string, string[]> }; team?: { four_eyes?: boolean };
  integrations?: { githubWebhookSigned: boolean; automationToken: boolean };
}
export interface Autopilot { enabled: boolean; auto_analyze: boolean; auto_dispatch: boolean; auto_publish_evidence: boolean; max_wip: number }
export interface InboxItem {
  kind: string; story: string | null; summary: string; title: string; detail: string; since: string; ageMinutes: number;
  canAct: boolean; reason: string; runId: number | null; noteId?: number;
}
export interface InboxView { items: InboxItem[]; actionable: number; autopilot: Autopilot }
export interface Playbook { id: string; title: string; body: string; sha256: string; devinPlaybookId: string }
export interface KnowledgeNote {
  id: number; title: string; body: string; tags: string[]; source: string; status: string; storyKey: string | null;
  createdBy: string; createdAt: string | null;
}
export interface SpecBrief { engine: string; engineLabel: string; playbook: string; brief: string; devinPlaybookId: string }
export interface CriterionTests { passed: number; failed: number; skipped: number; tests: string[] }
export interface JunitResult { state?: string; result: string; summary: string; criteria: Record<string, CriterionTests> }
export interface TestPlan {
  story: string; specVersion: number; specSha256: string; rule: string; e2eSkeleton: string;
  criteria: { id: string; text: string; testNameMustContain: string; errorCases: string[] }[];
}
export interface Kpis { not_measured?: string[]; [k: string]: unknown }
export interface MockJira {
  sprint: { id?: string; name?: string; state?: string }; failWrites: number; failAfterWrite: number;
  issues: { key: string; status: string; labels: string[]; criteria: string[] }[];
  comments: Record<string, { id: string; body: string }[]>;
}

// ---------------------------------------------------------------- endpoints
const s = (key: string) => `/stories/${encodeURIComponent(key)}`;
export const api = {
  users: () => request<User[]>('GET', '/users', undefined, false),
  me: () => get<Me>('/me'),
  sprint: () => get<SprintView>('/sprints/active'),
  syncSprint: () => post<{ synced: number; at: string }>('/sprints/sync'),
  bulkAnalyze: () => post<{ started: { key: string }[]; skipped: { key: string; reason: string }[] }>('/stories/bulk-analyze'),
  story: (key: string) => get<StoryDetail>(s(key)),
  syncStory: (key: string) => post<{ state: string }>(`${s(key)}/sync`),
  analyze: (key: string) => post<{ runId: string; state: string }>(`${s(key)}/analyze`),
  specDiff: (key: string, a: number, b: number) => get<SpecDiff>(`${s(key)}/specs/${a}/diff/${b}`),
  prototype: (key: string, v: number) => get<Prototype>(`${s(key)}/prototype/${v}`),
  revise: (key: string, body: { feedback?: string; prototypeHtml?: string; origin?: string; enrichment?: unknown }) =>
    post<{ state: string }>(`${s(key)}/prototype/revise`, body),
  specBrief: (key: string, engine: string) => get<SpecBrief>(`${s(key)}/spec-brief?engine=${encodeURIComponent(engine)}`),
  prototypeBrief: (key: string, engine: string) =>
    get<{ engine: string; engineLabel: string; brief: string; file: string }>(`${s(key)}/prototype-brief?engine=${encodeURIComponent(engine)}`),
  testPlan: (key: string) => get<TestPlan>(`${s(key)}/test-plan`),
  /** Posts the raw JUnit XML; the server maps tests to criteria and records the gate for that commit. */
  junit: (key: string, commit: string, gate: string, xml: string) =>
    request<JunitResult>('POST', `${s(key)}/ci/junit?commit=${encodeURIComponent(commit)}&gate=${encodeURIComponent(gate)}`, xml, true, 'application/xml'),
  annotate: (key: string, body: { selector: string; comment: string; prototypeVersion?: number }) => post<{ id: number }>(`${s(key)}/annotations`, body),
  resolveAnnotation: (key: string, id: number) => post<{ id: number }>(`${s(key)}/annotations/${id}/resolve`),
  approve: (key: string, body: Record<string, unknown>) => post<{ state: string }>(`${s(key)}/approve`, body),
  dispatch: (key: string, engines: Record<string, string>) =>
    post<{ state: string; started: number[]; queued: string[] }>(`${s(key)}/dispatch`, { engines }),
  ingestCommit: (key: string, body: { commit: string; prUrl: string; source: string }) =>
    post<{ state: string; changed: boolean }>(`${s(key)}/commits`, body),
  recordGate: (key: string, body: { gate: string; result: string; commit: string; summary: string; evidence: unknown }) =>
    post<{ state: string }>(`${s(key)}/gates`, body),
  uat: (key: string, body: { decision: string; commit: string; feedback: string; materialDesignChange: boolean }) =>
    post<{ state: string }>(`${s(key)}/uat`, body),
  syncEvidence: (key: string) => post<{ state: string; remoteId: string; attempts: number }>(`${s(key)}/evidence/sync`),
  evidence: (key: string) => get<EvidenceView>(`${s(key)}/evidence`),
  artifact: (id: number) => get<ArtifactRow>(`/artifacts/${id}`),
  transition: (key: string) => post<{ state: string; jiraStatus: string }>(`${s(key)}/transition`),
  pause: (key: string, reason: string) => post<{ state: string }>(`${s(key)}/pause`, reason ? { reason } : {}),
  resume: (key: string) => post<{ state: string }>(`${s(key)}/resume`),
  retry: (key: string) => post<{ state: string }>(`${s(key)}/retry`),
  cancel: (key: string, reason: string) => post<{ state: string }>(`${s(key)}/cancel`, reason ? { reason } : {}),
  runs: () => get<RunsView>('/runs'),
  attachRun: (id: number, url: string) => post<Run>(`/runs/${id}/attach`, { url }),
  reportRun: (id: number, body: { state: string; commit: string; prUrl: string; cost: number; failure: string }) =>
    post<Run>(`/runs/${id}/report`, body),
  cancelRun: (id: number, reason: string) => post<Run>(`/runs/${id}/cancel`, { reason }),
  inbox: () => get<InboxView>('/inbox'),
  playbooks: () => get<{ playbooks: Playbook[]; note: string }>('/playbooks'),
  knowledge: () => get<{ notes: KnowledgeNote[] }>('/knowledge'),
  knowledgeExport: () => get<{ markdown: string; notes: number }>('/knowledge/export'),
  addNote: (body: { title: string; body: string; tags: string[] }) => post<KnowledgeNote>('/knowledge', body),
  noteStatus: (id: number, body: { status: 'active' | 'archived'; body?: string }) => post<KnowledgeNote>(`/knowledge/${id}/status`, body),
  adminConfig: () => get<AdminConfig>('/admin/config'),
  setEngines: (body: Record<string, boolean>) => request<{ engines: Engine[] }>('PUT', '/admin/engines', body),
  pauseWorkspace: (paused: boolean, reason: string) => post<{ paused: boolean }>('/admin/pause', { paused, reason }),
  reconcile: () => post<Record<string, number>>('/admin/reconcile'),
  kpis: () => get<Kpis>('/metrics/kpis'),
  auditVerify: () => get<{ ok: boolean; events: number; broken_at: number | null }>('/audit/verify'),
  mockJira: () => get<MockJira>('/dev/mock-jira'),
  mockSprint: (state: 'closed' | 'active') => post<unknown>('/dev/mock-jira/sprint', { state }),
  mockFaults: (failWrites: number, failAfterWrite: number) => post<unknown>('/dev/mock-jira/faults', { failWrites, failAfterWrite }),
  mockIssue: (key: string, body: Record<string, unknown>) => post<unknown>(`/dev/mock-jira/issues/${encodeURIComponent(key)}`, body),
};
