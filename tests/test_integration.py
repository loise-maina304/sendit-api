from uuid import uuid4


def test_full_auth_flow(client):
    unique = uuid4().hex[:8]

    user = {
        "username": f"integration{unique}",
        "email": f"integration{unique}@example.com",
        "password": "testpass123",
        "full_name": "Integration User",
    }

    register_response = client.post("/register", json=user)

    assert register_response.status_code in [200, 201], (
        f"Registration failed: "
        f"{register_response.status_code} "
        f"{register_response.text}"
    )

    login_response = client.post(
        "/login", data={"username": user["username"], "password": user["password"]}
    )

    assert login_response.status_code == 200, (
        f"Login failed: " f"{login_response.status_code} " f"{login_response.text}"
    )

    token = login_response.json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}

    me_response = client.get("/me", headers=headers)

    assert me_response.status_code == 200
    assert me_response.json()["username"] == user["username"]
