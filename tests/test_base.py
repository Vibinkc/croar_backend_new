# ** Base Modules
from fastapi import status

# ** App Modules
from app.main import client


def test_home() -> None:
    response = client.get("/api/health")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": "All Healthy"}
