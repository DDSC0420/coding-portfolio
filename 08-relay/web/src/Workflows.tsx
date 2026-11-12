import { useEffect, useState } from 'react';
import { ago, api, shortWorker, STATUS_LABEL, usePoll, type EventItem, type WorkflowDetail, type WorkflowSummary } from './api';
import { describe } from './Overview';

const FILTERS = ['', 'running', 'awaiting_approval', 'succeeded', 'failed', 'outcome_unknown', 'rejected'];
const PAGE = 25;

export function WorkflowList({ query }: { query: string }) {
  const params = new URLSearchParams(query);
  const status = params.get('status') ?? '';
  const [q, setQ] = useState(params.get('q') ?? '');
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [status, q]);
  const search = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE) });
  if (status) search.set('status', status);
  if (q.trim()) search.set('q', q.trim());
  const { data, error } = usePoll<{ items: WorkflowSummary[]; total: number }>(`/workflows?${search}`, 2000);
  const now = Date.now();
  const go = (s: string) => {
    location.hash = s ? `#/workflows?status=${s}` : '#/workflows';
  };
  return (
    <section aria-labelledby="h-list">
      <h1 id="h-list">Workflows</h1>
      <div className="filters">
        <div role="group" aria-label="Filter by status" className="chips">
          {FILTERS.map((s) => (
            <button key={s} aria-pressed={status === s} onClick={() => go(s)}>
              {s ? STATUS_LABEL[s] : 'All'}
            </button>
          ))}
        </div>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search reference, name, company or message" aria-label="Search workflows" />
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {data && data.items.length === 0 ? (
        <p className="empty">{status || q ? 'No workflows match.' : 'No workflows yet. Send a batch from the Live page.'}</p>
      ) : (
        <table className="runs">
          <thead>
            <tr>
              <th>Reference</th>
              <th>Status</th>
              <th>At step</th>
              <th>Version</th>
              <th>Submitted</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((w) => (
              <tr key={w.id}>
                <td>
                  <a href={`#/workflows/${w.id}`}>{w.label ?? w.id.slice(0, 8)}</a>
                </td>
                <td>
                  <span className={`status ${w.status}`}>{STATUS_LABEL[w.status]}</span>
                </td>
                <td>{w.current_step ?? ''}</td>
                <td>v{w.workflow_version}</td>
                <td>{ago(w.created_at, now)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data && data.total > PAGE && (
        <p className="pager">
          <button disabled={page === 0} onClick={() => setPage(page - 1)}>
            Newer
          </button>
          {page * PAGE + 1} to {Math.min(data.total, (page + 1) * PAGE)} of {data.total}
          <button disabled={(page + 1) * PAGE >= data.total} onClick={() => setPage(page + 1)}>
            Older
          </button>
        </p>
      )}
    </section>
  );
}

export function WorkflowDetailPage({ id }: { id: string }) {
  const { data: run, error } = usePoll<WorkflowDetail>(`/workflows/${id}`, 1000);
  const [events, setEvents] = useState<EventItem[]>([]);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api<{ items: EventItem[] }>(`/workflows/${id}/events?limit=500`)
        .then((p) => alive && setEvents(p.items))
        .catch(() => {});
    load();
    const t = setInterval(load, 1500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [id]);
  if (error && !run) return <p className="error" role="alert">{error}</p>;
  if (!run) return <p className="empty">Loading…</p>;
  const failure = run.failure as { summary?: string; step?: string; details?: { errors?: Array<{ field: string; message: string }> } } | null;
  return (
    <article className="detail">
      <p className="crumb">
        <a href="#/workflows">Workflows</a>
      </p>
      <h1>
        {run.label ?? run.id.slice(0, 8)} <span className={`status ${run.status}`}>{STATUS_LABEL[run.status]}</span>
      </h1>
      <p className="lede">
        enquiry v{run.workflow_version}, submitted {new Date(run.created_at).toLocaleString('en-GB')}
        {run.completed_at && `, finished ${new Date(run.completed_at).toLocaleTimeString('en-GB')}`}. Run {run.id}.
      </p>
      {run.status === 'outcome_unknown' && (
        <p className="callout bad">Retries ran out after an attempt whose result never came back. The CRM may or may not have this contact: check it before resubmitting.</p>
      )}
      {failure?.summary && run.status !== 'outcome_unknown' && (
        <p className="callout bad">
          {failure.summary}
          {failure.details?.errors && ': ' + failure.details.errors.map((e) => e.message).join('; ')}
        </p>
      )}
      {run.approval && (
        <p className={`callout ${run.approval.status === 'pending' ? 'hold' : ''}`}>
          {run.approval.status === 'pending'
            ? `Paused for approval: ${run.approval.reason}. Decide on the Live page.`
            : `${run.approval.status === 'approved' ? 'Approved' : 'Rejected'} by ${run.approval.decided_by}${run.approval.comment ? ` (“${run.approval.comment}”)` : ''}. Reason it paused: ${run.approval.reason}.`}
        </p>
      )}
      <h2>Steps</h2>
      <ol className="steps">
        {run.steps.map((s) => (
          <li key={s.id} className={s.status}>
            <header>
              <h3>{s.name}</h3>
              <span className={`status ${s.status}`}>{STATUS_LABEL[s.status] ?? s.status}</span>
              {s.status === 'running' && s.lease_owner && <span className="owner">leased to {shortWorker(s.lease_owner)}, gen {s.lease_generation}</span>}
              {s.idempotency_key && <span className="owner">CRM key {s.idempotency_key.slice(-22)}</span>}
            </header>
            {s.attempts.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>Attempt</th>
                    <th>Worker</th>
                    <th>Lease gen</th>
                    <th>Result</th>
                    <th>Took</th>
                  </tr>
                </thead>
                <tbody>
                  {s.attempts.map((a) => (
                    <tr key={a.attempt_number} className={a.outcome}>
                      <td>
                        {a.attempt_number} of {s.max_attempts}
                      </td>
                      <td>{shortWorker(a.worker_id)}</td>
                      <td>{a.lease_generation}</td>
                      <td>
                        {a.outcome.replace(/_/g, ' ')}
                        {a.error_summary && <small> {a.error_class}: {a.error_summary}</small>}
                      </td>
                      <td>{a.finished_at ? `${((new Date(a.finished_at).getTime() - new Date(a.started_at).getTime()) / 1000).toFixed(2)}s` : 'in progress'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </li>
        ))}
      </ol>
      <h2>History</h2>
      <ol className="feed">
        {events.map((e) => (
          <li key={e.id}>
            <time dateTime={e.created_at}>{new Date(e.created_at).toLocaleTimeString('en-GB')}</time>
            <span>{describe(e)}</span>
          </li>
        ))}
      </ol>
      <details>
        <summary>What was submitted, and what came out</summary>
        <pre>{JSON.stringify({ input: run.input, result: run.result }, null, 2)}</pre>
      </details>
    </article>
  );
}
