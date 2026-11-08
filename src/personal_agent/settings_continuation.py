"""MCP setup and Settings Work continuation extracted from AgentService (#21).

AgentService remains the composition root and owns all mutable service state.
The collaborator uses the service on every call, including calls between moved
methods, preserving service forwarding entry points and patch targets. Setup,
authority, approvals, budgets, recovery and database transactions are unchanged.
SettingsOrchestrator retains draft creation and confirmation; this collaborator
coordinates their service-owned setup and Work continuation boundaries.
"""

import json
import secrets
import time
from pathlib import Path

from . import owner_mcp, trust_record
from . import work_status as ws
from .agent_runtime import OWNER_LOCAL_ACTIONS, WorkLedger
from .messages import msg
from .settings_orchestrator import SettingsError
from .quickstart_service import CONTINUE_REASONS, CONTINUE_RECEIPT_REASON, awaiting_owner_rows


class SettingsContinuation:
    """MCP setup and prerequisite continuation over existing service state."""

    def __init__(self, service):
        self.service = service

    def mcp_setup_value(self, value):
        service=self.service
        from .mcp_setup import setup_value
        value=setup_value(value,service._redact_known_secrets,service.intranet_declared)
        data=json.loads(value)
        if not service._connector_input_safe(data):raise ValueError(msg('quickstart_service.credentials_secret_input_only'))
        definition=service.mcp_setup_definition(data)
        if definition is not None and definition!={'type':'http','url':data['url']}:
            raise ValueError(msg('quickstart_service.server_name_conflict'))
        target=trust_record.connection(data['engine'],'owner-mcp',data['name'])
        record=trust_record.hand(service.store,target)
        if record['state']=='forgotten':raise ValueError(msg('trust_record.restore_confirmation'))
        if record['revision'] and (record['money']!=data['money'] or
                any(record['operations'].get(op) not in (None,'read') for op in data['read_tools'])):
            raise ValueError(msg('quickstart_service.preserve_operation_decisions'))
        return value

    def mcp_setup_definition(self, data):
        service=self.service
        home=getattr(service,'mcp_setup_home',None) or Path.home()
        return owner_mcp.claude_definitions(home,[data['name']]).get(data['name'])

    def mcp_setup_before(self, value):
        service=self.service
        data=json.loads(value)
        target=trust_record.connection(data['engine'],'owner-mcp',data['name'])
        record=trust_record.hand(service.store,target)
        return service.connector_input_digest('connection-setup-authority',{
            'connections':service.store.config(service.AI_CONNECTIONS_KEY,{}),
            'servers':service.store.config(owner_mcp.CONFIRMED_KEY,{}),
            'trust_revision':record['revision'],'trust_epoch':record['epoch'],'state':record['state']})

    def mcp_setup_guard(self, draft):
        service=self.service
        job=service.store.job(draft.get('work_id')) if draft.get('work_id') else None
        if (not job or service.settings_owner(job)!=draft['owner'] or job['channel']!=draft['channel']
                or service.work_stopped(job['id']) or draft.get('recovery_of') and draft.get('continuation') and ws.is_succeeded(job['status'])):
            raise ValueError(msg('quickstart_service.check_connection_work_owner'))
        # #213: a Work from a conversation surface is held to that surface's current pairing.
        surface=service.surface_claiming(job)
        if surface is not None and not surface.owns(job):
            raise ValueError(msg('quickstart_service.connection_owner_changed'))
        data=json.loads(draft['after'])
        target=trust_record.connection(data['engine'],'owner-mcp',data['name'])
        return {'generation':service._owner_generation(service.issuer_owner_id(job)),
                'trust_epoch':trust_record.hand(service.store,target)['epoch']}

    def mcp_setup_client(self, draft, interrupted):
        service=self.service
        factory=getattr(service,'mcp_setup_factory',None)
        if factory is not None:return factory(draft,interrupted)
        from .mcp_setup import RemoteMcpSetup
        binary=service.subscription_engines.finder('claude')
        if not binary:raise ValueError(msg('quickstart_service.claude_missing'))
        return RemoteMcpSetup(binary,getattr(service,'mcp_setup_home',None) or Path.home(),
                              interrupted=interrupted,redact=service._redact_known_secrets,declared=service.intranet_declared)

    def mcp_setup_call(self, draft, client, phase):
        """Record the bounded CLI attempt, never credentials or raw auth output.

        An attempted endpoint is not a claim that the remote accepted it. The
        CLI owns any OAuth destination; those redirects remain unobserved here.
        """
        service=self.service
        data=json.loads(draft['after'])
        detail={'draft_id':draft['id'],'engine':data['engine'],'phase':phase,
                'destination':data['url'] if phase!='register' else 'local CLI profile',
                'scope':'connection-setup','remote_receipt':'unknown'}
        def record(status, state):
            with service.store.db() as db:
                db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                    (draft['work_id'],'connection_setup',status,json.dumps({**detail,'state':state}),time.time()))
        record('running','requested')
        try:
            result=getattr(client,phase)(data['engine'],data['name'],data['url'])
        except Exception:
            record('failed','unknown')
            from .mcp_setup import SetupFailure
            raise SetupFailure(phase,'unknown') from None
        from .mcp_setup import STATES
        state=result.get('state') if isinstance(result,dict) else None
        state=state if state in STATES|{'registration-returned','already-present','login-returned'} else 'unknown'
        record('succeeded' if state in ('connected','registration-returned','already-present','login-returned') else 'failed',state)
        return {**(result if isinstance(result,dict) else {}),'state':state}

    def apply_mcp_setup(self, draft):
        """Exact owner draft → official setup → atomic authority/receipt settlement."""
        service=self.service
        from .mcp_setup import SetupFailure
        data=json.loads(draft['after'])
        expected={'type':'http','url':data['url']}
        def interrupted():
            current=service.settings_orchestrator._drafts().get(draft['id']) or {}
            if (current.get('state')!='applying' or current.get('digest')!=draft['digest']
                    or service.settings_orchestrator._digest(current)!=draft['digest']):return 'setup-no-longer-current'
            try:
                if service.mcp_setup_guard(draft)!=draft.get('setup_guard'):return 'owner-changed'
            except ValueError:return 'work-stopped'
            if service.mcp_setup_before(draft['after'])!=draft['before']:return 'authority-changed'
            return None
        if interrupted():raise ValueError(msg('quickstart_service.setup_authority_changed'))
        client=service.mcp_setup_client(draft,interrupted)
        if service.mcp_setup_definition(data) is None and draft.get('observe_only'):
            raise SetupFailure('observe','unknown')
        if service.mcp_setup_definition(data) is None:
            registered=service.mcp_setup_call(draft,client,'register')
            if registered.get('state') not in ('registration-returned','already-present'):
                raise SetupFailure('register',registered.get('state'))
        if interrupted() or service.mcp_setup_definition(data)!=expected:
            raise ValueError(msg('quickstart_service.server_configuration_changed'))
        observed=service.mcp_setup_call(draft,client,'observe')
        if observed.get('state')=='needs-auth' and not draft.get('observe_only'):
            if interrupted():raise ValueError(msg('quickstart_service.login_authority_changed'))
            login=service.mcp_setup_call(draft,client,'login')
            if login.get('state')!='login-returned':
                raise SetupFailure('login',login.get('state'))
            observed=service.mcp_setup_call(draft,client,'observe')
        if (observed.get('state')!='connected' or not isinstance(observed.get('tools'),list)
                or not set(data['required_tools'])<=set(observed['tools'])):
            raise SetupFailure('observe',observed.get('state'))
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if interrupted() or service.mcp_setup_definition(data)!=expected:
                raise ValueError(msg('quickstart_service.connection_authority_changed'))
            target=trust_record.connection(data['engine'],'owner-mcp',data['name'])
            before=trust_record.hand(service.store,target)
            record=trust_record.introduce_connection(service.store,target,data['read_tools'],data['money'],
                        expected_revision=before['revision'],transaction=db)
            names=owner_mcp.confirmed(service.store,data['engine'])
            if data['name'] not in names:names.append(data['name'])
            owner_mcp.set_confirmed(service.store,data['engine'],names,transaction=db)
            if not service.ai_connections_enabled():
                db.execute('INSERT INTO config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           (service.AI_CONNECTIONS_KEY,json.dumps({'enabled':True,'changed_at':time.time(),'revision':secrets.token_hex(16)})))
            rows=json.loads(db.execute("SELECT value FROM config WHERE key='settings_change_drafts'").fetchone()['value'])
            row=rows[draft['id']]
            row.update(state='applied',setup_receipt={'state':'connected','tools':list(observed['tools']),
                       'observed_at':time.time(),'trust_revision':record['revision'],
                       'authentication':'unknown','account':'unknown','granted_scopes':'unknown','input_schemas':'unknown'})
            db.execute("UPDATE config SET value=? WHERE key='settings_change_drafts'",(json.dumps(rows),))
        return {'response':msg('quickstart_service.mcp_setup_receipt', name=data['name'], result=msg('quickstart_service.mcp_ready_resume') if draft.get('continuation') else msg('quickstart_service.mcp_settings_applied'))}

    def mcp_setup_recoverable(self, draft):
        """Retry known failures, or observe unknown setup without repeating mutations."""
        service=self.service
        try:
            job=service.store.job(draft.get('work_id'))
            return (job is not None and (not draft.get('continuation') or ws.is_resumable_settings(job['status']))
                    and (draft.get('state')=='unknown' or draft.get('state')=='failed' and draft.get('setup_failure',{}).get('retryable') is True)
                    and (draft.get('category'),draft.get('setting'))==('ai_connections','connect')
                    and 0<=service.settings_orchestrator.now()-float(draft.get('settled_at') or 0)<=service.settings_orchestrator.RENEW_GRACE_SECONDS
                    and not draft.get('trust_revoked') and draft.get('digest')==service.settings_orchestrator._digest(draft)
                    and service.mcp_setup_guard(draft)==draft.get('setup_guard')
                    and service.mcp_setup_before(draft['after'])==draft['before']
                    and service.may_continue(draft['work_id'],'resume')[0]
                    and service.mcp_setup_attempts(draft['work_id'])<3)
        except (ValueError,KeyError,TypeError):
            return False

    def mcp_setup_attempts(self, work_id):
        """Durable admissions across replacement drafts, before any CLI dispatch."""
        service=self.service
        return sum(1 for row in service.settings_orchestrator._drafts().values()
                   if isinstance(row,dict) and row.get('work_id')==work_id
                   and (row.get('category'),row.get('setting'))==('ai_connections','connect')
                   and 'applying_at' in row)

    def mcp_setup_ready(self, draft):
        """The observed setup and current local authority must still cover the request."""
        service=self.service
        try:
            data=json.loads(draft['after'])
            if not service.ai_connections_enabled() or service.mcp_setup_definition(data)!={'type':'http','url':data['url']}:return False
            if data['name'] not in owner_mcp.confirmed(service.store,data['engine']):return False
            record=trust_record.hand(service.store,trust_record.connection(data['engine'],'owner-mcp',data['name']))
            if record['state']!='active' or record['money']!=data['money']:return False
            guard=draft.get('setup_guard') or {}
            if 'trust_epoch' in guard and guard['trust_epoch']!=record['epoch']:return False
            if not all(record['operations'].get(op)=='read' for op in data['read_tools']):return False
            source=draft
            if draft.get('setup_from'):
                source=service.mcp_covering_setup(draft,draft['setup_from'])
                if source is None:return False
            receipt=source.get('setup_receipt') or {}
            return (receipt.get('state')=='connected' and receipt.get('trust_revision')==record['revision']
                    and set(data['required_tools'])<=set(receipt.get('tools',[])))
        except (ValueError,TypeError,KeyError):return False

    def mcp_covering_setup(self, draft, source_id=None):
        """Reuse a current applied setup, never another Work's action authority.

        A reference keeps the original observation attributable. It neither
        refreshes its timestamp nor copies credentials, grants or tool effects.
        """
        service=self.service
        try:
            requested=json.loads(draft['after'])
            target=trust_record.connection(requested['engine'],'owner-mcp',requested['name'])
            guard=draft.get('setup_guard') or {}
            if 'trust_epoch' not in guard or guard['trust_epoch']!=trust_record.hand(service.store,target)['epoch']:return None
            for source in service.settings_orchestrator._drafts().values():
                if (source.get('id')==draft['id'] or source_id is not None and source.get('id')!=source_id
                        or source.get('state')!='applied' or source.get('trust_revoked') or source.get('setup_from')
                        or (source.get('category'),source.get('setting'))!=('ai_connections','connect')
                        or source.get('owner')!=draft['owner'] or source.get('channel')!=draft['channel']
                        or source.get('setup_guard')!=draft.get('setup_guard')
                        or source.get('digest')!=service.settings_orchestrator._digest(source)):
                    continue
                offered=json.loads(source['after'])
                if (any(requested[key]!=offered[key] for key in ('engine','name','url','source','publisher','licence','money'))
                        or not set(requested['read_tools'])<=set(offered['read_tools'])
                        or not set(requested['required_tools'])<=set((source.get('setup_receipt') or {}).get('tools',[]))):
                    continue
                if service.mcp_setup_ready(source):return source
        except (ValueError,TypeError,KeyError):pass
        return None

    def satisfy_admitted_mcp_setup(self, draft):
        """At the serialized apply boundary, reuse setup completed while queued.

        This runs before any CLI action for this draft. A recovered unknown
        apply cannot enter here through confirmation, and is never retried.
        """
        service=self.service
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            found=db.execute("SELECT value FROM config WHERE key='settings_change_drafts'").fetchone()
            rows=json.loads(found['value']) if found else {}
            current=rows.get(draft['id']) or {}
            if (current.get('state')!='applying' or current.get('trust_revoked')
                    or current.get('digest')!=draft['digest']
                    or service.settings_orchestrator._digest(current)!=draft['digest']):return False
            if service.mcp_setup_guard(current)!=current.get('setup_guard'):return False
            source=service.mcp_covering_setup(current)
            if source is None:return False
            current.update(state='satisfied',setup_from=source['id'])
            db.execute("UPDATE config SET value=? WHERE key='settings_change_drafts'",(json.dumps(rows),))
        service.settings_orchestrator._audit(current,'satisfied')
        return True

    def settings_continuation(self, draft):
        """Bind a prerequisite to its original Work, never to a confirmation reply.

        Unknown read reviews have observed denied-call evidence. A content-skill
        prerequisite is an explicit decision-model choice on the exact owner
        draft; a setup-only change leaves continue_work false.
        """
        service=self.service
        job=service.store.job(draft.get('work_id')) if draft.get('work_id') else None
        if not job or service.settings_owner(job)!=draft.get('owner') or job['channel']!=draft.get('channel'):return None
        if draft.get('continue_work') is True and (draft.get('category'),draft.get('setting'))==('api_connections','connect'):
            return {'kind':'api-setting','generation':service._owner_generation(service.issuer_owner_id(job)),
                    'prerequisite':'settings:'+draft['id']}
        if draft.get('continue_work') is True and (draft.get('category'),draft.get('setting'))==('ai_connections','connect'):
            return {'kind':'mcp-setting','generation':service._owner_generation(service.issuer_owner_id(job)),
                    'prerequisite':'settings:'+draft['id']}
        if draft.get('continue_work') is True and draft.get('category')=='skills':
            if draft.get('setting')=='add' or draft.get('setting')=='enabled' and draft.get('after')=='on':
                return {'kind':'skill-setting','generation':service._owner_generation(service.issuer_owner_id(job)),
                        'prerequisite':'settings:'+draft['id'], 'skill':draft.get('required_skill')}
        if draft.get('category')!='ai_connections' or draft.get('setting')!='review':return None
        parts=str(draft.get('after') or '').split('|')
        if len(parts)!=5 or parts[-1]!='read':return None
        with service.store.db() as db:
            rows=db.execute("SELECT id,detail FROM tool_events WHERE job_id=? AND tool='connector_permission' AND status='denied' ORDER BY id DESC",
                            (job['id'],)).fetchall()
        for event in rows:
            try:evidence=json.loads(event['detail'])['evidence']
            except (TypeError,ValueError,KeyError):continue
            if [evidence.get(key) for key in ('engine','kind','identity','operation')]==parts[:4]:
                before=str(draft.get('before') or '').split(':')
                return {'permission_event':event['id'],'generation':service._owner_generation(service.issuer_owner_id(job)),
                        'trust_epoch':before[2] if len(before)==3 else ''}
        return None

    def continuation_reasons(self, work_id, purpose, *, since=None, owner_waits=False, unaccounted=False):
        """Every reason this Work may not continue for ``purpose``, strongest first (#618 E2).

        The one continuation rule.  Re-delegation, resume, retry, parking, steer and budget-continue
        all ask it, so they agree, and each tells the owner and the model the same reason.  Adopted
        from RFC 9110 §9.2.2 (repeat only what is known not applied) and the OpenAI Agents SDK's
        human-in-the-loop rule ("retry only after authoritative reconciliation establishes that no
        earlier attempt performed the action"), on the per-call classes of ``record_effect``:

        - ``unknown``: a call of this Work has no definite answer (an unknown effect, a dispatch
          claim never settled, a call cut off before its terminal record, an unreadable record);
        - ``unaccounted``: an action Still could not observe ran - a trusted-local CLI's own host
          actions (``unaccounted``), or, when resuming, a CLI turn whose actions were not accounted;
        - ``owner``: the attempt waits for the owner's own decision or connection - a recorded
          prerequisite (``owner_waits``: an open approval, an observed sign-in page; or a recorded
          draft or proposal, or a missing connection a call named);
        - ``effect``: a call changed something outside the owner (``changed``).  A ``resume``
          carries the Work's own progress forward, so a change Still's own replay key already
          guards is accounted, not repeated (``api_effect_replayed``, the connector input-digest
          claim): an API dispatch observed for this Work, a connector change its permission claimed.
        - ``limit`` (``resume`` only, #627): the request already queued its one continuation count
          (``MAX_CONTINUATIONS``) - every owner-act continuation counts, a renewed allowance included.

        ``none`` and ``draft`` calls never block, nor does an owner-local write (``OWNER_LOCAL_ACTIONS``).
        Unknown and observed external changes are read over the whole Work (and its request chain);
        ``since`` (an event id) limits the calls and owner waits to one attempt.  The reasons come in
        ``CONTINUE_REASONS`` order and read as ``CONTINUE_TEXT``; an empty tuple means the Work may continue.
        """
        service=self.service
        from .agent_runtime import (EFFECT_CHANGED, EFFECT_FREE_READS, EFFECT_LEDGER_TOOLS, EFFECT_UNKNOWN, INTERNAL_STATE_ACTIONS,
                                    MAX_CONTINUATIONS, call_effects, continuation_count, keyed_change)
        if purpose not in service.CONTINUE_PURPOSES:raise ValueError(purpose)
        reasons=[]
        def add(reason):
            if reason not in reasons:reasons.append(reason)
        carry=purpose=='resume'
        observed,claims=service.connector_effect_progress(work_id)
        observed_api=set()
        with service.store.db() as db:
            rows=db.execute('SELECT id,tool,status,detail FROM tool_events WHERE job_id=? ORDER BY id',(work_id,)).fetchall()
            if carry and continuation_count(db,work_id)>=MAX_CONTINUATIONS:add('limit')
            if carry:
                # Retry/input continuation dispatches are stored on the canonical original Work;
                # only this Work's host-attributed observed dispatches are accounted.
                try:root=WorkLedger.request_chain(db,work_id)[-1]['id']
                except ValueError:root=None;add('unknown')
                for row in db.execute("SELECT id,detail FROM tool_events WHERE job_id=? AND tool='api_dispatch' AND status='observed'",
                                      (root,)) if root else ():
                    try:evidence=json.loads(row['detail']).get('evidence')
                    except (TypeError,ValueError,AttributeError):evidence=None
                    if not isinstance(evidence,dict):add('unknown');continue
                    if evidence.get('work_id')==work_id:observed_api.add(row['id'])
        if claims:add('unknown')
        calls,scoped,engine_running=[],[],False
        for row in rows:
            try:detail=json.loads(row['detail'] or '{}')
            except (TypeError,ValueError):detail=None
            if not isinstance(detail,dict):
                add('unknown');continue
            tool=row['tool']
            if tool in EFFECT_LEDGER_TOOLS|service.CONTINUATION_EVENTS:continue
            if tool in service.LOCAL_NON_TOOL_EVENTS or tool=='subscription_engine':
                # Still's own records are no calls; an unknown outcome one of them carries still counts.
                if service._unknown_effect(detail):add('unknown')
                if tool=='subscription_engine' and carry:
                    # A CLI turn's own actions are accounted only when its record says so.
                    if row['status']=='running':
                        if engine_running:add('unaccounted')
                        engine_running=True
                    else:
                        if detail.get('continuation_read_only') is not True and detail.get('continuation_accounted') is not True:
                            add('unaccounted')
                        engine_running=False
                continue
            if row['status'] not in ('running','succeeded','failed'):
                # A record with no call outcome (a preflight's ``unavailable``): only an unknown it carries counts.
                if service._unknown_effect(detail):add('unknown')
                continue
            calls.append((tool,row['status'],detail))
            if since is None or row['id']>since:scoped.append((tool,row['status'],detail))
        if engine_running or unaccounted:add('unaccounted')
        def cut_off(action,status,detail):
            # A call (paired by its id) with no terminal record got no definite answer, unless it only
            # reads or stays with the owner; a navigation or input step may have acted on the page.
            return (status=='running' and bool(detail.get('call_id'))
                    and action not in (EFFECT_FREE_READS-{'browser_open'})|OWNER_LOCAL_ACTIONS|INTERNAL_STATE_ACTIONS)
        for action,effect,status,detail in call_effects(calls,with_detail=True):
            # E1: unknown is that call's own.
            if effect==EFFECT_UNKNOWN or cut_off(action,status,detail):add('unknown')
        # #170: a recorded draft or proposal; #366: a connection only the owner can make.
        if (owner_waits or (purpose in ('redelegate','steer') and awaiting_owner_rows(scoped))
                or (purpose=='redelegate' and service.missing_connection_rows(scoped))):
            add('owner')
        if observed and not carry:add('effect')
        for action,effect,status,detail in call_effects(scoped,with_detail=True):
            if (effect==EFFECT_CHANGED and not cut_off(action,status,detail)
                    and not (carry and keyed_change(action,detail,status,observed_api))):add('effect')
        return tuple(sorted(reasons,key=CONTINUE_REASONS.index))

    def may_continue(self, work_id, purpose, **kwargs):
        """``(ok, reason)`` of the one continuation rule (#618 E2): ``reason`` is the strongest, or None."""
        service=self.service
        reasons=service.continuation_reasons(work_id,purpose,**kwargs)
        return (not reasons,reasons[0] if reasons else None)

    def settings_review_ready(self, draft):
        """Current readiness for the exact prerequisite, never just a setter receipt."""
        service=self.service
        if (draft.get('continuation') or {}).get('kind')=='api-setting':
            from .api_input import ready
            return ready(service, draft)
        if (draft.get('continuation') or {}).get('kind')=='mcp-setting':
            return service.mcp_setup_ready(draft)
        if (draft.get('continuation') or {}).get('kind')=='skill-setting':
            return service.settings_skill_ready(draft)
        parts=str(draft.get('after') or '').split('|')
        if len(parts)!=5 or parts[-1]!='read':return False
        engine,kind,identity,operation,_cls=parts
        try:target=trust_record.connection(engine,kind,identity)
        except ValueError:return False
        binding=draft.get('continuation')
        if binding and trust_record.hand(service.store,target)['epoch']!=binding.get('trust_epoch',''):return False
        return (service.ai_connections_enabled()
                and (kind!='owner-mcp' or identity in owner_mcp.confirmed(service.store,engine))
                and trust_record.is_read(service.store,target,operation))

    def settings_skill_ready(self, draft):
        """A pinned skill must be available in the resumed catalogue and intact.

        Inspect content without loading it into any model; only skill_load may
        do that. The original running worker keeps its old immutable binding.
        """
        service=self.service
        from .skills import parse_source
        try:
            library=service.skill_library()
            entries=library.catalogue()
            if not entries:return False
            if draft.get('setting')=='add':
                source=parse_source(draft['after'])
                package_ids={row['id'] for row in library.installed()
                             if all(row.get('source',{}).get(key)==source.get(key) for key in ('repo','revision','path'))}
                entries=[entry for entry in entries if entry['package'] in package_ids]
                if not entries:return False
            elif draft.get('setting')=='enabled' and draft.get('after')=='on':
                ref=(draft.get('continuation') or {}).get('skill')
                entries=[entry for entry in entries if entry['skill']+'@'+entry['digest']==ref]
                if not entries:return False
            else:return False
            for entry in entries:
                if not library.current(entry,verify_content=True):return False
            return True
        except (OSError,ValueError,KeyError,TypeError):
            return False

    def settings_prerequisites_observed(self, job, drafts):
        """The still-running worker may have used the new read and finished already."""
        service=self.service
        if not ws.is_succeeded(job['status']):return False
        with service.store.db() as db:
            rows=db.execute("SELECT id,detail FROM tool_events WHERE job_id=? AND tool='connector_read' AND status='succeeded'",
                            (job['id'],)).fetchall()
        completed=set()
        for event in rows:
            try:detail=json.loads(event['detail']);evidence=detail['evidence']
            except (ValueError,TypeError,KeyError):continue
            if detail.get('effect')!='none':continue
            for draft in drafts:
                if (draft['continuation'].get('kind') not in ('skill-setting','mcp-setting','api-setting')
                        and event['id']>draft['continuation']['permission_event']
                        and [evidence.get(k) for k in ('engine','kind','identity','operation')]==draft['after'].split('|')[:4]):
                    completed.add(draft['id'])
        return all(row['id'] in completed for row in drafts)

    def resume_settings_work(self):
        """Reconcile prerequisite waits using existing drafts, events and jobs.

        The receipt and enqueue share a transaction. Repeated callbacks/processes
        cannot replay a consumed wait. Current permission is checked again by the
        tool on the resumed run. Eight continuations cap the cumulative execution
        while each host run inherits the original Work's durable counters and
        remaining active time through WorkBudget.
        """
        service=self.service
        orchestrator=service.settings_orchestrator
        with orchestrator._confirming:
            from .api_input import reconcile, published as api_published
            reconcile(service)
            groups={}
            for draft in orchestrator._drafts().values():
                if isinstance(draft,dict) and draft.get('continuation') and draft.get('state')!='renewed':
                    groups.setdefault(draft.get('work_id'),[]).append(draft)
            for work_id,drafts in groups.items():
                job=service.store.job(work_id) if work_id else None
                if not job or ws.is_active(job['status']):continue
                with service.store.db() as db:
                    prior=db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool IN ('settings_continuation','action_continuation')",(work_id,)).fetchall()
                receipts=[json.loads(row['detail']) for row in prior]
                used={ref for row in receipts for ref in row.get('draft_ids',[])}
                drafts=[row for row in drafts if row['id'] not in used]
                if not drafts:continue
                valid=all(row.get('digest')==orchestrator._digest(row)
                          and row.get('owner')==service.settings_owner(job) and row.get('channel')==job['channel']
                          and row['continuation'].get('generation')==service._owner_generation(service.issuer_owner_id(job))
                          for row in drafts)
                stopped=service.work_stopped(work_id) or not ws.is_resumable_settings_settlement(job['status'])
                surface=service.surface_claiming(job)
                if surface is not None:
                    stopped=stopped or not surface.paired_owner(job['chat_id'])
                # A second Work waiting on the same operation uses the first
                # owner's persisted classification; it never re-applies a setter.
                changed_during_settlement=False
                for row in drafts:
                    if valid and not stopped and row['state']=='awaiting-confirmation':
                        if row['continuation'].get('kind') in ('skill-setting','mcp-setting','api-setting'):
                            if orchestrator.expired(row):
                                row.update(orchestrator._settle(row['id'],'expired','expired'))
                            elif row['continuation'].get('kind')=='mcp-setting':
                                data=json.loads(row['after'])
                                target=trust_record.connection(data['engine'],'owner-mcp',data['name'])
                                guard=row.get('setup_guard') or {}
                                if 'trust_epoch' in guard and guard['trust_epoch']!=trust_record.hand(service.store,target)['epoch']:
                                    row.update(orchestrator._settle(row['id'],'cancelled','cancelled',trust_revoked=True))
                                    continue
                                source=service.mcp_covering_setup(row)
                                if source is not None:
                                    try:
                                        row.update(orchestrator._settle(row['id'],'satisfied','satisfied',
                                                   expected={'state':'awaiting-confirmation','digest':row['digest']},
                                                   setup_from=source['id']))
                                    except SettingsError:
                                        # A concurrent cancellation/forget/confirmation wins.
                                        changed_during_settlement=True
                                        break
                            continue  # a shared receipt applies no setting or setup action
                        parts=row['after'].split('|')
                        target=trust_record.connection(*parts[:3])
                        if trust_record.hand(service.store,target)['epoch']!=row['continuation'].get('trust_epoch',''):
                            row.update(orchestrator._settle(row['id'],'cancelled','cancelled',trust_revoked=True))
                        elif orchestrator.expired(row):
                            row.update(orchestrator._settle(row['id'],'expired','expired'))
                        elif service.settings_review_ready(row):
                            row.update(orchestrator._settle(row['id'],'satisfied','satisfied'))
                if changed_during_settlement:continue
                waiting=any(row['state'] in ('awaiting-confirmation','awaiting-input','applying')
                            or row['continuation'].get('kind')=='api-setting'
                            and row['state'] in ('unknown','applied') and api_published(service,row)
                            and not service.settings_review_ready(row) for row in drafts)
                # #627: the continuation count withholds the resume itself (below), never the wait.
                safe=not [blocker for blocker in service.continuation_reasons(work_id,'resume') if blocker!='limit']
                if valid and not stopped and waiting and safe:
                    with service.store.db() as db:
                        db.execute("UPDATE jobs SET status='awaiting_connection' WHERE id=? AND status=?",(work_id,job['status']))
                    continue
                completed=service.settings_prerequisites_observed(job,drafts)
                ready=(valid and not stopped and safe and not waiting and not completed
                       and all(row['state'] in ('applied','satisfied') and service.settings_review_ready(row) for row in drafts))
                reason=('work-stopped' if stopped else 'original-completed' if completed else 'effect-or-unobserved-action' if not safe else
                        'prerequisite-ready' if ready and any(row['continuation'].get('kind') in ('skill-setting','mcp-setting','api-setting') for row in drafts) else
                        'read-review-applied' if ready else 'review-not-current')
                with service.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    found=db.execute("SELECT value FROM config WHERE key='settings_change_drafts'").fetchone()
                    latest=json.loads(found['value']) if found else {}
                    if any((latest.get(row['id']) or {}).get('trust_revoked') for row in drafts):
                        ready,reason=False,'trust-forgotten'
                    if ready and any(not latest.get(row['id']) or
                            latest[row['id']].get('state')!=row['state'] or
                            latest[row['id']].get('digest')!=row['digest'] or
                            latest[row['id']].get('setup_from')!=row.get('setup_from') or
                            orchestrator._digest(latest[row['id']])!=row['digest'] for row in drafts):
                        ready,reason=False,'review-not-current'
                    if ready and not all(service.settings_review_ready(row) for row in drafts):
                        ready,reason=False,'review-not-current'
                    if ready:
                        for row in drafts:
                            if row['continuation'].get('kind')=='mcp-setting':
                                try:
                                    current_guard=service.mcp_setup_guard(row)
                                    expected_guard=row.get('setup_guard') or {}
                                    if 'trust_epoch' not in expected_guard and not row.get('setup_from'):
                                        current_guard.pop('trust_epoch',None)  # pre-sharing applied receipt
                                    if current_guard!=expected_guard:
                                        ready,reason=False,'owner-or-authority-changed'
                                except ValueError:
                                    ready,reason=False,'work-stopped'
                    current=db.execute("SELECT detail FROM tool_events WHERE job_id=? AND tool IN ('settings_continuation','action_continuation')",(work_id,)).fetchall()
                    receipts=[json.loads(row['detail']) for row in current]
                    consumed=[row['id'] for row in drafts]
                    permissions=[row['continuation'].get('permission_event',row['continuation'].get('prerequisite')) for row in drafts]
                    if any(set(consumed)&set(row.get('draft_ids',[])) for row in receipts):continue
                    # A withheld expiry receipt settles its draft, but does not
                    # consume execution. The owner's explicit renewal may resume;
                    # an actually queued continuation still consumes the wait.
                    if any(row.get('resumed') is True and set(permissions)&set(row.get('permission_events',[]))
                           for row in receipts):ready,reason=False,'wait-already-consumed'
                    # #627: the one continuation count, read through the one continuation rule.
                    if ready and 'limit' in service.continuation_reasons(work_id,'resume'):
                        ready,reason=False,CONTINUE_RECEIPT_REASON['limit']
                    refusal=service.preparations.run_refusal(db,work_id,service.preparations.clock())
                    if refusal and not completed:
                        ready,reason=False,refusal
                        service.preparations.refuse_run(db,work_id,service.preparations.clock(),refusal)
                    if ready:
                        changed=db.execute("UPDATE jobs SET status='queued',error=NULL,delivery='none' WHERE id=? AND status=?",
                                           (work_id,job['status'])).rowcount
                        if not changed:continue
                        if reason=='prerequisite-ready':
                            # #1445: the owner completed the setup this Work proposed; its remaining work
                            # starts with a fresh allowance, not the turns spent finding it (#627: bounded by
                            # the one continuation count this resume is counted in).
                            from .agent_runtime import renew_for_owner_prerequisite
                            renew_for_owner_prerequisite(db,work_id)
                    elif job['status']=='awaiting_connection':
                        db.execute("UPDATE jobs SET status='partial',error=? WHERE id=? AND status='awaiting_connection'",
                                   (msg('quickstart_service.settings_resume_failed', reason=reason),work_id))
                    detail={'draft_ids':consumed,'permission_events':permissions,'resumed':ready,'reason':reason}
                    db.execute('INSERT INTO tool_events(job_id,tool,status,detail,created) VALUES (?,?,?,?,?)',
                               (work_id,'settings_continuation','queued' if ready else 'withheld',json.dumps(detail),time.time()))

