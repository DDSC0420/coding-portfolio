import { useState } from 'react';
import {
  ago,
  post,
  pounds,
  put,
  shortWorker,
  STATUS_LABEL,
  useEvents,
  usePoll,
  type Approval,
  type CrmState,
  type EventItem,
  type Lease,
  type Overview as OverviewData,
  type Worker,
  type WorkflowStatus,
} from './api';

const ORDER: WorkflowStatus[] = ['running', 'awaiting_approval', 'succeeded', 'failed', 'outcome_unknown', 'rejected'];

/** One lane per worker; each lease is a bar that drains as its time runs out. */
function Lanes({ workers, leases, leaseSeconds }: { workers: Worker[]; leases: Lease[]; leaseSeconds: number }) {
  const byOwner = new Map<string, Lease[]>();
  for (const l of leases) byOwner.set(l.lease_owner, [...(byOwner.get(l.lease_owner) ?? []), l]);
  // Leases whose owner is not a current worker instance (it was killed and replaced, or never came back).
  const known = new Set(workers.map((w) => w.id));
  const orphans = leases.filter((l) => !known.has(l.lease_owner));
  if (workers.length === 0) {
    return <p className="empty">No workers have registered yet. Start one with <code>docker compose up -d</code>.</p>;
  }
  return (
    <div className="lanes">
      {workers.map((w) => {
        const held = byOwner.get(w.id) ?? [];
        return (
          <section key={w.id} className={`lane ${w.status}`} aria-label={`${w.name}, ${w.status}`}>
            <header>
              <h3>{w.name}</h3>
              <p>
                <span className={`dot ${w.status}`} aria-hidden="true" />
                {w.status === 'online'
                  ? `online, heartbeat ${w.heartbeat_age_seconds.toFixed(1)}s ago`
                  : w.status === 'stopped'
                    ? 'stopped cleanly'
                    : `no heartbeat for ${Math.round(w.heartbeat_age_seconds)}s`}
              </p>
            </header>
            <div className="track">
              {held.length === 0 && (
                <p className="idle">{w.status === 'online' ? 'Idle: holding no leases.' : 'Holding no leases.'}</p>
              )}
              {held.map((l) => (
                <LeaseBar key={l.step_run_id} lease={l} leaseSeconds={leaseSeconds} dead={w.status !== 'online'} />
              ))}
            </div>
          </section>
        );
      })}
      {orphans.length > 0 && (
        <section className="lane offline" aria-label="Leases held by workers that are gone">
          <header>
            <h3>Gone</h3>
            <p>Held by an instance that no longer exists. Each is recovered when its lease runs out.</p>
          </header>
          <div className="track">
            {orphans.map((l) => (
              <LeaseBar key={l.step_run_id} lease={l} leaseSeconds={leaseSeconds} dead />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function LeaseBar({ lease, leaseSeconds, dead }: { lease: Lease; leaseSeconds: number; dead: boolean }) {
  const left = Math.max(0, lease.seconds_left);
  const share = Math.min(1, left / leaseSeconds);
  return (
    <a
      className={`lease ${dead ? 'dead' : ''} ${left === 0 ? 'expired' : ''}`}
      href={`#/workflows/${lease.workflow_run_id}`}
      title={`Lease generation ${lease.lease_generation}, attempt ${lease.attempt_count} of ${lease.max_attempts}`}
    >
      <span className="fill" style={{ width: `${share * 100}%` }} />
      <span className="what">
        <b>{lease.step}</b> {lease.label ?? lease.workflow_run_id.slice(0, 8)}
      </span>
      <span className="when">
        {left === 0 ? 'expired, awaiting recovery' : `${left.toFixed(0)}s left`}
        <i>gen {lease.lease_generation}</i>
      </span>
    </a>
  );
}

function Ledger({ data }: { data: OverviewData }) {
  const m = data.metrics;
  return (
    <>
      <dl className="ledger">
        {ORDER.map((s) => (
          <a key={s} href={`#/workflows?status=${s}`} className={`cell ${s} ${m.workflows[s] ? '' : 'zero'}`}>
            <dd>{m.workflows[s]}</dd>
            <dt>{STATUS_LABEL[s]}</dt>
          </a>
        ))}
      </dl>
      <p className="facts">
        {m.completed_last_minute} finished in the last minute. {m.steps_ready} steps queued
        {m.steps_waiting_retry ? `, ${m.steps_waiting_retry} waiting to retry` : ''}. {m.retries_total} retries,{' '}
        {m.leases_expired_total} leases expired and recovered, {m.stale_writes_rejected_total} stale writes refused.
        {m.p50_seconds !== null && ` Typical run takes ${m.p50_seconds.toFixed(1)}s (95% within ${(m.p95_seconds ?? 0).toFixed(1)}s).`}
      </p>
    </>
  );
}

function Approvals({ items, refresh, now }: { items: Approval[]; refresh: () => void; now: number }) {
  const [reviewer, setReviewer] = useState(() => localStorage.getItem('relay:reviewer') ?? '');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const decide = async (a: Approval, action: 'approve' | 'reject') => {
    setBusy(a.workflow_run_id);
    setError(null);
    try {
      localStorage.setItem('relay:reviewer', reviewer);
      await post(`/workflows/${a.workflow_run_id}/${action}`, { reviewer: reviewer.trim() });
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="panel" aria-labelledby="h-approvals">
      <h2 id="h-approvals">Waiting for a decision</h2>
      {items.length === 0 ? (
        <p className="empty">Nothing is waiting. Enquiries worth £50,000 or more, or flagged for review, stop here before the CRM is touched.</p>
      ) : (
        <>
          <label className="reviewer">
            Deciding as
            <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="your name" maxLength={100} />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <ul className="approvals">
            {items.map((a) => (
              <li key={a.id}>
                <a href={`#/workflows/${a.workflow_run_id}`}>
                  <b>{a.company ?? a.label}</b> {a.project_value_gbp !== null && <span className="value">{pounds(a.project_value_gbp)}</span>}
                </a>
                <p>
                  {a.reason}. Waiting {ago(a.requested_at, now).replace(' ago', '')}, holding no lease.
                </p>
                <div className="actions">
                  <button className="primary" disabled={!reviewer.trim() || busy === a.workflow_run_id} onClick={() => decide(a, 'approve')}>
                    Approve
                  </button>
                  <button disabled={!reviewer.trim() || busy === a.workflow_run_id} onClick={() => decide(a, 'reject')}>
                    Reject
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

const TONE: Record<string, string> = {
  'step.retry_scheduled': 'warn',
  'step.lease_expired': 'warn',
  'step.stale_write_rejected': 'bad',
  'step.failed': 'bad',
  'workflow.failed': 'bad',
  'workflow.outcome_unknown': 'bad',
  'workflow.rejected': 'bad',
  'approval.rejected': 'bad',
  'workflow.succeeded': 'good',
  'approval.requested': 'hold',
  'approval.approved': 'good',
};

export function describe(e: EventItem): string {
  const d = e.details as Record<string, string | number | undefined>;
  const who = typeof d.worker_id === 'string' ? shortWorker(d.worker_id) : '';
  switch (e.type) {
    case 'workflow.created':
      return 'submitted';
    case 'step.claimed':
      return `${e.step} claimed by ${who} (attempt ${d.attempt}, gen ${d.lease_generation})`;
    case 'step.succeeded':
      return `${e.step} done by ${who}`;
    case 'step.ready':
      return `${e.step} queued`;
    case 'step.retry_scheduled':
      return `${e.step} failed on ${who}: ${d.summary}. Retrying in ${d.delay_seconds}s`;
    case 'step.failed':
      return `${e.step} failed for good: ${d.summary}`;
    case 'step.lease_expired':
      return `${e.step}: ${shortWorker(String(d.previous_owner ?? ''))} stopped renewing its lease (gen ${d.lease_generation}); ${shortWorker(String(d.recovered_by ?? ''))} took the step back${d.new_status === 'ready' ? ' for another attempt' : ` and ended it as ${String(d.new_status).replace(/_/g, ' ')}`}`;
    case 'step.stale_write_rejected':
      return `${e.step}: refused a late ${d.action} from ${who} (gen ${d.lease_generation}, now gen ${d.current_generation})`;
    case 'approval.requested':
      return `paused for approval: ${d.reason}`;
    case 'approval.approved':
      return `approved by ${d.decided_by}`;
    case 'approval.rejected':
      return `rejected by ${d.decided_by}`;
    default:
      return e.type.replace('workflow.', 'workflow ').replace('step.', `${e.step ?? 'step'} `).replace(/_/g, ' ');
  }
}

function Feed({ events }: { events: EventItem[] }) {
  return (
    <section className="panel" aria-labelledby="h-feed">
      <h2 id="h-feed">What just happened</h2>
      {events.length === 0 ? (
        <p className="empty">No events yet. Send a batch to see steps being claimed, completed and handed on.</p>
      ) : (
        <ol className="feed">
          {events.map((e) => (
            <li key={e.id} className={TONE[e.type] ?? ''}>
              <time dateTime={e.created_at}>{new Date(e.created_at).toLocaleTimeString('en-GB')}</time>
              <a href={`#/workflows/${e.workflow_run_id}`}>{e.label ?? '—'}</a>
              <span>{describe(e)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

const PRESETS: Array<{ name: string; hint: string; body: Record<string, number> | null }> = [
  { name: 'Healthy', hint: 'Every call works.', body: null },
  { name: 'Refuse 40%', hint: '503 before saving: safe to retry.', body: { fail_before_commit_rate: 0.4 } },
  { name: 'Lose 40% of replies', hint: 'Saves, then drops the connection: did it happen?', body: { drop_after_commit_rate: 0.4 } },
  { name: 'Slow', hint: '800 ms per call.', body: { delay_ms: 800 } },
];

function Demo({ refresh }: { refresh: () => void }) {
  const crm = usePoll<CrmState>('/demo/crm', 2000);
  const [note, setNote] = useState<string | null>(null);
  const send = async (count: number, invalid: number, needs_approval: number) => {
    try {
      const r = await post<{ batch_id: string; submitted: number }>('/batches', { count, invalid, needs_approval });
      setNote(`Sent ${r.submitted} enquiries as ${r.batch_id}.`);
      refresh();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    }
  };
  const fault = async (p: (typeof PRESETS)[number]) => {
    try {
      if (p.body) await put('/demo/crm/faults', p.body);
      else await post('/demo/crm/faults/reset');
      setNote(`CRM set to: ${p.name.toLowerCase()}.`);
      crm.refresh();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    }
  };
  const faultsOn = crm.data?.faults.demo_mode === true;
  // Which preset the CRM is actually running, read back from the CRM itself.
  const current = crm.data?.faults.settings ?? {};
  const active =
    PRESETS.find((p) => p.body && Object.entries(p.body).every(([k, v]) => current[k] === v))?.name ??
    (Object.entries(current).some(([k, v]) => /_rate$|^delay_ms$/.test(k) && v) ? 'Custom' : 'Healthy');
  const dupes = crm.data?.keys_with_duplicates.length ?? 0;
  return (
    <section className="panel demo" aria-labelledby="h-demo">
      <h2 id="h-demo">Try to break it</h2>
      <div className="row">
        <button className="primary" onClick={() => send(40, 3, 4)}>
          Send 43 enquiries
        </button>
        <button onClick={() => send(5, 0, 5)}>Send 5 that need approval</button>
      </div>
      <p className="hint">Then kill a worker mid-batch from a terminal and watch its leases drain and move:</p>
      <pre>docker compose kill worker-a{'\n'}docker compose start worker-a</pre>
      <h3>The CRM the workflows call</h3>
      {faultsOn ? (
        <div className="row presets" role="group" aria-label="CRM behaviour">
          {PRESETS.map((p) => (
            <button key={p.name} aria-pressed={active === p.name} title={p.hint} onClick={() => fault(p)}>
              {p.name}
            </button>
          ))}
        </div>
      ) : (
        <p className="hint">Fault switches are off. Start with <code>-f compose.demo.yaml</code> to turn them on.</p>
      )}
      <p className={`crm ${dupes ? 'bad' : ''}`}>
        {crm.data
          ? `${crm.data.mutations} contact writes recorded by the CRM, ${dupes} applied twice.`
          : crm.error
            ? `CRM: ${crm.error}`
            : 'Asking the CRM…'}
      </p>
      {note && <p className="note" role="status">{note}</p>}
    </section>
  );
}

export function Overview({ overview, workers, refresh }: { overview: OverviewData | null; workers: Worker[] | null; refresh: () => void }) {
  const events = useEvents();
  if (!overview || !workers) return <p className="empty">Loading the live view…</p>;
  const now = new Date(overview.server_time).getTime();
  const online = workers.filter((w) => w.status === 'online').length;
  return (
    <>
      <section className="hero" aria-labelledby="h-lanes">
        <h1 id="h-lanes">
          {online} of {workers.length} workers online, holding {overview.leases.length} {overview.leases.length === 1 ? 'lease' : 'leases'}
        </h1>
        <p className="lede">
          A worker owns a step only while its lease has time left; it renews every {(overview.lease_seconds / 3).toFixed(0)}s. Stop renewing
          and the bar drains: at zero, another worker takes the step.
        </p>
        <Lanes workers={workers} leases={overview.leases} leaseSeconds={overview.lease_seconds} />
      </section>
      <Ledger data={overview} />
      <div className="columns">
        <div>
          <Approvals items={overview.pending_approvals} refresh={refresh} now={now} />
          <Demo refresh={refresh} />
        </div>
        <Feed events={events} />
      </div>
    </>
  );
}
