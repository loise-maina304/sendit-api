def test_full_crud_flow(client, monkeypatch):
    async def fake_weather(city, country):
        return {
            "city": city,
            "country": country,
            "temperature": 20,
            "windspeed": 5,
            "weathercode": 1,
            "time": "2026-08-13T08:00",
            "source": "Test"
        }

    monkeypatch.setattr("main.get_weather", fake_weather)

    user = {
        "username": "integrationmanager",
        "email": "integrationmanager@example.com",
        "password": "testpass123",
        "full_name": "Integration Manager",
        "role": "manager"
    }

    register_response = client.post("/register", json=user)
    assert register_response.status_code in [200, 201]

    login_response = client.post(
        "/login",
        data={
            "username": user["username"],
            "password": user["password"]
        }
    )

    assert login_response.status_code == 200

    token = login_response.json()["access_token"]

    headers = {
        "Authorization": f"Bearer {token}"
    }

    file_content = b"%PDF-1.4 test document"

    upload_response = client.post(
        "/documents/upload",
        files={
            "file": (
                "integration.pdf",
                file_content,
                "application/pdf"
            )
        },
        data={
            "city": "Nyeri",
            "country": "Kenya",
            "description": "Integration test document"
        },
        headers=headers
    )

    assert upload_response.status_code == 200

    document_id = upload_response.json()["document_id"]

    update_response = client.put(
        f"/documents/{document_id}",
        json={
            "city": "Nairobi",
            "description": "Updated integration document"
        },
        headers=headers
    )

    assert update_response.status_code == 200
    assert update_response.json()["city"] == "Nairobi"

    delete_response = client.delete(
        f"/documents/{document_id}",
        headers=headers
    )

    assert delete_response.status_code == 200
