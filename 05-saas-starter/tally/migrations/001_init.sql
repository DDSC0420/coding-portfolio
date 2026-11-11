-- Accounts, workspaces (the billing unit), the product (projects) and billing state.

CREATE TABLE users (
  id            INTEGER PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at    INTEGER NOT NULL
);

-- Only a SHA-256 of each token is stored.
CREATE TABLE sessions (
  token_hash   TEXT PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workspace_id INTEGER NOT NULL,
  created_at   INTEGER NOT NULL,
  expires_at   INTEGER NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);

CREATE TABLE workspaces (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  owner_id   INTEGER NOT NULL REFERENCES users(id),
  created_at INTEGER NOT NULL
);

CREATE TABLE memberships (
  workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role         TEXT NOT NULL CHECK (role IN ('owner', 'member')),
  PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE invites (
  token_hash   TEXT PRIMARY KEY,
  workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  email        TEXT NOT NULL,
  created_at   INTEGER NOT NULL,
  expires_at   INTEGER NOT NULL,
  accepted_at  INTEGER
);
CREATE INDEX invites_workspace ON invites(workspace_id);

CREATE TABLE password_resets (
  token_hash TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL,
  used_at    INTEGER
);

CREATE TABLE projects (
  id           INTEGER PRIMARY KEY,
  workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  created_at   INTEGER NOT NULL
);
CREATE INDEX projects_workspace ON projects(workspace_id);

-- Local copy of the provider's subscription, kept in step by webhooks.
CREATE TABLE subscriptions (
  workspace_id         INTEGER PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  plan                 TEXT NOT NULL DEFAULT 'free',
  status               TEXT NOT NULL DEFAULT 'none',
  customer_id          TEXT,
  subscription_id      TEXT UNIQUE,
  current_period_end   INTEGER,
  cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
  last_event_at        INTEGER NOT NULL DEFAULT 0
);

-- Every webhook event processed, once: the primary key is what makes handling idempotent.
CREATE TABLE webhook_events (
  id           TEXT PRIMARY KEY,
  type         TEXT NOT NULL,
  workspace_id INTEGER,
  received_at  INTEGER NOT NULL,
  outcome      TEXT NOT NULL,
  payload      TEXT NOT NULL
);

-- Dev stand-in for email.
CREATE TABLE outbox (
  id         INTEGER PRIMARY KEY,
  to_email   TEXT NOT NULL,
  subject    TEXT NOT NULL,
  body       TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- The fake billing provider's own records. It plays a remote service, so the app never reads
-- these directly: it only learns about them through signed webhooks.
CREATE TABLE fake_checkouts (
  id           TEXT PRIMARY KEY,
  workspace_id INTEGER NOT NULL,
  email        TEXT NOT NULL,
  plan         TEXT NOT NULL,
  created_at   INTEGER NOT NULL,
  completed_at INTEGER
);

CREATE TABLE fake_subscriptions (
  id                   TEXT PRIMARY KEY,
  customer_id          TEXT NOT NULL,
  workspace_id         INTEGER NOT NULL,
  plan                 TEXT NOT NULL,
  status               TEXT NOT NULL,
  current_period_end   INTEGER NOT NULL,
  cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
  failed_attempts      INTEGER NOT NULL DEFAULT 0,
  card                 TEXT NOT NULL  -- 'ok' or 'declines_on_renewal'; never a card number
);

CREATE TABLE fake_sent_events (
  id         TEXT PRIMARY KEY,
  payload    TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
