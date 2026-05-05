from __future__ import annotations

from app.auth import SessionStore, generate_salt, hash_password, verify_password


def test_verify_password_accepts_correct_password() -> None:
    salt = generate_salt()
    password_hash = hash_password("secret-pass", salt)

    assert verify_password("secret-pass", salt, password_hash) is True
    assert verify_password("wrong-pass", salt, password_hash) is False


def test_session_store_round_trip() -> None:
    sessions = SessionStore()

    token = sessions.create_session()

    assert sessions.is_valid(token) is True
    sessions.destroy_session(token)
    assert sessions.is_valid(token) is False
