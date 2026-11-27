"""#1277 blocker listing: a temporary store, no model and never the owner's data folder."""
import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evals'))
from agentos_eval import blockers, cli
from personal_agent.owner_model import KIND_SELF_REVIEW, Upkeep
from personal_agent.quickstart_store import QuickStore

REQUEST, ANSWER, MESSAGE = 'SECRET-REQUEST', 'SECRET-ANSWER', 'SECRET-MESSAGE'


@pytest.fixture
def store(tmp_path):
    return QuickStore(tmp_path / 'data')


def add_work(store, work_id, blocker, *, created=1_700_000_000.0, tool='self_review'):
    """A Work with owner text, and its event written the way ``Upkeep.finish`` stores it."""
    with store.db() as db:
        db.execute('INSERT INTO jobs(id,request_key,message,response,created) VALUES (?,?,?,?,?)',
                   (work_id, work_id, REQUEST, ANSWER, created))
        db.execute('INSERT INTO messages(role,content,channel,created,job_id) VALUES (?,?,?,?,?)',
                   ('user', MESSAGE, 'web', created, work_id))
    detail = {'reason': 'correction', 'blocker': blocker}
    if tool == 'self_review':
        Upkeep(store).finish(work_id, 'done', 1, 'recorded', detail, created, kind=KIND_SELF_REVIEW)
    else:
        with store.db() as db:
            db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                       (work_id, tool, 'recorded', json.dumps(detail), created))


def run_cli(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(['blockers', *argv])
    return code, out.getvalue()


def test_empty_store_lists_nothing(store):
    assert blockers.read_blockers(store.path) == []
    code, out = run_cli('--store', str(store.path))
    assert code == 0 and '0 in 0 groups' in out


def test_connection_refuses_writes(store):
    add_work(store, 'w1', 'The skill names a removed page.')
    db = blockers.connect(blockers.store_path(store.path))
    try:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute('DELETE FROM tool_events')
    finally:
        db.close()
    assert len(blockers.read_blockers(store.path)) == 1


def test_missing_store_is_not_created(tmp_path):
    with pytest.raises(SystemExit):
        run_cli('--store', str(tmp_path / 'absent.db'))
    assert not (tmp_path / 'absent.db').exists()


def test_groups_matching_wording_and_prints_no_owner_text(store):
    add_work(store, 'w1', 'The skill names a removed page.')
    add_work(store, 'w2', 'A different cause')
    add_work(store, 'w3', 'the skill  names a removed\npage')
    add_work(store, 'w4', None)
    add_work(store, 'w5', 'not a self-review', tool='owner_model')
    rows = blockers.read_blockers(store.path)
    assert [[row['work_id'] for row in members] for members in blockers.group(rows)] == [['w3', 'w1'], ['w2']]
    # The data folder works as well as the file.
    code, out = run_cli('--store', str(store.root))
    assert code == 0
    assert '3 in 2 groups' in out and '2x the skill names a removed page' in out
    assert '2023-11-1' in out and 'w1' in out and 'w2' in out
    assert 'not a self-review' not in out and 'w4' not in out
    for owner_text in (REQUEST, ANSWER, MESSAGE):
        assert owner_text not in out
    assert all(set(row) == {'work_id', 'created', 'blocker'} for row in rows)


def test_forgotten_work_is_left_out(store):
    add_work(store, 'w1', 'kept')
    add_work(store, 'w2', 'about something forgotten')
    with store.db() as db:
        db.execute("INSERT INTO forgotten_messages(message_id,receipt_id,created) "
                   "SELECT id,'r1',1 FROM messages WHERE job_id='w2'")
    assert [row['work_id'] for row in blockers.read_blockers(store.path)] == ['w1']


def test_scan_bounds_the_events_read(store):
    add_work(store, 'w1', 'older')
    add_work(store, 'w2', 'newer')
    assert [row['work_id'] for row in blockers.read_blockers(store.path, scan=1)] == ['w2']


def test_undated_row_and_out_of_range_scan(store):
    add_work(store, 'w1', 'no date')
    with store.db() as db:
        db.execute("UPDATE tool_events SET created=NULL WHERE tool='self_review'")
    code, out = run_cli('--store', str(store.path))
    assert code == 0 and 'undated  w1' in out
    with pytest.raises(SystemExit, match='positive'):
        run_cli('--store', str(store.path), '--scan', str(2**63))


def test_store_argument_has_no_default():
    with pytest.raises(SystemExit):
        run_cli()
