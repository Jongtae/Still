"""Pin the pre-#664 memberships independently of the implementation."""
import re
import sqlite3
from pathlib import Path

import pytest

from personal_agent import work_status as ws

# Each column is (constant, predicate); each row below pins every column.
QUESTIONS = (
    ('RESUMABLE_CONTINUATION', 'is_resumable_continuation'),
    ('RESUMABLE_SETTINGS', 'is_resumable_settings'),
    ('RESUMABLE_SETTINGS_SETTLEMENT', 'is_resumable_settings_settlement'),
    ('RESUMABLE_ACTION', 'is_resumable_action'),
    ('RESUMABLE_ACTION_DECISION', 'is_resumable_action_decision'),
    ('RESUMABLE_LOGIN_EXPLICIT', 'is_resumable_login_explicit'),
    ('RESUMABLE_INTERRUPTED', 'is_resumable_interrupted'),
    ('INCOMPLETE_RESULT', 'has_incomplete_result'),
    ('DELIVERED_RESULT', 'has_delivered_result'),
    ('AVAILABLE_RESULT', 'has_available_result'),
    ('REVIEWABLE_RESULT', 'has_reviewable_result'),
    ('ABNORMAL_END', 'ended_abnormally'),
    ('NEEDS_ATTENTION', 'needs_attention'),
    ('OBSERVED_STOP', 'has_observed_stop'),
    ('STEER_FAILED', 'has_failed_steer'),
    ('TERMINAL', 'is_terminal'),
    ('TERMINAL_OUTCOME', 'is_terminal_outcome'),
    ('ACTIVE', 'is_active'),
    ('ACTIVE_PREPARATION', 'is_active_preparation'),
    ('ACTIVE_APPROVAL', 'is_active_approval'),
    ('ACTIVE_PARKED', 'is_active_parked'),
    ('WAITING_PREREQUISITE', 'is_waiting_prerequisite'),
    ('WAITING_CONNECTION_DRIVE', 'is_waiting_connection_drive'),
    ('QUEUED_CONNECTION', 'is_queued_connection'),
    ('QUEUED_CONTEXT', 'is_queued_context'),
    ('REFUSABLE_PREPARATION', 'is_refusable_preparation'),
    ('SUCCEEDED', 'is_succeeded'),
    ('FAILED', 'is_failed'),
    ('CANCELLED', 'is_cancelled'),
    ('EFFECT_UNKNOWN', 'is_effect_unknown'),
    ('AWAITING_CONNECTION', 'is_awaiting_connection'),
    ('INCOMPLETE_ANSWER', 'has_incomplete_answer'),
    ('UNFINISHED_PORTION', 'has_unfinished_portion'),
    ('UNSPECIFIED_OR_SUCCEEDED', 'is_unspecified_or_succeeded'),
    ('INSPECTABLE_ANSWER', 'has_inspectable_answer'),
    ('UNVERIFIED_RESULT', 'has_unverified_result'),
)

# 1 = member / predicate True; 0 = non-member / predicate False.
EXPECTED = (
    ('queued', '000000000000000001111001110000000000'),
    ('running', '000010000000000001111000000000000000'),
    ('awaiting_approval', '000000000000000000010000000000000000'),
    ('awaiting_connection', '011000000000000000101111010000100000'),
    ('awaiting_context', '000000000000000000001100100000000000'),
    ('awaiting_drive', '000000000000000000001110000000000000'),
    ('succeeded', '001111001110000110000000011000000110'),
    ('partial', '111111111110100110000000010000011011'),
    ('failed', '111111110111111110000000010100010011'),
    ('interrupted', '011110100001111110000000010000011011'),
    ('cancelled', '000000000000011110000000000010000000'),
    ('unknown', '000000000010100110000000000001000011'),
    ('blocked', '000000000000000100000000000000000000'),
    ('unsupported', '000000000000001000000000000000000000'),
    (None, '000000000000000000000000000000000100'),
    ('unrecognised-status', '000000000000000000000000000000000000'),
)


@pytest.mark.parametrize("status,expected", EXPECTED)
def test_exact_legacy_membership(status, expected):
    assert len(expected) == len(QUESTIONS)
    for (constant_name, predicate_name), bit in zip(QUESTIONS, expected):
        constant = getattr(ws, constant_name)
        predicate = getattr(ws, predicate_name)
        assert isinstance(constant, frozenset)
        assert (status in constant) is (bit == "1"), constant_name
        assert predicate(status) is (bit == "1"), predicate_name
        assert (not predicate(status)) is (bit == "0"), predicate_name


def test_all_constants_and_predicates_are_pinned():
    # #632: stored-fact vocabularies and helpers are pinned in test_work_facts_632.
    assert {name for name, value in vars(ws).items() if isinstance(value, frozenset)} == {name for name, _ in QUESTIONS} | {"GOAL_OUTCOMES", "EFFECT_STATES"}
    assert {name for name, value in vars(ws).items() if callable(value) and value.__module__ == ws.__name__ and name not in {"sql_in", "goal_outcome_of", "effect_state_of", "facts_recorded", "goal_reached", "effect_floor_clean", "request_met", "derive_status"}} == {name for _, name in QUESTIONS}
    for column, (name, _) in enumerate(QUESTIONS):
        assert getattr(ws, name) == frozenset(status for status, row in EXPECTED if row[column] == "1")


