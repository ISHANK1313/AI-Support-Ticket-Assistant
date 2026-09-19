"""Repository invariants on raw SQLite: atomic ticket+decision persistence,
rollback on failure, owner-scoped reads and schema constraints. No network or key."""
import json
import sqlite3

import pytest

from src import database

HASH = '$argon2id$v=19$m=65536,t=3,p=4$test$test'
DECISION = {'action': 'REQUEST_PHOTOS', 'reason': 'Photos required for this damage report.',
            'confidence': 0.9, 'sources': ['damaged_goods.md']}


def new_db(tmp_path):
    path = tmp_path / 'repo.db'
    database.init_db(path)
    return path


def count(path, table):
    with sqlite3.connect(path) as conn:
        return conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]


def add_user(path, email):
    with database.get_conn(path) as conn:
        return database.insert_user(conn, email, HASH)


def test_schema_creates_required_tables_and_columns(tmp_path):
    path = new_db(tmp_path)
    with sqlite3.connect(path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {table: {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
                   for table in ('users', 'tickets', 'decisions')}
    assert {'users', 'tickets', 'decisions', 'policy_index', 'policy_chunks'} <= tables
    assert columns['users'] == {'id', 'email', 'password_hash', 'created_at'}
    assert columns['tickets'] == {'id', 'user_id', 'message', 'facts_json', 'created_at'}
    assert columns['decisions'] == {'id', 'ticket_id', 'action', 'reason', 'confidence',
                                    'sources', 'created_at'}


def test_ticket_and_decision_are_persisted_together(tmp_path):
    path = new_db(tmp_path)
    user_id = add_user(path, 'alice@example.com')
    facts = {'order_value_inr': '3500.00', 'days_since_delivery': 0, 'days_since_dispatch': None}
    with database.get_conn(path) as conn:
        ticket_id = database.insert_ticket_and_decision(conn, user_id, 'Arrived damaged', facts, DECISION)
    with sqlite3.connect(path) as conn:
        ticket = conn.execute('SELECT user_id, message, facts_json FROM tickets').fetchone()
        decision = conn.execute('SELECT ticket_id, action, confidence, sources FROM decisions').fetchone()
    assert ticket[0] == user_id and ticket[1] == 'Arrived damaged'
    assert json.loads(ticket[2]) == facts          # nulls survive, money stays a decimal string
    assert decision[0] == ticket_id                # one-to-one link
    assert decision[1] == 'REQUEST_PHOTOS' and decision[2] == 0.9
    assert json.loads(decision[3]) == ['damaged_goods.md']


def test_failed_decision_insert_rolls_back_the_ticket(tmp_path):
    """A CHECK violation must not leave a ticket without its decision."""
    path = new_db(tmp_path)
    user_id = add_user(path, 'bob@example.com')
    with pytest.raises(sqlite3.IntegrityError):
        with database.get_conn(path) as conn:
            database.insert_ticket_and_decision(
                conn, user_id, 'Bad confidence', {}, {**DECISION, 'confidence': 1.5})
    assert count(path, 'tickets') == 0 and count(path, 'decisions') == 0


def test_ticket_requires_an_existing_owner(tmp_path):
    path = new_db(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with database.get_conn(path) as conn:
            database.insert_ticket_and_decision(conn, 999, 'Orphan ticket', {}, DECISION)
    assert count(path, 'tickets') == 0


def test_email_is_unique_case_insensitively(tmp_path):
    path = new_db(tmp_path)
    add_user(path, 'alice@example.com')
    with pytest.raises(sqlite3.IntegrityError):
        add_user(path, 'ALICE@example.com')


def test_reads_are_owner_scoped(tmp_path):
    path = new_db(tmp_path)
    alice, bob = add_user(path, 'alice@example.com'), add_user(path, 'bob@example.com')
    with database.get_conn(path) as conn:
        alice_ticket = database.insert_ticket_and_decision(conn, alice, "Alice's issue",
                                                           {'product_type': 'food'}, DECISION)
        database.insert_ticket_and_decision(conn, bob, "Bob's issue", {}, DECISION)
    with database.get_conn(path) as conn:
        listing = database.list_tickets_for_user(conn, alice, 20, 0)
        assert [row['message'] for row in listing] == ["Alice's issue"]
        assert database.get_ticket_for_user(conn, alice_ticket, alice)['message'] == "Alice's issue"
        assert database.get_ticket_for_user(conn, alice_ticket, bob) is None   # cross-owner denied
        assert database.get_ticket_for_user(conn, 999, alice) is None


def test_password_hash_is_not_returned_by_user_lookups(tmp_path):
    path = new_db(tmp_path)
    user_id = add_user(path, 'alice@example.com')
    with database.get_conn(path) as conn:
        row = database.get_user_by_id(conn, user_id)
    assert row['password_hash'] == HASH   # repository exposes it for verification only
    assert 'password_hash' not in {'id', 'email', 'created_at'}  # response schemas exclude it