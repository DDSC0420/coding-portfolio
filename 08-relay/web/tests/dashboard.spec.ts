// The dashboard against the real engine: every number asserted here is read back from the API
// or follows from what the test submitted.

import { execSync } from 'node:child_process';
import { expect, test, type Page } from '@playwright/test';

const API = 'http://127.0.0.1:8000';

async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await fetch(API + path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path}: ${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

type Metrics = { workflows: Record<string, number>; leases_expired_total: number };
const cell = (page: Page, label: string) => page.locator('.ledger .cell', { hasText: label }).locator('dd');

let errors: string[] = [];
test.beforeEach(({ page }) => {
  errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
});
test.afterEach(async () => {
  await api('/demo/crm/faults/reset', 'POST');
  expect(errors, 'no errors in the console').toEqual([]);
});

test.describe.configure({ mode: 'serial' });

test('an empty system says so and shows both workers idle', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('2 of 2 workers online, holding 0 leases');
  await expect(page.getByRole('status').first()).toHaveText('Connected. Nothing is waiting on you.');
  await expect(page.locator('.lane')).toHaveCount(2);
  await expect(page.getByText('Idle: holding no leases.')).toHaveCount(2);
  await expect(page.getByText('Nothing is waiting. Enquiries worth £50,000 or more')).toBeVisible();
  for (const label of ['Running', 'Succeeded', 'Failed']) await expect(cell(page, label)).toHaveText('0');
});

test('a batch sent from the page runs, and the counts match the engine', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Send 43 enquiries' }).click();
  await expect(page.getByText(/^Sent 43 enquiries as batch-/)).toBeVisible();
  // 40 valid of which 4 pause, 3 invalid: 36 succeed, 4 wait, 3 fail.
  await expect(cell(page, 'Succeeded')).toHaveText('36', { timeout: 30_000 });
  await expect(cell(page, 'Waiting for approval')).toHaveText('4');
  await expect(cell(page, 'Failed')).toHaveText('3');
  await expect(cell(page, 'Running')).toHaveText('0');
  const metrics = await api<Metrics>('/metrics');
  expect(metrics.workflows).toMatchObject({ succeeded: 36, awaiting_approval: 4, failed: 3, running: 0 });
  expect((metrics as unknown as { stale_writes_rejected_total: number }).stale_writes_rejected_total).toBe(0);
  await expect(page.getByText('36 contact writes recorded by the CRM, 0 applied twice.')).toBeVisible();
  await expect(page.getByRole('status').first()).toHaveText('Connected. 4 waiting for your decision.');
  await expect(page.locator('.feed li').first()).toBeVisible();
});

test('approve one and reject one from the page', async ({ page }) => {
  await page.goto('/');
  const items = page.locator('.approvals li');
  await expect(items).toHaveCount(4);
  await expect(items.first().getByRole('button', { name: 'Approve' })).toBeDisabled(); // no name yet
  await page.getByLabel('Deciding as').fill('kyran');

  const first = (await items.first().locator('a b').textContent())!;
  expect(first.length).toBeGreaterThan(2);
  await items.first().getByRole('button', { name: 'Approve' }).click();
  await expect(items).toHaveCount(3);
  await expect(cell(page, 'Succeeded')).toHaveText('37', { timeout: 20_000 });
  await expect(page.getByText('37 contact writes recorded by the CRM, 0 applied twice.')).toBeVisible();

  await items.first().getByRole('button', { name: 'Reject' }).click();
  await expect(items).toHaveCount(2);
  await expect(cell(page, 'Rejected')).toHaveText('1');
  await expect(page.locator('.feed li', { hasText: 'approved by kyran' }).first()).toBeVisible();
  await expect(page.locator('.feed li', { hasText: 'rejected by kyran' }).first()).toBeVisible();

  // The approved run's page tells the whole story; search finds it by company name.
  await page.locator('.feed li', { hasText: 'approved by kyran' }).first().getByRole('link').click();
  await expect(page.locator('.steps > li')).toHaveCount(5);
  await expect(page.locator('.steps > li.succeeded')).toHaveCount(5);
  await expect(page.locator('.callout')).toContainText('Approved by kyran');
  await expect(page.locator('.detail .feed li', { hasText: 'paused for approval' })).toBeVisible();
  await page.getByRole('link', { name: 'Workflows' }).first().click();
  await page.getByRole('searchbox').fill(first.split(' ')[0]!);
  await expect(page.locator('.runs tbody tr').first()).toBeVisible();
  await page.getByRole('searchbox').fill('no such company anywhere');
  await expect(page.getByText('No workflows match.')).toBeVisible();
});

test('the workflow list filters and a failed run explains itself', async ({ page }) => {
  await page.goto('/#/workflows');
  await expect(page.locator('.pager')).toContainText('1 to 25 of 43');
  await page.getByRole('button', { name: 'Failed' }).click();
  await expect(page.locator('.runs tbody tr')).toHaveCount(3);
  await page.locator('.runs tbody a').first().click();
  await expect(page.locator('.callout.bad')).toContainText('validation failed');
  await expect(page.locator('.steps > li.failed h3')).toHaveText('validate');
  await expect(page.locator('.steps > li.cancelled')).toHaveCount(2 + 1); // enrich, approval, crm_upsert never ran
});

test('kill a worker mid-batch: its leases drain, the other worker finishes, nothing is written twice', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Slow' }).click();
  await expect(page.getByRole('button', { name: 'Slow' })).toHaveAttribute('aria-pressed', 'true');
  await api('/demo/crm/faults', 'PUT', { delay_ms: 1500, drop_after_commit_rate: 0.3 });
  const before = await api<Metrics>('/metrics');
  await api('/batches', 'POST', { count: 24 });

  // Wait until worker-a is really holding leases, then kill it without warning.
  const laneA = page.locator('.lane', { has: page.getByRole('heading', { name: 'worker-a' }) });
  await expect(laneA.locator('.lease').first()).toBeVisible({ timeout: 20_000 });
  const workers = await api<Array<{ name: string; pid: number }>>('/workers');
  execSync(`kill -9 ${workers.find((w) => w.name === 'worker-a')!.pid}`);

  await expect(laneA).toContainText(/no heartbeat for \d+s/, { timeout: 15_000 });
  await expect(page.getByRole('heading', { level: 1 })).toContainText('1 of 2 workers online');
  await expect(page.locator('.feed li', { hasText: 'stopped renewing its lease' }).first()).toBeVisible({ timeout: 20_000 });

  // worker-b alone finishes all 24, through lost replies and the dead worker's steps.
  await expect(cell(page, 'Succeeded')).toHaveText(String(before.workflows.succeeded! + 24), { timeout: 60_000 });
  const after = await api<Metrics & { workflows: Record<string, number> }>('/metrics');
  expect(after.workflows.running).toBe(0);
  expect(after.workflows.outcome_unknown).toBe(0);
  expect(after.leases_expired_total).toBeGreaterThan(before.leases_expired_total);
  const crm = await api<{ mutations: number; keys_with_duplicates: unknown[] }>('/demo/crm');
  expect(crm.keys_with_duplicates).toEqual([]);
  expect(crm.mutations).toBe(after.workflows.succeeded);
  await expect(page.getByText(`${crm.mutations} contact writes recorded by the CRM, 0 applied twice.`)).toBeVisible();
});
