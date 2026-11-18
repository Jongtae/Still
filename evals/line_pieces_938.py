"""#938 slice 2: does the line-run guidance sentence change what a real worker does?

A small before/after measurement (design D2, ``docs/design/line-pieces-938.en.md``). Each scenario
is one owner-set-up line with a one-off ``prepare`` row. Its run goes through the ordinary tick and
worker, on the configured real main AI, with and without ``lines.piece_guidance`` in the run's input.

Evidence class: live local observation of the exact checkout and model named in the record. It reuses
the model-free test fixture (``tests/test_entrusted_lines_798._LineCase``: a temporary store, a fake
clock, a recording Telegram transport, a FixtureDecisionEngine) and replaces only the scripted model
with the real provider. Outside reads go to a fixed synthetic world (no network besides the model).
Setup turns (opening the line) stay scripted so every arm starts from the same state.

This is development tooling. It is never imported by ``src/personal_agent``.

Usage (from the checkout)::

    PYTHONPATH=src:tests python evals/line_pieces_938.py --epochs 2 --out evals/records/938-line-pieces

The model and key are read from the owner's live data folder (read-only) unless ``--model`` and the
``OPENAI_API_KEY`` environment variable are given. The key is never written or printed.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from personal_agent import preparations as prep
from personal_agent import preparations_service as prep_service
from personal_agent.messages import msg as real_msg
from personal_agent.providers import request_json

from test_entrusted_lines_798 import _LineCase, tool_results
from test_preparations import SEOUL, call

LIVE = Path.home() / '.local/share/agentos/private'


def clock(now, seconds):
    return datetime.fromtimestamp(now + seconds, SEOUL).strftime('%H:%M')


#: Synthetic, generalized scenarios. ``expect_piece`` is the behavior the design asks for; ``hint`` is
#: the number of seconds after the run when the world says the next change comes (None: no wait).
SCENARIOS = [
    {'id': 'refund-pending', 'expect_piece': True, 'hint': 7200,
     'task': '반품 요청 R-1042의 환불이 shop.example에서 완료로 표시될 때까지 지켜보고, 완료되면 알려 주기',
     'promise': '환불이 완료되면 알려 드려요',
     'goal': 'shop.example 반품 R-1042 환불 상태 확인',
     'url': 'https://shop.example/returns/R-1042',
     'page': lambda now: (f'반품 R-1042 상태: 환불 처리 중. 환불 상태는 2시간마다 갱신되며 다음 갱신은 오늘 {clock(now, 7200)} 무렵입니다. '
                          '완료되면 이 페이지에 "환불 완료"가 표시됩니다.')},
    {'id': 'signup-opens-later', 'expect_piece': True, 'hint': 5400,
     'task': 'class.example의 주말 도예 수업 신청이 열리면 바로 신청 페이지 주소와 함께 알려 주기',
     'promise': '신청이 열리면 바로 알려 드려요',
     'goal': 'class.example 주말 도예 수업 신청 열렸는지 확인',
     'url': 'https://class.example/pottery-weekend',
     'page': lambda now: (f'주말 도예 수업. 신청은 오늘 {clock(now, 5400)}에 열립니다. 그 전에는 신청 버튼이 보이지 않습니다. '
                          '신청 페이지: https://class.example/pottery-weekend/apply')},
    {'id': 'one-go-summary', 'expect_piece': False, 'hint': None,
     'task': 'notice.example의 오늘 공지를 읽고 핵심만 세 줄로 정리해 주기',
     'promise': '정리해서 알려 드려요',
     'goal': 'notice.example 오늘 공지 세 줄 정리',
     'url': 'https://notice.example/today',
     'page': lambda now: ('오늘 공지: 1) 3층 회의실은 오후 2시부터 4시까지 점검으로 사용할 수 없습니다. '
                          '2) 사내 식당은 다음 주 월요일부터 7시 30분에 아침을 엽니다. '
                          '3) 주차장 B 구역 도색으로 금요일 하루 B 구역 주차가 안 됩니다. 이상입니다.')},
]


class World:
    """The fixed outside world of one scenario: a search returns its page, a read returns its text."""

    def __init__(self, scenario, now):
        self.scenario, self.now, self.plans = scenario, now, []

    def execute(self, plan, budget=None):
        self.plans.append(dict(plan))
        url, text = self.scenario['url'], self.scenario['page'](self.now())
        if plan.get('tool') == 'public_page_read':
            return {'tool': 'public_page_read', 'url': plan.get('url'), 'retrieved_at': self.now(),
                    'content': text if plan.get('url', '').rstrip('/') == url else 'Not found', 'sources': [plan.get('url')]}
        return {'tool': plan['tool'], 'query': plan.get('query'), 'retrieved_at': self.now(), 'sources': [url],
                'results': [{'title': url, 'url': url, 'snippet': text[:160]}]}


def live_model(args):
    if args.model:
        return {'provider': args.provider, 'endpoint': args.endpoint, 'model': args.model}, os.environ['OPENAI_API_KEY']
    db = sqlite3.connect(f'file:{LIVE / "quickstart.db"}?mode=ro', uri=True)
    config = json.loads(db.execute("SELECT value FROM config WHERE key='model'").fetchone()[0])
    key = json.loads((LIVE / 'connections.json').read_text()).get('model_key', '')
    return {name: config[name] for name in ('provider', 'endpoint', 'model')}, key


class Run(_LineCase):
    """One scenario, one arm: the fixture with the real model in place of the script."""

    MODEL = KEY = None
    real = False

    def runTest(self):  # unittest needs one; the measurement drives setUp and measure() itself
        pass

    def _model(self, url, body, headers=None, timeout=60):
        if not self.real:  # the fixture's own setup and the scripted opening turn
            return super()._model(url, body, headers, timeout)
        self.model_calls.append(json.loads(json.dumps(body)))
        return request_json(url, body, headers, timeout=max(timeout, 180))

    def use_real_model(self):
        self.real = True
        self.service.save_model({**self.MODEL, 'api_key': self.KEY})
        probe = self.service.test_model()  # the tool-call connection check, on the real model
        if not probe.get('ok'):
            raise RuntimeError(f'model connection check failed: {probe}')
        self.model_calls.clear()

    def open_scenario(self, scenario):
        args = {'action': 'open', 'task': scenario['task'], 'report_promise': scenario['promise']}

        def schedule(body):
            [opened] = tool_results(body, 'entrusted_line')
            return {'content': None, 'tool_calls': [call('s', 'schedule_preparation', kind='prepare', goal=scenario['goal'],
                                                         due=self.due_iso(600), line=opened['line_id'])]}
        self.script = [{'content': None, 'tool_calls': [call('o', 'entrusted_line', **args)]}, schedule,
                       {'content': '맡아서 할게요.'}]
        self.receive(scenario['task'] + ' 맡아 줘')
        self.script = []
        self.deliver_all()
        brief = self.service.lines.lines()[0]
        [row] = self.work_rows(brief['line_id'])
        return brief, row

    def measure(self, scenario, guidance):
        world = World(scenario, lambda: self.now)
        self.service.local_tools = world
        brief, row = self.open_scenario(scenario)
        self.use_real_model()
        started = self.now = max(self.now, row['due_at']) + 1

        def patched(key, **values):
            return '' if key == 'lines.piece_guidance' else real_msg(key, **values)
        with mock.patch.object(prep_service, 'msg', patched) if not guidance else _null():
            self.assertTrue(self.tick())
            began = time.time()
            self.service.run_one()
            seconds = round(time.time() - began, 1)
        self.deliver_all()
        self.tick()
        run = self.service.preparations.get(row['id'])
        with self.store.db() as db:
            job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (run['last_run_job_id'],)).fetchone())
            tools = [dict(r) for r in db.execute('SELECT tool,status,detail FROM tool_events WHERE job_id=? ORDER BY id', (job['id'],))]
        pieces = [p for p in self.work_rows(brief['line_id']) if p.get('origin') == prep.ORIGIN_LINE_PIECE]
        saw_guidance = any(real_msg('lines.piece_guidance', line=brief['line_id'], minutes=5) in json.dumps(b, ensure_ascii=False)
                           for b in self.model_calls)
        piece = pieces[0] if pieces else None
        lead = (piece['due_at'] - started) if piece else None
        return {
            'scenario': scenario['id'], 'guidance': guidance, 'saw_guidance': saw_guidance,
            'status': job['status'], 'seconds': seconds, 'model_calls': len(self.model_calls),
            'tools': [t['tool'] for t in tools], 'world_reads': len(world.plans),
            'piece_scheduled': bool(pieces), 'pieces': len(pieces),
            'piece_lead_minutes': round(lead / 60) if lead is not None else None,
            'piece_goal': piece['goal_text'] if piece else None,
            'schedule_calls': [schedule_event(t) for t in tools if t['tool'] == 'schedule_preparation'],
            'reply': (job.get('response') or job.get('error') or '')[:1200],
        }


def schedule_event(event):
    """A ``schedule_preparation`` event as its status and, when refused, its refusal code."""
    try:
        detail = json.loads(event['detail'] or '{}')
    except ValueError:
        detail = {}
    text = json.dumps(detail, ensure_ascii=False)
    code = detail.get('code') or detail.get('error_code') or next(
        (c for c in prep.REFUSALS if f'"{c}"' in text), None)
    return {'status': event['status'], 'code': code}


class _null:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def score(result, scenario):
    """Deterministic checks of the behavior the design asks for."""
    checks = {'expected_piece': result['piece_scheduled'] == scenario['expect_piece']}
    if scenario['expect_piece'] and result['piece_scheduled']:
        hint = scenario['hint'] / 60
        checks['due_near_hint'] = abs(result['piece_lead_minutes'] - hint) <= max(60, hint / 2)
        checks['handoff_written'] = len(result['piece_goal'] or '') >= 40
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--epochs', type=int, default=2)
    parser.add_argument('--scenarios', nargs='*')
    parser.add_argument('--arms', nargs='*', default=['without', 'with'])
    parser.add_argument('--model')
    parser.add_argument('--provider', default='openai')
    parser.add_argument('--endpoint', default='https://api.openai.com/v1')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    Run.MODEL, Run.KEY = live_model(args)
    if not Run.KEY:
        sys.exit('no model key')
    args.out.mkdir(parents=True, exist_ok=True)
    chosen = [s for s in SCENARIOS if not args.scenarios or s['id'] in args.scenarios]
    results = []
    for epoch in range(args.epochs):
        for scenario in chosen:
            for arm in args.arms:
                case = Run()
                case.setUp()
                try:
                    result = case.measure(scenario, arm == 'with')
                except Exception as exc:  # noqa: BLE001 - a failed run is a recorded outcome
                    result = {'scenario': scenario['id'], 'guidance': arm == 'with', 'error': f'{type(exc).__name__}: {exc}'[:400]}
                finally:
                    case.doCleanups()
                result.update(epoch=epoch, checks=score(result, scenario) if 'error' not in result else {})
                results.append(result)
                print(json.dumps({k: result.get(k) for k in ('epoch', 'scenario', 'guidance', 'status', 'piece_scheduled',
                                                             'piece_lead_minutes', 'checks', 'error')}, ensure_ascii=False), flush=True)
    record = {'issue': 938, 'slice': 2, 'model': Run.MODEL, 'epochs': args.epochs,
              'commit': os.popen('git rev-parse HEAD').read().strip(), 'measured_at': datetime.now(SEOUL).isoformat(timespec='seconds'),
              'results': results}
    (args.out / 'results.json').write_text(json.dumps(record, ensure_ascii=False, indent=1))
    return record


if __name__ == '__main__':
    unittest.TestCase.maxDiff = None
    main()
