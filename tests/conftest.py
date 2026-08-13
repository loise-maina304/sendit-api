import uuid

import pytest
from fastapi.testclient import TestClient
from main import app, limiter


@pytest.fixture
def client():
    # Disable rate limiting during automated tests only.
    limiter.enabled = False
    app.state.limiter.enabled = False

    with TestClient(app) as test_client:
        yield test_client

    limiter.enabled = True
    app.state.limiter.enabled = True


@pytest.fixture
def test_user():
    unique = uuid.uuid4().hex[:8]

    return {
        "username": f"testuser{unique}",
        "email": f"test{unique}@example.com",
        "password": "TestPassword123!",
        "full_name": "Test User",
        "role": "staff",
    }


@pytest.fixture
def auth_headers(client, test_user):
    register_response = client.post("/register", json=test_user)

    assert register_response.status_code in [200, 201], (
        f"Registration failed: "
        f"{register_response.status_code} "
        f"{register_response.text}"
    )

    login_response = client.post(
        "/login",
        data={"username": test_user["username"], "password": test_user["password"]},
    )

    assert login_response.status_code == 200, (
        f"Login failed: " f"{login_response.status_code} " f"{login_response.text}"
    )

    token = login_response.json()["access_token"]

    return {"Authorization": f"Bearer {token}"}
