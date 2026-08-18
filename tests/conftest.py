import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_counter_checker.db")
os.environ.setdefault("APP_SECRET_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        test_client.post("/setup", data={
            "username": "admin", "display_name": "테스트 관리자",
            "password": "test-password-123",
        })
        yield test_client
    Base.metadata.drop_all(engine)
