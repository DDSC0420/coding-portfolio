// Make docs/img/live.png and docs/img/workflow.png from the real stack. Dev only.
//
//   (terminal 1)  uv run python scripts/run_local.py --fresh
//   (terminal 2)  cd web && npm run build && npm run preview
//   (terminal 3)  cd web && node screenshot.mjs
//
// live.png: a batch against a slow CRM that loses replies, a few seconds after worker-a was killed.

import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';

const out = fileURLToPath(new URL('../docs/img/', import.meta.url));
const api = 'http://127.0.0.1:8000';
const call = (path, method = 'GET', body) =>
  fetch(api + path, { method, headers: body ? { 'content-type': 'application/json' } : {}, body: body && JSON.stringify(body) }).then((r) => r.json());
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {});
const page = await browser.newPage({ viewport: { width: 1360, height: 1040 }, deviceScaleFactor: 2 });
await page.goto('http://127.0.0.1:4173/');

// Some history first, so the counts are not all zero, and two approvals decided.
await call('/batches', 'POST', { count: 30, invalid: 3, needs_approval: 5 });
await sleep(4000);
const pending = await call('/approvals');
await call(`/workflows/${pending[0].workflow_run_id}/approve`, 'POST', { reviewer: 'kyran' });
await call(`/workflows/${pending[1].workflow_run_id}/reject`, 'POST', { reviewer: 'kyran', comment: 'out of scope' });
await sleep(1500);

await call('/demo/crm/faults', 'PUT', { delay_ms: 2500, drop_after_commit_rate: 0.4 });
await call('/batches', 'POST', { count: 40 });
const workers = await call('/workers');
const a = workers.find((w) => w.name === 'worker-a');
for (;;) {
  const { leases } = await call('/overview');
  if (leases.filter((l) => l.lease_owner === a.id).length >= 3) break;
  await sleep(100);
}
process.kill(a.pid, 'SIGKILL');
await page.getByText(/no heartbeat for/).waitFor({ timeout: 20000 });
await page.screenshot({ path: `${out}live.png` });

// A run that worker-a was holding when it died: two attempts, two workers, one CRM write.
await page.locator('.feed li', { hasText: 'stopped renewing its lease' }).first().waitFor({ timeout: 30000 });
await call('/demo/crm/faults/reset', 'POST');
const href = await page.locator('.feed li', { hasText: 'stopped renewing its lease' }).first().getByRole('link').getAttribute('href');
for (;;) {
  const run = await call(`/workflows/${href.split('/').pop()}`);
  if (run.status !== 'running') break;
  await sleep(300);
}
await page.goto(`http://127.0.0.1:4173/${href}`);
await page.locator('.steps > li').first().waitFor();
await sleep(1200);
await page.screenshot({ path: `${out}workflow.png`, fullPage: true });
await browser.close();
console.log('docs/img/live.png, docs/img/workflow.png');
