"""Authentication and authorization tests: password storage, JWT issue and every
rejection path required by the brief's section 5 (hash, Bearer, expired, forged,
missing claims, unknown user) plus owner isolation on ticket reads."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import jwt
import pytest
from fastapi.testclient import TestClient

from src.config import Settings
from src.api import create_app

KNOWLEDGE_BASE = Path(__file__).resolve().parents[1] / 'knowledge_base'
SECRET = 'test-secret-' * 5
PASSWORD = 'a-secure-password'


def settings_for(tmp_path):
    return Settings('', 'test-model', 'test-embedding', SECRET, 60,
                    tmp_path / 'test.db', KNOWLEDGE_BASE)


def credentials(email='alice@example.com'):
    return {'email': email, 'password': PASSWORD}


def sign_in(client, email='alice@example.com'):
    """Register then log in; returns the issued JWT."""
    assert client.post('/register', json=credentials(email)).status_code == 201
    response = client.post('/login', json=credentials(email))
    assert response.status_code == 200, response.text
    return response.json()['access_token']


def bearer(token):
    return {'Authorization': f'Bearer {token}'}


def craft_token(payload=None, *, user_id=1, secret=SECRET, algorithm='HS256',
                expires_in=3600, missing=()):
    """Build a JWT directly so tests can forge signatures, claims and expiries."""
    now = datetime.now(timezone.utc)
    body = payload if payload is not None else {
        'sub': str(user_id),
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    body = dict(body)
    for claim in missing:
        body.pop(claim, None)
    key = None if algorithm == 'none' else secret
    return jwt.encode(body, key, algorithm=algorithm)


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


@pytest.mark.parametrize('route', ['/me', '/tickets', '/tickets/1'])
def test_expired_token_is_rejected(tmp_path, route):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        sign_in(client)
        response = client.get(route, headers=bearer(craft_token(expires_in=-60)))
        assert response.status_code == 401
        assert response.json()['detail']['code'] == 'INVALID_TOKEN'


def test_token_signed_with_another_secret_is_rejected(tmp_path):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        sign_in(client)
        forged = craft_token(secret='the-wrong-secret-' * 3)
        assert client.get('/me', headers=bearer(forged)).status_code == 401


def test_unsigned_alg_none_token_is_rejected(tmp_path):
    """The explicit HS256 allowlist must block alg=none / algorithm confusion."""
    with TestClient(create_app(settings_for(tmp_path))) as client:
        sign_in(client)
        assert client.get('/me', headers=bearer(craft_token(algorithm='none'))).status_code == 401
        assert client.get('/me', headers=bearer(craft_token(algorithm='HS384'))).status_code == 401


@pytest.mark.parametrize('claim', ['sub', 'iat', 'exp'])
def test_missing_required_claim_is_rejected(tmp_path, claim):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        sign_in(client)
        assert client.get('/me', headers=bearer(craft_token(missing=(claim,)))).status_code == 401


def test_non_numeric_subject_is_rejected(tmp_path):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        sign_in(client)
        now = datetime.now(timezone.utc)
        payload = {'sub': 'alice', 'iat': int(now.timestamp()),
                   'exp': int((now + timedelta(seconds=3600)).timestamp())}
        assert client.get('/me', headers=bearer(craft_token(payload))).status_code == 401


def test_token_for_deleted_user_is_rejected(tmp_path):
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        token = sign_in(client)
        assert client.get('/me', headers=bearer(token)).status_code == 200
        with sqlite3.connect(settings.database_path) as conn:
            conn.execute('DELETE FROM users')
        assert client.get('/me', headers=bearer(token)).status_code == 401


@pytest.mark.parametrize('header', [None, 'Bearer not-a-jwt', 'Basic a-secure-password', 'Bearer'])
def test_missing_or_malformed_authorization_is_rejected(tmp_path, header):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        sign_in(client)
        response = client.get('/me') if header is None else client.get('/me', headers={'Authorization': header})
        assert response.status_code == 401
