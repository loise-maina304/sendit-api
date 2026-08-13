def test_list_documents(client, auth_headers):
    response = client.get("/documents", headers=auth_headers)

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_search_documents(client, auth_headers):
    response = client.get(
        "/documents/search", params={"q": "test"}, headers=auth_headers
    )

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_document_not_found(client, auth_headers):
    response = client.get("/documents/999999", headers=auth_headers)

    assert response.status_code == 404


def test_update_document_not_found(client, auth_headers):
    response = client.put(
        "/documents/999999",
        json={"city": "Nairobi", "country": "Kenya", "description": "Updated document"},
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_delete_document_requires_manager(client, auth_headers):
    response = client.delete("/documents/999999", headers=auth_headers)

    assert response.status_code in [401, 403, 404]
