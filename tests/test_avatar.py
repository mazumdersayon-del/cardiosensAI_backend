"""Profile pictures: JPEG/PNG by content, size limit, replace, remove, erased with the account."""

from app.core.storage import get_storage
from tests.conftest import auth, login

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


def _put(client, t, data: bytes, name: str = "me.jpg"):
    return client.put("/v1/me/avatar", files={"file": (name, data, "application/octet-stream")}, headers=auth(t))


def test_profile_picture_set_replace_and_remove(client):
    t = login(client, "+919844440001", name="Asha Rao")
    assert client.get("/v1/me", headers=auth(t)).json()["avatarVersion"] is None
    assert client.get("/v1/me/avatar", headers=auth(t)).status_code == 404

    r = _put(client, t, JPEG)
    assert r.status_code == 200, r.text
    assert r.json()["avatarVersion"]
    got = client.get("/v1/me/avatar", headers=auth(t))
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg" and got.content == JPEG

    from app.core.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        first_key = db.query(User).filter(User.phone == "+919844440001").one().avatar_key
    assert _put(client, t, PNG, "me.png").status_code == 200
    assert client.get("/v1/me/avatar", headers=auth(t)).headers["content-type"] == "image/png"
    try:
        get_storage().get(first_key)
        raise AssertionError("the replaced picture should be deleted")
    except FileNotFoundError:
        pass

    r = client.delete("/v1/me/avatar", headers=auth(t))
    assert r.status_code == 200 and r.json()["avatarVersion"] is None
    assert client.get("/v1/me/avatar", headers=auth(t)).status_code == 404


def test_profile_picture_rejects_other_files_and_large_ones(client):
    t = login(client, "+919844440002")
    assert _put(client, t, b"%PDF-1.7 not a picture", "me.jpg").json()["error"]["code"] == "unsupported_file"  # by content
    assert _put(client, t, JPEG + b"\x00" * 600_000).json()["error"]["code"] == "file_too_large"
    assert client.get("/v1/me/avatar").status_code == 401


def test_profile_picture_is_erased_with_the_account(client):
    t = login(client, "+919844440003")
    assert _put(client, t, JPEG).status_code == 200
    from app.core.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        key = db.query(User).filter(User.phone == "+919844440003").one().avatar_key
    assert get_storage().get(key) == JPEG
    assert client.delete("/v1/me", headers=auth(t)).status_code == 204
    try:
        get_storage().get(key)
        raise AssertionError("the picture should be erased")
    except FileNotFoundError:
        pass
