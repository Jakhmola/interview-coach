from httpx import AsyncClient


async def test_register_login_me_happy_path(client: AsyncClient) -> None:
    creds = {"email": "alice@example.com", "password": "hunter22a"}

    r = await client.post("/auth/register", json=creds)
    assert r.status_code == 201
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "alice@example.com"
    token = body["access_token"]

    r = await client.post("/auth/login", json=creds)
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "alice@example.com"

    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "alice@example.com"


async def test_register_duplicate_email(client: AsyncClient) -> None:
    creds = {"email": "dup@example.com", "password": "hunter22a"}
    await client.post("/auth/register", json=creds)
    r = await client.post("/auth/register", json=creds)
    assert r.status_code == 409


async def test_login_wrong_password(client: AsyncClient) -> None:
    creds = {"email": "bob@example.com", "password": "hunter22a"}
    await client.post("/auth/register", json=creds)
    r = await client.post("/auth/login", json={**creds, "password": "wrongpass"})
    assert r.status_code == 401


async def test_login_unknown_email(client: AsyncClient) -> None:
    r = await client.post("/auth/login", json={"email": "ghost@example.com", "password": "x" * 8})
    assert r.status_code == 401


async def test_me_missing_token(client: AsyncClient) -> None:
    r = await client.get("/auth/me")
    assert r.status_code == 401


async def test_me_garbage_token(client: AsyncClient) -> None:
    r = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


async def test_register_short_password(client: AsyncClient) -> None:
    r = await client.post("/auth/register", json={"email": "a@b.com", "password": "short"})
    assert r.status_code == 422


async def test_register_bad_email(client: AsyncClient) -> None:
    r = await client.post("/auth/register", json={"email": "not-an-email", "password": "x" * 8})
    assert r.status_code == 422


async def test_email_normalized_lowercase(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"email": "MIXED@example.com", "password": "x" * 8})
    r = await client.post("/auth/login", json={"email": "mixed@example.com", "password": "x" * 8})
    assert r.status_code == 200
