def test_csrf_cookie_not_secure(env):  # Review Focus 4: the app is served over plain http on 127.0.0.1
    from fastapi.testclient import TestClient

    fresh_client = TestClient(env.client.app)
    set_cookie = fresh_client.get("/").headers["set-cookie"]
    assert (
        "bh_csrf=" in set_cookie
        and "secure" not in set_cookie.lower()
        and "samesite=strict" in set_cookie.lower()
    )


def test_post_without_token_refused(env):
    c = env.any_pending()
    assert env.client.post(f"/cards/{c.id}/approve", data={"version": c.version}).status_code == 403
    assert env.fake.writes == []


def test_post_with_wrong_token_refused(env):
    c = env.any_pending()
    r = env.client.post(f"/cards/{c.id}/approve", data={"csrf": "forged", "version": c.version})
    assert r.status_code == 403 and env.fake.writes == []


def test_get_cannot_write(env):
    assert env.client.get(f"/cards/{env.any_pending().id}/approve").status_code == 405


def test_changed_version_refused(env):
    c = env.any_pending()
    assert (
        env.client.post(f"/cards/{c.id}/approve", data={"csrf": env.token, "version": "stale"}).status_code
        == 409
    )
    assert env.fake.writes == []
