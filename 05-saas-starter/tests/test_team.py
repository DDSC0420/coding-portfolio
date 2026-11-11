"""Workspaces, roles, invites and seat limits."""


def invite(owner, email):
    return owner.post("/app/team/invite", email=email)


def join(new_browser, owner, email):
    """Invite `email`, then have a new browser accept by signing up through the link."""
    assert invite(owner, email).headers["location"] == "/app/team?n=invited"
    link = next(link for link in owner.outbox_links("/invite/"))
    member = new_browser()
    assert member.get(link).status_code == 200
    r = member.signup(email, invite=link.rsplit("/", 1)[1])
    assert r.headers["location"] == "/app?n=joined"
    return member


def test_inviting_needs_the_team_plan(owner):
    assert invite(owner, "a@example.com").status_code == 402
    owner.upgrade("pro")
    r = invite(owner, "a@example.com")
    assert r.status_code == 402 and "Team plan" in r.text


def test_member_joins_and_shares_the_workspace(owner, new_browser):
    owner.upgrade("team")
    owner.post("/app/projects", name="Shared roadmap")
    member = join(new_browser, owner, "sam@example.com")
    page = member.get("/app").text
    assert "Shared roadmap" in page and "owner&#39;s workspace" in page
    assert member.status()[1] == "Team"  # the workspace's plan applies to everyone in it
    members = owner.get("/app/team").text
    assert "sam@example.com" in members and ">member<" in members


def test_seat_limit_counts_members_and_pending_invites(owner, new_browser):
    owner.upgrade("team")
    join(new_browser, owner, "m1@example.com")
    for email in ("m2@example.com", "m3@example.com", "m4@example.com"):
        assert invite(owner, email).status_code == 303
    full = invite(owner, "m5@example.com")  # owner + 1 member + 3 invites = 5 seats
    assert full.status_code == 402 and "All 5 seats are taken" in full.text
    assert invite(owner, "m4@example.com").status_code == 303  # re-sending an invite doesn't need a new seat
    owner.post("/app/team/revoke", email="m4@example.com")
    assert invite(owner, "m5@example.com").status_code == 303


def test_existing_member_cannot_be_invited_again(owner, new_browser):
    owner.upgrade("team")
    join(new_browser, owner, "sam@example.com")
    assert invite(owner, "sam@example.com").status_code == 400


def test_invite_for_someone_else_is_refused(owner, new_browser):
    owner.upgrade("team")
    invite(owner, "sam@example.com")
    (link,) = owner.outbox_links("/invite/")
    stranger = new_browser()
    stranger.signup("stranger@example.com")
    assert "This invite is for sam@example.com" in stranger.get(link).text
    assert stranger.post(f"{link}/accept").status_code == 403
    r = new_browser().signup("not-sam@example.com", invite=link.rsplit("/", 1)[1])
    assert r.status_code == 400 and "This invite is for sam@example.com" in r.text


def test_existing_user_accepts_by_signing_in(owner, new_browser):
    owner.upgrade("team")
    sam = new_browser()
    sam.signup("sam@example.com")
    invite(owner, "sam@example.com")
    (link,) = owner.outbox_links("/invite/")
    assert sam.post(f"{link}/accept").headers["location"] == "/app?n=joined"
    assert "owner&#39;s workspace" in sam.get("/app").text
    assert sam.get(link).status_code == 410  # used up
    assert sam.post(f"{link}/accept").status_code == 403


def test_invite_expires_after_seven_days(owner, new_browser, clock):
    owner.upgrade("team")
    invite(owner, "sam@example.com")
    (link,) = owner.outbox_links("/invite/")
    clock.advance(7 * 86400 + 1)
    assert new_browser().get(link).status_code == 410


def test_members_cannot_manage_billing_or_people(owner, new_browser):
    owner.upgrade("team")
    member = join(new_browser, owner, "sam@example.com")
    for path, data in [("/app/billing/checkout", {"plan": "pro"}), ("/app/billing/cancel", {}),
                       ("/app/team/invite", {"email": "x@example.com"}), ("/app/team/remove/1", {})]:  # fmt: skip
        assert member.post(path, **data).headers["location"] == "/app?n=owner-only"
    assert owner.status() == ("active", "Team")
    billing = member.get("/app/billing").text
    assert "Only the workspace owner can change the plan." in billing and 'data-testid="choose-pro"' not in billing


def test_workspace_switching_only_to_your_own(owner, new_browser):
    owner.upgrade("team")
    member = join(new_browser, owner, "sam@example.com")
    member.post("/app/projects", name="In shared")
    assert member.post("/app/switch/2").headers["location"] == "/app"  # sam's own workspace
    assert "In shared" not in member.get("/app").text
    assert member.post("/app/switch/1").status_code == 303
    outsider = new_browser()
    outsider.signup("outsider@example.com")
    assert outsider.post("/app/switch/1").status_code == 404


def test_removed_member_falls_back_to_their_own_workspace(owner, new_browser, db):
    owner.upgrade("team")
    member = join(new_browser, owner, "sam@example.com")
    sam_id = db.execute("SELECT id FROM users WHERE email = 'sam@example.com'").fetchone()[0]
    owner.post(f"/app/team/remove/{sam_id}")
    page = member.get("/app").text
    assert "sam&#39;s workspace" in page and "owner&#39;s workspace" not in page


def test_owner_cannot_be_removed(owner, db):
    owner.upgrade("team")
    owner.post("/app/team/remove/1")
    assert db.execute("SELECT COUNT(*) FROM memberships WHERE role = 'owner'").fetchone()[0] == 1
