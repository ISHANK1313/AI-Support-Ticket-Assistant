from pathlib import Path

from fastapi.testclient import TestClient

from src.config import Settings
from src.api import create_app


def test_registration_login_and_me(tmp_path):
    settings = Settings('', 'test-model', 'test-embedding', 'test-secret-' * 5, 60,
                        tmp_path / 'test.db', Path(__file__).resolve().parents[1] / 'knowledge_base')
    with TestClient(create_app(settings)) as client:
        credentials = {'email': 'Alice@example.com', 'password': 'a-secure-password'}
        registered = client.post('/register', json=credentials)
        assert registered.status_code == 201, registered.text
        assert 'password_hash' not in registered.json()
        login = client.post('/login', json=credentials)
        assert login.status_code == 200, login.text
        token = login.json()['access_token']
        me = client.get('/me', headers={'Authorization': f'Bearer {token}'})
        assert me.status_code == 200, me.text
        assert me.json()['email'] == 'alice@example.com'
        assert client.get('/me').status_code == 401
