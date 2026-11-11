"""Every SQL query in the app, behind plain methods. One Store per request."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .security import new_token, token_digest
from .subscriptions import Subscription

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
SESSION_TTL = 14 * 86400
RESET_TTL = 30 * 60
INVITE_TTL = 7 * 86400


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply numbered migrations newer than PRAGMA user_version. Returns the new version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for file in sorted(MIGRATIONS.glob("*.sql")):
        number = int(file.name.split("_")[0])
        if number <= version:
            continue
        conn.executescript(f"BEGIN;\n{file.read_text()}\nPRAGMA user_version = {number};\nCOMMIT;")
        version = number
    return version


class Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _one(self, sql: str, *args) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def _all(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    # --- settings -------------------------------------------------------------------------

    def setting(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM settings WHERE key = ?", key)
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # --- users and sessions ---------------------------------------------------------------

    def create_user(self, email: str, password_hash: str, now: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)", (email, password_hash, now)
        )
        return int(cur.lastrowid)

    def user_by_email(self, email: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM users WHERE email = ?", email)

    def user_by_id(self, user_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM users WHERE id = ?", user_id)

    def set_password(self, user_id: int, password_hash: str) -> None:
        self.conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))

    def create_session(self, user_id: int, workspace_id: int, now: int) -> str:
        token = new_token()
        self.conn.execute(
            "INSERT INTO sessions (token_hash, user_id, workspace_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token_digest(token), user_id, workspace_id, now, now + SESSION_TTL),
        )
        return token

    def session(self, token: str | None, now: int) -> sqlite3.Row | None:
        if not token:
            return None
        return self._one(
            "SELECT s.token_hash, s.workspace_id, u.id AS user_id, u.email FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
            token_digest(token),
            now,
        )

    def set_session_workspace(self, token_hash: str, workspace_id: int) -> None:
        self.conn.execute("UPDATE sessions SET workspace_id = ? WHERE token_hash = ?", (workspace_id, token_hash))

    def delete_session(self, token: str) -> None:
        self.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))

    def delete_user_sessions(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # --- workspaces, members, invites -----------------------------------------------------

    def create_workspace(self, name: str, owner_id: int, now: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO workspaces (name, owner_id, created_at) VALUES (?, ?, ?)", (name, owner_id, now)
        )
        workspace_id = int(cur.lastrowid)
        self.conn.execute(
            "INSERT INTO memberships (workspace_id, user_id, role) VALUES (?, ?, 'owner')", (workspace_id, owner_id)
        )
        self.conn.execute("INSERT INTO subscriptions (workspace_id) VALUES (?)", (workspace_id,))
        return workspace_id

    def workspace(self, workspace_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM workspaces WHERE id = ?", workspace_id)

    def workspaces_for(self, user_id: int) -> list[sqlite3.Row]:
        return self._all(
            "SELECT w.id, w.name, m.role FROM memberships m JOIN workspaces w ON w.id = m.workspace_id "
            "WHERE m.user_id = ? ORDER BY m.role = 'owner' DESC, w.id",
            user_id,
        )

    def role(self, workspace_id: int, user_id: int) -> str | None:
        row = self._one(
            "SELECT role FROM memberships WHERE workspace_id = ? AND user_id = ?", workspace_id, user_id
        )
        return row["role"] if row else None

    def members(self, workspace_id: int) -> list[sqlite3.Row]:
        return self._all(
            "SELECT u.id, u.email, m.role FROM memberships m JOIN users u ON u.id = m.user_id "
            "WHERE m.workspace_id = ? ORDER BY m.role = 'owner' DESC, u.email",
            workspace_id,
        )

    def add_member(self, workspace_id: int, user_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO memberships (workspace_id, user_id, role) VALUES (?, ?, 'member')",
            (workspace_id, user_id),
        )

    def remove_member(self, workspace_id: int, user_id: int) -> None:
        self.conn.execute(
            "DELETE FROM memberships WHERE workspace_id = ? AND user_id = ? AND role = 'member'",
            (workspace_id, user_id),
        )
        self.conn.execute(
            "UPDATE sessions SET workspace_id = (SELECT w.id FROM workspaces w WHERE w.owner_id = sessions.user_id "
            "ORDER BY w.id LIMIT 1) WHERE user_id = ? AND workspace_id = ?",
            (user_id, workspace_id),
        )

    def create_invite(self, workspace_id: int, email: str, now: int) -> str:
        token = new_token()
        self.conn.execute("DELETE FROM invites WHERE workspace_id = ? AND email = ? AND accepted_at IS NULL",
                          (workspace_id, email))  # fmt: skip
        self.conn.execute(
            "INSERT INTO invites (token_hash, workspace_id, email, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token_digest(token), workspace_id, email, now, now + INVITE_TTL),
        )
        return token

    def invite(self, token: str, now: int) -> sqlite3.Row | None:
        return self._one(
            "SELECT i.*, w.name AS workspace_name FROM invites i JOIN workspaces w ON w.id = i.workspace_id "
            "WHERE i.token_hash = ? AND i.accepted_at IS NULL AND i.expires_at > ?",
            token_digest(token),
            now,
        )

    def pending_invites(self, workspace_id: int, now: int) -> list[sqlite3.Row]:
        return self._all(
            "SELECT email, expires_at FROM invites WHERE workspace_id = ? AND accepted_at IS NULL "
            "AND expires_at > ? ORDER BY email",
            workspace_id,
            now,
        )

    def revoke_invite(self, workspace_id: int, email: str) -> None:
        self.conn.execute(
            "DELETE FROM invites WHERE workspace_id = ? AND email = ? AND accepted_at IS NULL", (workspace_id, email)
        )

    def accept_invite(self, token: str, user_id: int, now: int) -> int | None:
        """Mark the invite used and add the member. Returns the workspace id, or None if it's no longer valid."""
        cur = self.conn.execute(
            "UPDATE invites SET accepted_at = ? WHERE token_hash = ? AND accepted_at IS NULL AND expires_at > ? "
            "RETURNING workspace_id",
            (now, token_digest(token), now),
        )
        row = cur.fetchone()
        if row is None:
            return None
        self.add_member(row["workspace_id"], user_id)
        return int(row["workspace_id"])

    def seats_used(self, workspace_id: int, now: int) -> int:
        members = self._one("SELECT COUNT(*) FROM memberships WHERE workspace_id = ?", workspace_id)[0]
        return members + len(self.pending_invites(workspace_id, now))

    # --- password resets and outbox -------------------------------------------------------

    def create_reset(self, user_id: int, now: int) -> str:
        token = new_token()
        self.conn.execute(
            "INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_digest(token), user_id, now + RESET_TTL),
        )
        return token

    def reset_valid(self, token: str, now: int) -> bool:
        row = self._one(
            "SELECT 1 FROM password_resets WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
            token_digest(token),
            now,
        )
        return row is not None

    def use_reset(self, token: str, now: int) -> int | None:
        """Single use: the UPDATE only matches an unused, unexpired token."""
        row = self.conn.execute(
            "UPDATE password_resets SET used_at = ? WHERE token_hash = ? AND used_at IS NULL AND expires_at > ? "
            "RETURNING user_id",
            (now, token_digest(token), now),
        ).fetchone()
        return int(row["user_id"]) if row else None

    def send_email(self, to: str, subject: str, body: str, now: int) -> None:
        self.conn.execute(
            "INSERT INTO outbox (to_email, subject, body, created_at) VALUES (?, ?, ?, ?)", (to, subject, body, now)
        )

    def outbox(self, limit: int = 50) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", limit)

    # --- projects -------------------------------------------------------------------------

    def projects(self, workspace_id: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM projects WHERE workspace_id = ? ORDER BY id", workspace_id)

    def count_projects(self, workspace_id: int) -> int:
        return self._one("SELECT COUNT(*) FROM projects WHERE workspace_id = ?", workspace_id)[0]

    def create_project(self, workspace_id: int, name: str, now: int) -> None:
        self.conn.execute(
            "INSERT INTO projects (workspace_id, name, created_at) VALUES (?, ?, ?)", (workspace_id, name, now)
        )

    def delete_project(self, workspace_id: int, project_id: int) -> None:
        self.conn.execute("DELETE FROM projects WHERE id = ? AND workspace_id = ?", (project_id, workspace_id))

    # --- subscriptions and webhook events -------------------------------------------------

    def subscription(self, workspace_id: int) -> Subscription:
        row = self._one("SELECT * FROM subscriptions WHERE workspace_id = ?", workspace_id)
        if row is None:
            return Subscription()
        return Subscription(
            plan=row["plan"],
            status=row["status"],
            customer_id=row["customer_id"],
            subscription_id=row["subscription_id"],
            current_period_end=row["current_period_end"],
            cancel_at_period_end=bool(row["cancel_at_period_end"]),
            last_event_at=row["last_event_at"],
        )

    def save_subscription(self, workspace_id: int, sub: Subscription) -> None:
        self.conn.execute(
            "UPDATE subscriptions SET plan = ?, status = ?, customer_id = ?, subscription_id = ?, "
            "current_period_end = ?, cancel_at_period_end = ?, last_event_at = ? WHERE workspace_id = ?",
            (sub.plan, sub.status, sub.customer_id, sub.subscription_id, sub.current_period_end,
             int(sub.cancel_at_period_end), sub.last_event_at, workspace_id),
        )  # fmt: skip

    def workspace_for_billing(self, subscription_id: str | None, customer_id: str | None) -> int | None:
        for column, value in (("subscription_id", subscription_id), ("customer_id", customer_id)):
            if value:
                row = self._one(f"SELECT workspace_id FROM subscriptions WHERE {column} = ?", value)
                if row:
                    return int(row["workspace_id"])
        return None

    def event_seen(self, event_id: str) -> bool:
        return self._one("SELECT 1 FROM webhook_events WHERE id = ?", event_id) is not None

    def record_event(self, event_id: str, kind: str, workspace_id: int | None, now: int, outcome: str,
                     payload: str) -> None:  # fmt: skip
        self.conn.execute(
            "INSERT INTO webhook_events (id, type, workspace_id, received_at, outcome, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, kind, workspace_id, now, outcome, payload),
        )

    def recent_events(self, workspace_id: int, limit: int = 8) -> list[sqlite3.Row]:
        return self._all(
            "SELECT id, type, received_at, outcome FROM webhook_events WHERE workspace_id = ? "
            "ORDER BY received_at DESC, rowid DESC LIMIT ?",
            workspace_id,
            limit,
        )
