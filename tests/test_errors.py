def test_404_error(client):
    response = client.get("/this-route-does-not-exist")

    assert response.status_code == 404


def test_unauthorized_documents(client):
    response = client.get("/documents")

    assert response.status_code == 401


def test_unauthorized_search(client):
    response = client.get("/documents/search", params={"q": "test"})

    assert response.status_code == 401


def test_document_not_found(client, auth_headers):
    response = client.get("/documents/999999", headers=auth_headers)

    assert response.status_code == 404
