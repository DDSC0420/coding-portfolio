// Typed calls to the control API, and a small polling hook. Everything goes through /api.

import { useEffect, useRef, useState } from 'react';

export type WorkflowStatus = 'running' | 'awaiting_approval' | 'succeeded' | 'failed' | 'outcome_unknown' | 'rejected';

export interface Metrics {
  workflows: Record<WorkflowStatus, number>;
  total: number;
  completed_last_minute: number;
  steps_ready: number;
  steps_waiting_retry: number;
  steps_running: number;
  oldest_ready_age_seconds: number;
  attempts_total: number;
  retries_total: number;
  leases_expired_total: number;
  stale_writes_rejected_total: number;
  p50_seconds: number | null;
  p95_seconds: number | null;
}
export interface Lease {
  step_run_id: string;
  workflow_run_id: string;
  label: string | null;
  step: string;
  lease_owner: string;
  lease_generation: number;
  seconds_left: number;
  attempt_count: number;
  max_attempts: number;
}
export interface Approval {
  id: number;
  workflow_run_id: string;
  status: 'pending' | 'approved' | 'rejected';
  reason: string;
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  comment: string | null;
  label: string | null;
  company: string | null;
  project_value_gbp: number | null;
}
export interface Overview {
  server_time: string;
  lease_seconds: number;
  metrics: Metrics;
  leases: Lease[];
  pending_approvals: Approval[];
}
export interface Worker {
  id: string;
  name: string;
  pid: number;
  started_at: string;
  heartbeat_age_seconds: number;
  status: 'online' | 'offline' | 'stopped';
}
export interface EventItem {
  id: number;
  type: string;
  workflow_run_id: string | null;
  label: string | null;
  step: string | null;
  created_at: string;
  details: Record<string, unknown>;
}
export interface WorkflowSummary {
  id: string;
  workflow_type: string;
  workflow_version: number;
  status: WorkflowStatus;
  batch_id: string | null;
  label: string | null;
  current_step: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}
export interface Attempt {
  attempt_number: number;
  worker_id: string;
  lease_generation: number;
  outcome: string;
  error_class: string | null;
  error_summary: string | null;
  started_at: string;
  finished_at: string | null;
}
export interface Step {
  id: string;
  name: string;
  position: number;
  status: string;
  attempt_count: number;
  max_attempts: number;
  next_eligible_at: string | null;
  lease_owner: string | null;
  lease_generation: number;
  idempotency_key: string | null;
  error_class: string | null;
  error_summary: string | null;
  attempts: Attempt[];
}
export interface WorkflowDetail extends WorkflowSummary {
  approval: Approval | null;
  input: Record<string, unknown>;
  result: Record<string, unknown> | null;
  failure: Record<string, unknown> | null;
  steps: Step[];
}
export interface CrmState {
  mutations: number;
  keys_with_duplicates: unknown[];
  faults: { demo_mode: boolean; settings?: Record<string, number | null>; injected?: Record<string, number> };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const post = <T,>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body: JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body: JSON.stringify(body) });

/** Fetch `path` now and every `ms`. Keeps the last good data while a refresh fails. */
export function usePoll<T>(path: string | null, ms = 1000): { data: T | null; error: string | null; refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const next = await api<T>(path);
        if (alive) {
          setData(next);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
      if (alive) timer = setTimeout(load, ms);
    };
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [path, ms, tick]);
  return { data, error, refresh: () => setTick((t) => t + 1) };
}

/** The live event feed: the newest page first, then only what is new since the last id seen. */
export function useEvents(max = 80): EventItem[] {
  const [events, setEvents] = useState<EventItem[]>([]);
  const cursor = useRef<number | null>(null);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const q = cursor.current === null ? `?limit=${max}` : `?after=${cursor.current}&limit=200`;
        const page = await api<{ items: EventItem[] }>(`/events${q}`);
        if (alive && page.items.length) {
          cursor.current = page.items[page.items.length - 1]!.id;
          setEvents((old) => [...page.items.reverse(), ...old].slice(0, max));
        } else if (alive && cursor.current === null) cursor.current = 0;
      } catch {
        /* the status line already reports a lost connection */
      }
      if (alive) timer = setTimeout(load, 1000);
    };
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [max]);
  return events;
}

export const STATUS_LABEL: Record<string, string> = {
  running: 'Running',
  awaiting_approval: 'Waiting for approval',
  succeeded: 'Succeeded',
  failed: 'Failed',
  outcome_unknown: 'Outcome unknown',
  rejected: 'Rejected',
  pending: 'Not started',
  ready: 'Queued',
  cancelled: 'Cancelled',
};

export function ago(iso: string, now: number): string {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export const pounds = (n: number | null) => (n === null ? '' : `£${n.toLocaleString('en-GB')}`);
export const shortWorker = (id: string) => id.split('/')[0] ?? id;