def test_photo_cleanup_trigger_matches_predicates():
    source = Path(__file__).resolve().parents[1] / "src/personal_agent/quickstart_store.py"
    trigger = source.read_text().split("CREATE TRIGGER telegram_photo_terminal_cleanup", 1)[1].split("END;", 1)[0]
    lists = re.findall(r"NEW\.status IN \(([^)]+)\)", trigger)
    assert len(lists) == 2
    terminal, continuation = ({value.strip().strip("'") for value in members.split(",")} for members in lists)
    assert terminal == ws.TERMINAL
    assert continuation == ws.RESUMABLE_CONTINUATION
    for status, _ in EXPECTED:
        assert (status in terminal) is ws.is_terminal(status)
        assert (status in continuation) is ws.is_resumable_continuation(status)


@pytest.mark.parametrize("constant_name,old_literal", (
    ('RESUMABLE_CONTINUATION', "('partial','failed')"),
    ('RESUMABLE_SETTINGS', "('partial','failed','interrupted','awaiting_connection')"),
    ('RESUMABLE_SETTINGS_SETTLEMENT', "('partial','failed','interrupted','succeeded','awaiting_connection')"),
    ('RESUMABLE_ACTION', "('failed','partial','interrupted','succeeded')"),
    ('RESUMABLE_ACTION_DECISION', "('running','failed','partial','interrupted','succeeded')"),
    ('RESUMABLE_LOGIN_EXPLICIT', "('failed','partial','succeeded')"),
    ('RESUMABLE_INTERRUPTED', "('partial','failed','interrupted')"),
    ('INCOMPLETE_RESULT', "('failed','partial')"),
    ('DELIVERED_RESULT', "('succeeded','partial')"),
    ('AVAILABLE_RESULT', "('succeeded','partial','failed')"),
    ('REVIEWABLE_RESULT', "('succeeded','partial','failed','unknown')"),
    ('ABNORMAL_END', "('failed','interrupted')"),
    ('NEEDS_ATTENTION', "('failed','partial','interrupted','unknown')"),
    ('OBSERVED_STOP', "('failed','interrupted','cancelled')"),
    ('STEER_FAILED', "('failed','interrupted','cancelled','unsupported')"),
    ('TERMINAL', "('succeeded','failed','partial','cancelled','interrupted','unknown','blocked')"),
    ('TERMINAL_OUTCOME', "('succeeded','partial','failed','unknown','cancelled','interrupted')"),
    ('ACTIVE', "('queued','running')"),
    ('ACTIVE_PREPARATION', "('queued','running','awaiting_connection')"),
    ('ACTIVE_APPROVAL', "('queued','running','awaiting_approval')"),
    ('ACTIVE_PARKED', "('awaiting_connection','awaiting_drive','awaiting_context','queued','running')"),
    ('WAITING_PREREQUISITE', "('awaiting_context','awaiting_drive','awaiting_connection')"),
    ('WAITING_CONNECTION_DRIVE', "('awaiting_connection','awaiting_drive')"),
    ('QUEUED_CONNECTION', "('awaiting_connection','queued')"),
    ('QUEUED_CONTEXT', "('queued','awaiting_context')"),
    ('REFUSABLE_PREPARATION', "('queued','awaiting_connection','failed','partial','interrupted','succeeded')"),
    ('SUCCEEDED', "('succeeded')"),
    ('FAILED', "('failed')"),
    ('CANCELLED', "('cancelled')"),
    ('EFFECT_UNKNOWN', "('unknown')"),
    ('AWAITING_CONNECTION', "('awaiting_connection')"),
    ('INCOMPLETE_ANSWER', "('failed','partial','interrupted')"),
    ('UNFINISHED_PORTION', "('partial','interrupted')"),
    ('INSPECTABLE_ANSWER', "('succeeded','partial','failed','interrupted','unknown')"),
    ('UNVERIFIED_RESULT', "('failed','partial','interrupted','unknown')"),
))
def test_sql_in_selects_exact_legacy_rows(constant_name, old_literal):
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE jobs(status TEXT)")
        db.executemany("INSERT INTO jobs VALUES (?)", ((status,) for status, _ in EXPECTED))
        clause = ws.sql_in(getattr(ws, constant_name))
        assert clause == "(" + ",".join(sorted(old_literal[1:-1].split(","))) + ")"
        for direction in ("IN", "NOT IN"):
            legacy = db.execute(f"SELECT rowid,status FROM jobs WHERE status {direction} {old_literal} ORDER BY rowid").fetchall()
            actual = db.execute(f"SELECT rowid,status FROM jobs WHERE status {direction} {clause} ORDER BY rowid").fetchall()
            assert actual == legacy


def test_legacy_public_aliases():
    from personal_agent import owner_model, telegram_presence
    assert owner_model.REVIEWABLE_STATUSES is ws.REVIEWABLE_RESULT
    assert telegram_presence.TERMINAL_OUTCOMES is ws.TERMINAL_OUTCOME
