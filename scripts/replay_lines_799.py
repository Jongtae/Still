#!/usr/bin/env python3
"""Replay L1 on a temporary SQLite backup, printing counts only.

Reads the owner's conversation, line/proposal history and configured decision route.
Calls the owner's configured decision model once per owner-local night through the
same ConversationJudgments.owner_model_dream path as the nightly look back. Owner
turns go to that configured AI. Never applies actions or prints owner text.
Mutable proposal rows changed after a historical night are omitted, because the
store has no version history for them; their later state is not backdated.

Reuse: sqlite3.Connection.backup (Python standard library) provides a consistent
snapshot; AgentService owns model construction and routing. No separate model client.
"""
import argparse
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, time, timedelta
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time as clock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from personal_agent import entrusted_lines as el
from personal_agent import owner_model as om
from personal_agent import preparations as prep
from personal_agent.current_context import resolve_local_timezone
from personal_agent.quickstart_service import AgentService
from personal_agent.quickstart_store import QuickStore


def snapshot(source_path, root):
    """Open only the real SQLite file read-only; all service writes stay in the copy."""
    private=root/'private'
    private.mkdir(parents=True,mode=0o700)
    with sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True) as source:
        with sqlite3.connect(private/'quickstart.db') as target:
            source.backup(target)
    # The existing decision route reads credentials from this installation-local file.
    secrets=source_path.parent/'connections.json'
    if secrets.is_file():
        shutil.copyfile(secrets,private/'connections.json')
        (private/'connections.json').chmod(0o600)
    return QuickStore(root)


def as_of(store, night):
    """Remove future records from the disposable copy; restore the latest surviving brief."""
    with store.db() as db:
        for table,column in (('jobs','created'),('messages','created'),('tool_events','created'),
                             ('preparations','created_at'),('procedure_versions','created'),('memories','created')):
            db.execute(f'DELETE FROM {table} WHERE {column}>?',(night,))
        db.execute('DELETE FROM preparations WHERE updated_at>?',(night,))
        db.execute("UPDATE procedure_versions SET state='kept' WHERE slot LIKE 'line:%' AND state='current'")
        db.execute("UPDATE procedure_versions SET state='current' WHERE slot LIKE 'line:%' AND state='kept' AND version=("
                   "SELECT MAX(p.version) FROM procedure_versions p WHERE p.slot=procedure_versions.slot AND p.state='kept')")
        # A future notification cannot serve as historical evidence of an announcement.
        db.execute('DELETE FROM telegram_notifications WHERE created>? OR sent_at>?',(night,night))


def replay(source_path, *, nights=9, now=None, judgment_transport=None):
    """Counts only. Tests inject a DecisionEngine, never a new model client."""
    now=clock.time() if now is None else now
    counts=Counter(stand=0,link=0,amend=0)
    refused=Counter()
    with tempfile.TemporaryDirectory(prefix='still-lines-replay-') as tmp:
        root=Path(tmp)
        frozen=snapshot(Path(source_path).resolve(),root/'snapshot')
        zone=prep.zone(resolve_local_timezone(frozen))
        local=datetime.fromtimestamp(now,zone)
        latest=datetime.combine(local.date(),time(3),zone)
        if latest.timestamp()>now:latest-=timedelta(days=1)
        for offset in reversed(range(nights)):
            night=(latest-timedelta(days=offset)).timestamp()
            store=snapshot(frozen.path,root/f'night-{offset}')
            as_of(store,night)
            service=AgentService(store)
            service.preparations.clock=lambda:night
            service.owner_model.clock=lambda:night
            if judgment_transport is not None:service.use_decision_engine(judgment_transport)
            helper=service.preparations_service
            works=service.owner_model.dream_works(night-86400,night,limit=om.DREAM_TURNS)
            eligible=[]
            for work in works:
                try:helper.line_evidence([work['id']],night,minimum_days=1)
                except el.LineRefusal:continue
                eligible.append(work)
            sources={f't{index}':work['id'] for index,work in enumerate(eligible,1)}
            tuples=[(ref,service.scrub_work_text(work['id'],work['message']),
                     service.scrub_work_text(work['id'],work.get('response') or ''),
                     service._owner_local_time(work['created']),om.turn_outcome(work['status'],work['linked_kinds']))
                    for ref,work in zip(sources,eligible)]
            rendered,shown,_days=om.dream_turn_input(tuples)
            turns='\n'.join(rendered)
            sources={ref:work for ref,work in sources.items() if ref in shown}
            line_input,requests=helper.line_judgment_input(night,refs=True,exclude=sources.values())
            judgments=service._owner_model_judgments(None)
            with service.judgments_read([*sources.values(),*requests.values()]):
                data,_decision=judgments.owner_model_dream(turns,'','','',service._owner_local_time(night),lines=line_input)
            if data is None:
                refused['judgment_unavailable']+=1
                continue
            kept,dropped=om.validate_line_actions(data.get('lines'),sources,requests)
            refused.update(item['reason'] for item in dropped)
            mapping={**sources,**requests}
            for item in kept:
                try:helper.line_evidence([mapping[ref] for ref in item['sources']],night,
                                         minimum_days=2 if item['action']=='stand' else 1)
                except el.LineRefusal as exc:refused[exc.code]+=1
                else:counts[item['action']]+=1
            # Linking remains a suggestion, never an inferred writer action.
            suggestions,dropped=om.validate_suggestions(data.get('suggestions'),sources,{})
            refused.update(item['reason'] for item in dropped)
            counts['link']+=sum(bool(item.get('line')) for item in suggestions)
    return {'counts':dict(counts),'refusals':dict(sorted(refused.items()))}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store',type=Path,default=Path.home()/'.local/share/agentos',
                        help='Existing owner store directory (reads private/quickstart.db and decision credentials).')
    parser.add_argument('--nights',type=int,default=9,help='Owner-local nights; one configured decision-model call per night (default 9).')
    args=parser.parse_args(argv)
    if args.nights<1:parser.error('--nights must be positive')
    path=args.store/'private'/'quickstart.db'
    if not path.is_file():parser.error('existing owner store required')
    # Provider/CLI diagnostics can carry input text. Neither stream is passed through.
    try:
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            result=replay(path,nights=args.nights)
    except Exception:  # no exception payload or owner data on either output stream
        print(json.dumps({'counts':{'stand':0,'link':0,'amend':0},'refusals':{'replay_unavailable':1}}))
        return 1
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
