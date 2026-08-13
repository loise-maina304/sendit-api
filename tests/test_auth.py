def test_register_user(client, test_user):
    response = client.post("/register", json=test_user)

    assert response.status_code in [200, 201]

    data = response.json()

    assert data["username"] == test_user["username"]
    assert data["email"] == test_user["email"]
    assert "password" not in data
    assert "hashed_password" not in data


def test_login_user(client, test_user):
    client.post("/register", json=test_user)

    response = client.post(
        "/login",
        data={"username": test_user["username"], "password": test_user["password"]},
    )

    assert response.status_code == 200

    data = response.json()

    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_invalid_password(client, test_user):
    client.post("/register", json=test_user)

    response = client.post(
        "/login", data={"username": test_user["username"], "password": "wrongpassword"}
    )

    assert response.status_code == 401


def test_me_requires_authentication(client):
    response = client.get("/me")

    assert response.status_code == 401


def test_me_authenticated(client, auth_headers):
    response = client.get("/me", headers=auth_headers)

    assert response.status_code == 200
    assert "username" in response.json()
