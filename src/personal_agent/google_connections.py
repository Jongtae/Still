"""Google connection lifecycle extracted from AgentService (#21).

AgentService remains the composition root and state owner. Like the Telegram
surface, this collaborator reads and writes through the service on every call;
service forwarding methods preserve caller and patch points. OAuth scopes,
credentials, revocation, approvals and database operations are unchanged.
Shared service constants remain in quickstart_service; this module is loaded
lazily by AgentService.google_connections, as telegram_surface is.
"""

from contextlib import ExitStack

from . import work_status as ws
from .agent_runtime import MEMORY_OWNER
from .calendar import CALENDAR_CONNECTOR_ID, CALENDAR_WRITE_CONNECTOR_ID
from .calendar_oauth import GMAIL_FILTERS_GRANT, GMAIL_MODIFY_GRANT
from .connector_contract import ConnectorContractError
from .connector_revocation import (GoogleConnectionRevoker, RevocationError,
                                   google_revoke_transport, registry_connection)
from .conversation_handoff import ConnectorHandoff, ConversationHandoffError
from .gmail import GMAIL_FILTERS_CONNECTOR_ID, GMAIL_CONNECTOR_ID, GMAIL_MODIFY_CONNECTOR_ID, GmailError
from .google_drive_read import DRIVE_CONNECTOR_ID
from .messages import msg
from .quickstart_service import GMAIL_PROVEN_CALLBACK_REASONS


