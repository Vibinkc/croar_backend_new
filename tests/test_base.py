"""Smoke tests for the app's health/root endpoints."""

from fastapi import status


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"status": "ok"}


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == status.HTTP_200_OK
    assert "Croar" in resp.json().get("message", "")
