def test_list_documents_performance(client, auth_headers, benchmark):
    def list_documents():
        response = client.get("/documents", headers=auth_headers)
        assert response.status_code == 200

    benchmark(list_documents)