class GoogleConnections:
    """Google OAuth connections over one AgentService's existing state."""

    def __init__(self, service):
        self.service = service


    def google_connection_rows(self):
        """Every Google connection row Settings shows, each from its own authority."""
        service=self.service
        rows=[]
        for row in service.connector_connections():
            if row.get('state')=='connected':
                current=service.connector_credential_current(row.get('connector_id'))
                if current is False:
                    # Expired or unusable locally: the next request would
                    # refuse it and require a new authorization.
                    row={**row,'state':'reauth_required','detail_state':'connected-credential-expired'}
                elif current is None:
                    row={**row,'state':'unknown','detail_state':'connected-credential-unverified'}
            rows.append(row)
        # #1172: which connections run on the owner's own Google client.
        own={cid for cid,holder in ((GMAIL_CONNECTOR_ID,service.gmail),(CALENDAR_CONNECTOR_ID,service.calendar_oauth),
                                     (CALENDAR_WRITE_CONNECTOR_ID,service.calendar_oauth),(DRIVE_CONNECTOR_ID,service.drive_oauth),
                                     (GMAIL_MODIFY_CONNECTOR_ID,service.gmail_modify_oauth),(GMAIL_FILTERS_CONNECTOR_ID,service.gmail_modify_oauth))
             if service.google_client_id and holder is not None and getattr(holder,'client_id',None)==service.google_client_id}
        return [{**row,'source':'own-client'} if row.get('connector_id') in own else row for row in rows]

    def google_service_rows(self, connections=None, routes=None):
        """#535: Settings' Google card, one row per service with its routes (contract §3: which hand it uses).

        ``levels`` are the existing per-grant rows unchanged, plus their level
        name and, for a disconnected grant a connected level includes, ``covered_by``;
        disconnect still targets one ``connector_id``.  ``ai_routes`` are
        the AI-side connections whose names match the service (``ai_side_routes``).
        """
        service=self.service
        rows=service.google_connection_rows() if connections is None else connections
        routes=service.ai_side_routes() if routes is None else routes
        by_id={row.get('connector_id'):row for row in rows}
        declared=set()
        services=[]
        for spec in service.GOOGLE_SERVICES:
            declared.update(connector_id for connector_id,_level in spec['levels'])
            levels=[{**by_id[connector_id],'level':level} for connector_id,level in spec['levels'] if connector_id in by_id]
            # A disconnected grant that a connected level already includes is covered, not missing.
            for level in levels:
                coverer=next((higher for higher,covered in (spec.get('covers') or {}).items()
                              if level['connector_id'] in covered and (by_id.get(higher) or {}).get('state')=='connected'),None)
                if coverer and level.get('state')=='disconnected':
                    level['covered_by']=coverer
            ai=[route for route in routes if service._route_serves(route['name'],spec['ai_names'])]
            if levels or ai:
                services.append({'service':spec['service'],'name':spec['name'],'levels':levels,'ai_routes':ai})
        for row in rows:
            if row.get('connector_id') not in declared:
                services.append({'service':row.get('connector_id'),'name':row.get('label') or row.get('connector_id'),
                                 'levels':[{**row,'level':'read'}],'ai_routes':[]})
        return services

    def own_client_input_hint(self):
        """Where the owner adds their own Google client, with this computer's direct address (#1211)."""
        service=self.service
        base=service.local_settings_url()
        if base:
            return (msg('quickstart_service.google_client_mac_hint', base=base))
        return msg('quickstart_service.google_client_hint')

    def _remember_connector_owner(self, owner):
        """Record an identity a Google authorization completed under.

        Disconnect must reach it later even after Telegram is unpaired or
        re-paired, when it is no longer derivable from current config.
        """
        service=self.service
        known=service.store.config(service.CONNECTOR_OWNERS_KEY,[])
        known=known if isinstance(known,list) else []
        if isinstance(owner,str) and owner not in known:
            service.store.put(service.CONNECTOR_OWNERS_KEY,[*known,owner][-50:])

    def _connector_owner_candidates(self):
        """Every connector owner identity this install can hold a row under."""
        service=self.service
        owners=[*service.surface_connector_owners(enabled_only=False),'local-owner']
        known=service.store.config(service.CONNECTOR_OWNERS_KEY,[])
        for owner in known if isinstance(known,list) else []:
            if isinstance(owner,str) and owner not in owners:
                owners.append(owner)
        return owners

    def google_revocation(self):
        service=self.service
        connections=[]
        if service.gmail:
            connections.append(registry_connection(GMAIL_CONNECTOR_ID,'Google Gmail',service.gmail,
                                                   service._connector_owner_candidates))
        if service.calendar_oauth:
            connections.append(registry_connection(CALENDAR_CONNECTOR_ID,'Google Calendar',service.calendar_oauth,
                                                   service._connector_owner_candidates,write=False))
            connections.append(registry_connection(CALENDAR_WRITE_CONNECTOR_ID,msg('handoff.create_google_calendar_events'),
                                                   service.calendar_oauth,service._connector_owner_candidates,write=True))
        if service.gmail_modify_oauth:
            connections.append(registry_connection(GMAIL_MODIFY_CONNECTOR_ID,msg('handoff.modify_gmail'),
                                                   service.gmail_modify_oauth,service._connector_owner_candidates))
            connections.append(registry_connection(GMAIL_FILTERS_CONNECTOR_ID,msg('handoff.gmail_filters'),
                                                   service.gmail_modify_oauth,service._connector_owner_candidates,grant=GMAIL_FILTERS_GRANT))
        if service.drive_oauth:
            connections.append(registry_connection(DRIVE_CONNECTOR_ID,'Google Drive',service.drive_oauth,
                                                   service._connector_owner_candidates))
        transport=service.google_revoke_transport or google_revoke_transport()
        return GoogleConnectionRevoker(service.store,connections,transport,now=service.google_revocation_clock,
                                       on_disconnected=service._withdraw_disconnected_work)

    def _withdraw_disconnected_work(self, connector_id, parked):
        """End Work that was waiting on a connection the owner just removed.

        A parked request must not run later through a stale resume handle
        just because the owner reconnects: the handle is destroyed and the
        Work gets an explicit terminal state.  Only waiting Work is touched;
        a finished result is an owner Artifact and stays.
        """
        service=self.service
        work_ids=list(parked or [])
        if service.connector_handoff:
            work_ids+=service.connector_handoff.supersede(connector_id=connector_id)
        text=service.GOOGLE_DISCONNECTED_WORK_TEXT
        with service.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for work_id in work_ids:
                db.execute(f"UPDATE jobs SET delivery='cancelled' WHERE id=? AND status IN {ws.sql_in(ws.WAITING_CONNECTION_DRIVE)} AND delivery='pending'",(work_id,))
                db.execute(f"UPDATE jobs SET status='failed',error=? WHERE id=? AND status IN {ws.sql_in(ws.WAITING_CONNECTION_DRIVE)}",(text,work_id))
        for work_id in work_ids:
            job=service.store.job(work_id)
            if job:service._present_outcome(job,delivered=False,blocked=False)
        return work_ids

    @staticmethod
    def _revocation_call(fn):
        try:
            return fn()
        except RevocationError as exc:
            raise ValueError(str(exc)) from None

    def google_disconnect_preview(self, body, session):
        service=self.service
        body=body if isinstance(body,dict) else {}
        return service._revocation_call(lambda:service.google_revocation().preview(session,body.get('connector_id')))

    def google_disconnect(self, body, session):
        service=self.service
        body=body if isinstance(body,dict) else {}
        return service._revocation_call(lambda:service.google_revocation().disconnect(
            session,body.get('connector_id'),body.get('confirmation')))

    def retry_google_revocation(self, body):
        service=self.service
        body=body if isinstance(body,dict) else {}
        return service._revocation_call(lambda:service.google_revocation().retry(body.get('connector_id')))

    def google_revocations(self):
        service=self.service
        return {'pending_provider_revocations':service.google_revocation().pending_revocations()}

    def begin_gmail_connection(self):
        """Return one owner-local Gmail authorization URL; grant nothing here.

        Issuing an authorization URL is not a connection and not a Grant: the
        connector row stays exactly as it was until Google redirects back and
        `complete_oauth` commits the scopes the owner actually approved.
        """
        service=self.service
        if not service.gmail:
            raise ValueError(ConnectorHandoff.unavailable(GMAIL_CONNECTOR_ID))
        return service.gmail.begin_oauth(service.connector_callback_owner(GMAIL_CONNECTOR_ID))

    def calendar_for(self, job):
        """The Calendar connector bound to this Work's owner, or None."""
        service=self.service
        return service.calendar_for_owner(service.issuer_owner_id(job))

    def calendar_for_owner(self, owner_id):
        service=self.service
        connector=service.calendar if service.calendar is not None else (
            service.calendar_factory('local-owner') if service.calendar_factory is not None else None)
        if connector is not None:
            connector.token_owner=lambda owner:MEMORY_OWNER
        return connector

    def drive_for(self, job):
        """The Drive reader bound to this Work's owner, or None (#1172)."""
        service=self.service
        if service.drive_reader_factory is None:
            return None
        return service.drive_reader_factory(service.connector_owner_id(job))

    def mail_for(self, job):
        """The worker's Gmail reader bound to this Work's owner, or None (#150)."""
        service=self.service
        if service.gmail is None:
            return None
        from .gmail import GmailWorkerReader
        return GmailWorkerReader(service.gmail,service.connector_owner_id(job))

    def _own_client_holders(self):
        """``{name: holder}`` of the connectors the owner's own client installed and that still run.

        By installation, not by client id: a separately configured Gmail or
        Calendar client may carry the same id and is not this client's.
        """
        service=self.service
        holders={'gmail':service.gmail,'calendar':service.calendar_oauth,'drive':service.drive_oauth,
                 'gmail_modify':service.gmail_modify_oauth}
        installed=getattr(service,'own_client_installed',None) or {}
        return {name:holder for name,holder in holders.items()
                if holder is not None and installed.get(name) is holder}

    def google_key_problem(self):
        """Why the Google token key could not be used at its last fetch, or None (#8).

        Read from what the encrypted stores recorded, so a status surface
        never waits on the macOS Keychain itself.
        """
        service=self.service
        from .connector_http import LOCAL_KEY_INVALID,LOCAL_KEY_UNAVAILABLE
        problems={getattr(getattr(holder,'store',None),'key_problem',None) for holder in service._own_client_holders().values()}
        return ('invalid' if LOCAL_KEY_INVALID in problems else 'unavailable' if LOCAL_KEY_UNAVAILABLE in problems else None)

    def _own_client_in_use(self):
        service=self.service
        own=service._own_client_connector_ids()
        return bool(own) and any(row.get('connector_id') in own and row.get('state') in ('connected','reauth_required')
                                 for row in service.google_connection_rows())

    def _own_client_revocation_pending(self):
        """Whether a disconnected own-client service still waits for Google to confirm revocation (#8).

        Its retry runs through the connector built for this client, so the
        client must stay until Google confirms.  Unknown counts as pending.
        """
        service=self.service
        own=service._own_client_connector_ids()
        if not own:
            return False
        try:
            pending=service.google_revocations()['pending_provider_revocations']
        except Exception:
            return True
        return any(row.get('connector_id') in own for row in pending)

    def _own_client_connector_ids(self):
        service=self.service
        ids={'gmail':(GMAIL_CONNECTOR_ID,),'calendar':(CALENDAR_CONNECTOR_ID,CALENDAR_WRITE_CONNECTOR_ID),
             'drive':(DRIVE_CONNECTOR_ID,),'gmail_modify':(GMAIL_MODIFY_CONNECTOR_ID,GMAIL_FILTERS_CONNECTOR_ID)}
        return {cid for name in service._own_client_holders() for cid in ids[name]}

    def _own_client_rows_held(self):
        """Registry state only (no stored credential is read): does an own-client row hold a connection?"""
        service=self.service
        own=service._own_client_connector_ids()
        return any(row.get('connector_id') in own and row.get('state') in ('connected','reauth_required')
                   for row in service.connector_connections())

    def _own_client_removal_refusal(self):
        """The catalog id of why the saved client cannot be removed now, or None (#8)."""
        service=self.service
        if service._own_client_in_use():
            return 'quickstart.google_client_disconnect_before_removing'
        if service._own_client_revocation_pending():
            return 'quickstart.google_client_revocation_pending'
        return None

    def _saved_google_client_id(self):
        service=self.service
        stored=service.store.secret(service.GOOGLE_CLIENT_SECRET_KEY)
        installed=stored.get('installed') if isinstance(stored,dict) else None
        return installed.get('client_id') if isinstance(installed,dict) else None

    def google_client_status(self):
        """The owner's own Google client as Settings shows it: never the secret, only a short id hint."""
        service=self.service
        stored=bool(service.store.secret(service.GOOGLE_CLIENT_SECRET_KEY))
        client_id=service.google_client_id or ''
        return {'configured':bool(service.google_client_source),'source':service.google_client_source,
                'saved':stored,
                # #8: also when the saved client is not the one running (a replacement, or a
                # concurrent save that lost the race), which applies after a restart.
                'restart_required':stored and (not service.google_client_source or (
                    service.google_client_source=='settings' and service._saved_google_client_id()!=service.google_client_id)),
                'client_hint':('…'+client_id.split('.apps.googleusercontent.com')[0][-6:]) if client_id else '',
                'available':callable(service.google_client_installer),
                # #8: removable once no service runs on it or waits for Google's revocation;
                # never an environment-pinned client.
                'removable':(stored and service.google_client_source!='environment'
                             and service._own_client_removal_refusal() is None),
                'key_problem':service.google_key_problem()}

    def save_google_client(self, body):
        """Save the owner's own Google "Desktop app" client and offer Gmail, Calendar and Drive (#1172).

        The value goes only to the owner-local secret store.  Saving grants
        nothing: each service still needs the owner's own Google consent.
        A first client is installed at once; replacing one that is already
        in use takes effect after Still restarts, because existing
        connections were issued to the previous client.
        """
        service=self.service
        from .quickstart import google_client_from_json
        if not callable(service.google_client_installer):
            raise ValueError(msg('quickstart_service.google_client_unsupported'))
        raw=(body or {}).get('client_json') if isinstance(body,dict) else None
        if not isinstance(raw,str) or not raw.strip() or len(raw)>20_000:
            raise ValueError(msg('quickstart_service.paste_google_client'))
        client=google_client_from_json(raw.strip())
        # Read before the lock: it may read stored credentials, whose key can
        # wait on the macOS Keychain (#8).
        running=service.google_client_id
        in_use=service._own_client_in_use()
        # #8: the checks, the save and the install are one step, so a second
        # concurrent first save sees the first one's client as the one in use.
        with service._GOOGLE_CLIENT_LOCK:
            if service.google_client_id!=running:
                # Another save installed a client meanwhile: check against that one.
                in_use=service._own_client_in_use()
            if service.google_client_source=='environment':
                raise ValueError(msg('quickstart_service.google_client_env_locked'))
            # Grants were issued to the client in use: replacing it under a live
            # connection would keep rows "connected" on tokens the new client can
            # neither renew nor reconnect over.  Disconnect first.
            if service.google_client_id and client[0]!=service.google_client_id and in_use:
                raise ValueError(msg('quickstart_service.google_client_connections_active'))
            service.store.secret(service.GOOGLE_CLIENT_SECRET_KEY,{'installed':{'client_id':client[0],'client_secret':client[1]}})
            if not service.google_client_source:
                service.google_client_installer((*client,'settings'))
        return service.google_client_status()

    def remove_google_client(self):
        """Remove the owner's saved Google client once its services are disconnected (#8).

        Only a client saved in Settings; one named by
        ``AGENTOS_GOOGLE_CLIENT_FILE`` is the operator's.  While a service
        still holds a connection issued to it, removal is refused: the owner
        disconnects those first, through the same Settings rows.  Removal
        deletes the saved client and stops offering connections through it
        at once.  It touches no Google account and no other connection.
        """
        service=self.service
        if not callable(service.google_client_installer):
            raise ValueError(msg('quickstart_service.google_client_unsupported'))
        # The full check reads stored credentials, so it runs before any lock
        # (their key may wait on the macOS Keychain).  Under the connectors'
        # OAuth locks, which every authorization completion holds through its
        # commit, it is repeated from registry state and the revocation slots
        # only, so a connection committed in between is seen.
        refusal=service._own_client_removal_refusal()
        with service._GOOGLE_CLIENT_LOCK, ExitStack() as held:
            for lock in {id(lock):lock for lock in (getattr(holder,'oauth_lock',None)
                                                    for holder in service._own_client_holders().values())
                         if lock is not None}.values():
                held.enter_context(lock)
            if service.google_client_source=='environment':
                raise ValueError(msg('quickstart.google_client_environment_not_removable'))
            if not service.store.secret(service.GOOGLE_CLIENT_SECRET_KEY):
                raise ValueError(msg('quickstart.google_client_nothing_to_remove'))
            if not refusal and service._own_client_rows_held():
                refusal='quickstart.google_client_disconnect_before_removing'
            if not refusal and service._own_client_revocation_pending():
                refusal='quickstart.google_client_revocation_pending'
            if refusal:
                raise ValueError(msg(refusal))
            service.store.remove_secret(service.GOOGLE_CLIENT_SECRET_KEY)
            holders=service._own_client_holders()
            if 'gmail' in holders:
                service.gmail=None
                service.gmail_token_exchange=None
            if 'calendar' in holders:
                service.calendar_oauth=None
                service.calendar_factory=None
                service.calendar_token_exchange=None
            if 'gmail_modify' in holders:
                service.gmail_modify_oauth=None
                service.gmail_modify_token_exchange=None
            if 'drive' in holders:
                service.drive_oauth=None
                service.drive_reader_factory=None
                service.drive_read_token_exchange=None
            service.own_client_installed={}
            if service.google_client_source=='settings':
                service.google_client_source=None
                service.google_client_id=None
        return service.google_client_status()

    def begin_drive_connection(self):
        """One owner-local Google Drive authorization URL (#1172); issuing it grants nothing."""
        service=self.service
        if not service.drive_oauth:
            raise ValueError(ConnectorHandoff.unavailable(DRIVE_CONNECTOR_ID))
        return service.drive_oauth.begin_oauth(service.connector_callback_owner(DRIVE_CONNECTOR_ID))

    def complete_drive_connection(self, callback):
        """Complete the Drive callback, then resume a Work parked for it, as Calendar does."""
        service=self.service
        if not service.drive_oauth or not callable(service.drive_read_token_exchange):
            raise ValueError(ConnectorHandoff.unavailable(DRIVE_CONNECTOR_ID))
        owner=service.drive_oauth.callback_owner(callback.get('state') if isinstance(callback,dict) else None,
                                                  ['local-owner'])
        parked=bool(service.connector_handoff and service.connector_handoff.record(DRIVE_CONNECTOR_ID))
        result=service.drive_oauth.complete_oauth(owner,callback,service.drive_read_token_exchange)
        service._remember_connector_owner(owner)
        service.notify_connection_completed(DRIVE_CONNECTOR_ID,resuming=parked)
        if not parked:
            return result
        try:
            resumed=service.resume_connector_work(DRIVE_CONNECTOR_ID,owner,tuple(result.get('granted_scopes') or ()))
        except (ConnectorContractError,ConversationHandoffError) as exc:
            return {**result,'resume_refused':exc.reason}
        if not resumed:
            return result
        return {**result,'work_id':resumed['work_id'],'scheduled':resumed['scheduled'],
                'resumed':resumed['resumed'],'resume_refusals':resumed['refused']}

    def begin_gmail_modify_connection(self, grant='modify'):
        """One owner-local Google Gmail modify authorization URL (#355); issuing it grants nothing."""
        service=self.service
        if not service.gmail_modify_oauth:
            raise ValueError(ConnectorHandoff.unavailable(GMAIL_MODIFY_CONNECTOR_ID))
        if grant not in ('modify','filters'):raise ValueError('invalid_grant')
        connector_id=GMAIL_FILTERS_CONNECTOR_ID if grant=='filters' else GMAIL_MODIFY_CONNECTOR_ID
        label=GMAIL_FILTERS_GRANT if grant=='filters' else GMAIL_MODIFY_GRANT
        return service.gmail_modify_oauth.begin_oauth(service.connector_callback_owner(connector_id),grant=label)

    def complete_gmail_modify_connection(self, callback):
        """Complete the Gmail modify callback, then resume a Work parked for it, as Calendar does."""
        service=self.service
        if not service.gmail_modify_oauth or not callable(service.gmail_modify_token_exchange):
            raise ValueError(ConnectorHandoff.unavailable(GMAIL_MODIFY_CONNECTOR_ID))
        connector_id=(GMAIL_FILTERS_CONNECTOR_ID if service.gmail_modify_oauth.pending_grant(callback)==GMAIL_FILTERS_GRANT
                      else GMAIL_MODIFY_CONNECTOR_ID)
        owner=service.gmail_modify_oauth.callback_owner(callback.get('state') if isinstance(callback,dict) else None,
                                                  ['local-owner'])
        parked=bool(service.connector_handoff and service.connector_handoff.record(connector_id))
        result=service.gmail_modify_oauth.complete_oauth(owner,callback,service.gmail_modify_token_exchange)
        connector_id=result.get('connector_id') or connector_id
        service._remember_connector_owner(owner)
        service.notify_connection_completed(connector_id,resuming=parked)
        if not parked:
            return result
        try:
            resumed=service.resume_connector_work(connector_id,owner,tuple(result.get('granted_scopes') or ()))
        except (ConnectorContractError,ConversationHandoffError) as exc:
            return {**result,'resume_refused':exc.reason}
        if not resumed:
            return result
        return {**result,'work_id':resumed['work_id'],'scheduled':resumed['scheduled'],
                'resumed':resumed['resumed'],'resume_refusals':resumed['refused']}

    def begin_calendar_connection(self, grant='read'):
        """Return one owner-local Calendar authorization URL for one grant.

        Read and write are separate connectors and separate credentials, so
        the owner authorizes them separately and a read grant can never widen
        into a write grant by accident. Issuing a URL grants nothing: the
        connector rows stay exactly as they are until Google redirects back.
        """
        service=self.service
        if not service.calendar_oauth:
            raise ValueError(ConnectorHandoff.unavailable(CALENDAR_CONNECTOR_ID))
        if grant not in ('read','write'):
            raise ValueError(msg('quickstart_service.check_calendar_scope'))
        connector_id=CALENDAR_WRITE_CONNECTOR_ID if grant=='write' else CALENDAR_CONNECTOR_ID
        owner=service.connector_callback_owner(connector_id)
        return service.calendar_oauth.begin_oauth(owner,write=grant=='write')

    def complete_calendar_connection(self, callback):
        """Complete one browser OAuth callback for whichever grant it names.

        The grant is carried by the signed state, not by the query, so a
        relabelled callback addresses the other grant's pending slot where its
        signature does not verify. As with Gmail, this never decides that a
        connection happened - `complete_oauth` owns that and commits it
        through `ConnectorRegistry.transition`.
        """
        service=self.service
        if not service.calendar_oauth:
            raise ValueError(ConnectorHandoff.unavailable(CALENDAR_CONNECTOR_ID))
        # The signed pending flow owns this callback; newer waits cannot
        # redirect it to another local/paired identity.
        connector_id=(CALENDAR_WRITE_CONNECTOR_ID
                      if service.calendar_oauth.pending_grant(callback)=='write'
                      else CALENDAR_CONNECTOR_ID)
        owner=service.calendar_oauth.callback_owner(callback.get('state') if isinstance(callback,dict) else None,
                                                       ['local-owner'])
        # Read the parked record before the exchange consumes the pending
        # state, so a callback that arrives with nothing parked is a plain
        # connection rather than a resume reporting `no_pending_work` to the
        # owner as if something had gone wrong. Gmail does this and the first
        # version of this method did not: the ordinary connect -- the one the
        # settings payload advertises -- committed the connection and then
        # returned HTTP 400 telling the owner it had failed.
        parked=bool(service.connector_handoff and service.connector_handoff.record(connector_id))
        result=service.calendar_oauth.complete_oauth(owner,callback,service.calendar_token_exchange)
        service._remember_connector_owner(owner)
        connector_id=result.get('connector_id') or connector_id
        granted=tuple(result.get('granted_scopes') or ())
        service.notify_connection_completed(connector_id,resuming=parked)
        if not parked:
            return result
        try:
            resumed=service.resume_connector_work(connector_id,owner,granted)
        except (ConnectorContractError,ConversationHandoffError) as exc:
            # The connection is real and committed; only the resume was
            # refused, and `resume_connector_work` has already told the owner.
            return {**result,'resume_refused':exc.reason}
        if not resumed:
            return result
        return {**result,'work_id':resumed['work_id'],'scheduled':resumed['scheduled'],
                'resumed':resumed['resumed'],'resume_refusals':resumed['refused']}

    def complete_gmail_connection(self, callback):
        """Complete one browser OAuth callback, then resume or fail parked Work.

        This never decides that a connection happened.  `complete_oauth` owns
        that decision and commits it through `ConnectorRegistry.transition`;
        everything below reads back what Still committed.
        """
        service=self.service
        if not service.gmail or not callable(service.gmail_token_exchange):
            raise ValueError(ConnectorHandoff.unavailable(GMAIL_CONNECTOR_ID))
        owner=service.gmail.callback_owner(callback.get('state') if isinstance(callback,dict) else None,
                                                  ['local-owner'])
        # Read the parked record before the exchange consumes the pending
        # state, so a callback that arrives with nothing parked is a plain
        # connection rather than a resume that reports `no_pending_work` to
        # the owner as if something had gone wrong.
        parked=bool(service.connector_handoff and service.connector_handoff.record(GMAIL_CONNECTOR_ID))
        try:
            status=service.gmail.complete_oauth(owner,dict(callback),service.gmail_token_exchange)
            service._remember_connector_owner(owner)
        except GmailError as exc:
            if parked and exc.reason in GMAIL_PROVEN_CALLBACK_REASONS:
                try:
                    service.deny_connector_work(GMAIL_CONNECTOR_ID,owner,
                                             'denied' if exc.reason=='authorization_denied' else 'callback_failed')
                except ValueError:
                    # `no_pending_work` or `wrong_owner`: the record moved or
                    # belongs to someone else, so leave that Work alone.
                    pass
            raise
        # The granted set comes from the committed connector row, not from the
        # browser query string and not from the provider's free-text `scope`.
        # `transition` refuses to record CONNECTED unless the grant equals the
        # connector's required scopes, so this is the one value that cannot
        # disagree with the authority `claim` re-checks, and the malformed
        # `granted_scopes` path that fails Work while leaving the contract row
        # pending stays unreachable from this route.
        granted=tuple(status.get('granted_scopes') or ())
        result={'connected':True,'connector_id':GMAIL_CONNECTOR_ID,'work_id':None,'scheduled':False}
        service.notify_connection_completed(GMAIL_CONNECTOR_ID,resuming=parked)
        if not parked:
            return result
        try:
            resumed=service.resume_connector_work(GMAIL_CONNECTOR_ID,owner,granted)
        except ConversationHandoffError as exc:
            # The connection is real and committed; only the resume was
            # refused, and `resume_connector_work` has already told the owner.
            return {**result,'resume_refused':exc.reason}
        return {**result,'work_id':resumed['work_id'],'scheduled':resumed['scheduled'],
                'resumed':resumed['resumed'],'resume_refusals':resumed['refused']}
