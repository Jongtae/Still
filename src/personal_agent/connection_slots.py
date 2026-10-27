"""Still-held connections as bounded ``api_request`` slots (CONN-API).

The owner's AI decides at request time what it needs from a connected
service - one event in full, a message body, a file's revisions - and calls
the provider's own API through ``api_request``.  Still keeps what an AI
must not own: the token (resolved, renewed and revision-checked per call by
the connector's existing machinery, never shown), the provider API host and
path prefix the token may reach, and read-only methods for a read grant.
Write grants are further bounded by endpoint authority allow-lists (#355).

The tools that already exist for these services (``calendar_query``,
``drive_search``/``drive_read``) stay; this only removes the blocker that
anything outside their fixed shape was unreachable except by the browser.
"""

#: Connector id -> the slot it serves: provider API hosts, path prefixes and a note for the AI.
#: Read grants allow only GET/HEAD. Write grants keep an explicit path allow-list.  ``shortcuts`` are the
#: fixed-shape tools that read a few fields of the same service's records (#1325):
#: their results name this slot as the way to read an item in full.
SLOT_SPECS = {
    'google-calendar': {'hosts': ('www.googleapis.com',), 'paths': ('/calendar/v3/',),
                        'note': "the owner's Google Calendar, read-only (Calendar API v3, e.g. "
                                "/calendar/v3/calendars/{calendarId}/events/{eventId})",
                        'shortcuts': ('calendar_query',)},
    'google-drive': {'hosts': ('www.googleapis.com',), 'paths': ('/drive/v3/',),
                     'note': "the owner's Google Drive, read-only (Drive API v3)",
                     'shortcuts': ('drive_search',)},
    'gmail': {'hosts': ('gmail.googleapis.com',), 'paths': ('/gmail/v1/users/me/',),
              'note': "the owner's Gmail, read-only (Gmail API v1, users/me); message bodies come "
                      "base64url-encoded, so read one with mail_read to get its text",
              'shortcuts': (), 'write_requires': 'gmail-modify',
              'write_note': "the owner's Gmail (Gmail API v1, users/me); can modify message/thread labels, "
                            "trash/untrash messages and threads, and create/update/delete labels; sending, "
                            "draft writes, import/insert, permanent deletion and settings writes are unavailable. "
                            "Message bodies are base64url-encoded; mail_read returns their text",
              'write_paths': (('POST', r'/gmail/v1/users/me/messages/[A-Za-z0-9_-]+/(modify|trash|untrash)'),
                              ('POST', r'/gmail/v1/users/me/messages/batchModify'),
                              ('POST', r'/gmail/v1/users/me/threads/[A-Za-z0-9_-]+/(modify|trash|untrash)'),
                              ('POST', r'/gmail/v1/users/me/labels'),
                              ('PATCH', r'/gmail/v1/users/me/labels/[A-Za-z0-9_-]+'),
                              ('PUT', r'/gmail/v1/users/me/labels/[A-Za-z0-9_-]+'),
                              ('DELETE', r'/gmail/v1/users/me/labels/[A-Za-z0-9_-]+'))},
}


def shortcut_slot(action):
    """The slot whose service ``action`` reads a fixed shape of, or None (#1325)."""
    return next((name for name, spec in SLOT_SPECS.items() if action in spec['shortcuts']), None)


#: #363: what a read-only slot with a separate write grant tells the AI, so a change goes to the
#: connection handoff (the call is refused before dispatch and Still parks the Work) rather than the browser.
WRITE_GRANT_NOTE = ("; changes need the owner's separate '{grant}' connection: make the change with api_request "
                    "anyway, and if it is refused as read-only Still asks the owner to connect and continues "
                    "this request once afterwards")


def write_grants():
    """The separate write connections a slot can be upgraded by (#363): ``write_requires`` of every spec."""
    return frozenset(spec['write_requires'] for spec in SLOT_SPECS.values() if spec.get('write_requires'))


def _slot(name, token, *, writable=False):
    spec = SLOT_SPECS[name]
    note = spec['write_note'] if writable else spec['note'] + (
        WRITE_GRANT_NOTE.format(grant=spec['write_requires']) if spec.get('write_requires') else '')
    return {'name': name, 'note': note, 'hosts': tuple(spec['hosts']), 'paths': tuple(spec['paths']),
            'header': 'Authorization', 'scheme': 'Bearer', 'subject': None, 'revision': 1,
            'max_age_seconds': 900, 'read_only': not writable, 'connection': name, 'token': token,
            'write_paths': spec.get('write_paths'), 'write_requires': spec.get('write_requires'),
            # #1329: the provider's live API returns the owner's records as they are at retrieval.
            'live': True}


def _relayed_token():
    raise RuntimeError('a relayed connection slot holds no token in this process')


def relayed(names):
    """Token-less slots a CLI bridge lists, by name, for calls it relays to the service (#1325).

    The bridge process holds no connector or token: it only needs to know that
    the Work has these connections, so ``api_request`` is offered there as it
    is on the direct route.  Every call is relayed and the service resolves
    the real slot; an unknown name is dropped.
    """
    return {name: _slot(name, _relayed_token, writable=bool(SLOT_SPECS[name].get('write_requires') in names))
            for name in names if name in SLOT_SPECS}


def names(slots):
    """The Still-held connection slots among ``slots``, by name only (never a token)."""
    held = {name: slot for name, slot in (slots or {}).items() if isinstance(slot, dict) and slot.get('connection')}
    return tuple(sorted(set(held) | {slot['write_requires'] for slot in held.values()
                                    if not slot.get('read_only') and slot.get('write_requires')}))


def _oauth_token(oauth, owner_id, grant, exchange):
    """``(access_token, finish)`` for one ``CalendarOAuth`` grant, as ``calendar_transport``/``drive_transport`` do it.

    The token is renewed first when expired or about to expire (#8).
    ``finish(status)`` runs after the provider answered: a 401 gets one
    renewal attempt for the revision that saw it, and moves the connection to
    REAUTH_REQUIRED only when that cannot renew (#8); Still does not resend the
    call, the AI decides whether to.  Otherwise the connection must still be the
    one the token came from, or the response is not used (review P2).
    """
    from .calendar_oauth import (CalendarRenewalUnavailable, _assert_current_request, _authorization_context,
                                 _mark_reauthentication_required)

    def renewed_after_rejection(revision):
        if not callable(exchange):
            return False
        try:
            return oauth.renew_after_rejection(owner_id, exchange, grant=grant, expected_revision=revision)
        except CalendarRenewalUnavailable:
            # No answer from Google: keep the stored credential; the 401 is reported as it is.
            return True

    def token():
        if callable(exchange):
            oauth.renew_if_due(owner_id, exchange, grant=grant)
        access_token, revision = _authorization_context(oauth.store, oauth.registry, owner_id, grant, oauth.now)

        def finish(status):
            if status == 401:
                if not renewed_after_rejection(revision):
                    _mark_reauthentication_required(oauth.store, oauth.registry, owner_id, grant,
                                                    expected_revision=revision)
                return
            _assert_current_request(oauth.store, oauth.registry, owner_id, grant, revision, access_token)
        return access_token, finish
    return token


def _gmail_token(gmail, owner_id):
    """``(access_token, finish)`` from the Gmail connector, re-checked after the call as its own reads are."""
    def token():
        _headers, revision, access_token = gmail._authorization_context(owner_id)
        return access_token, lambda status: gmail._assert_current_request(owner_id, revision, access_token)
    return token


def build(service, owner_id):
    """The connection slots this owner can use now: one per service, using a connected write grant when available."""
    from .calendar import CALENDAR_CONNECTOR_ID
    from .calendar_oauth import DRIVE_GRANT, READ_GRANT, GMAIL_MODIFY_GRANT
    from .connector_contract import ConnectorState
    from .google_drive_read import DRIVE_CONNECTOR_ID
    registry = getattr(service, 'connector_registry', None)
    if registry is None:
        return {}

    def connected(connector_id):
        try:
            return registry.status(owner_id, connector_id).state is ConnectorState.CONNECTED
        except Exception:
            return False
    slots = {}
    calendar_oauth = getattr(service, 'calendar_oauth', None)
    if calendar_oauth is not None and connected(CALENDAR_CONNECTOR_ID):
        slots['google-calendar'] = _slot('google-calendar', _oauth_token(
            calendar_oauth, owner_id, READ_GRANT, getattr(service, 'calendar_token_exchange', None)))
    drive_oauth = getattr(service, 'drive_oauth', None)
    if drive_oauth is not None and connected(DRIVE_CONNECTOR_ID):
        slots['google-drive'] = _slot('google-drive', _oauth_token(
            drive_oauth, owner_id, DRIVE_GRANT, getattr(service, 'drive_read_token_exchange', None)))
    modify = getattr(service, 'gmail_modify_oauth', None)
    from .gmail import GMAIL_MODIFY_CONNECTOR_ID
    if modify is not None and connected(GMAIL_MODIFY_CONNECTOR_ID):
        slots['gmail'] = _slot('gmail', _oauth_token(
            modify, owner_id, GMAIL_MODIFY_GRANT, getattr(service, 'gmail_modify_token_exchange', None)), writable=True)
        slots['gmail']['revision'] = registry.status(owner_id, GMAIL_MODIFY_CONNECTOR_ID).connection_revision
    gmail = getattr(service, 'gmail', None)
    if gmail is not None:
        from .gmail import GMAIL_CONNECTOR_ID
        if 'gmail' not in slots and connected(GMAIL_CONNECTOR_ID):
            slots['gmail'] = _slot('gmail', _gmail_token(gmail, owner_id))
    # #366: a read-only slot's refusal hands the AI the address that makes its write connection,
    # so the owner gets the link even when the Work cannot wait for it.  Naming it grants nothing.
    address = getattr(service, 'connector_connect_url', None)
    for slot in slots.values():
        if slot['read_only'] and slot.get('write_requires') and callable(address):
            try:
                slot['write_connect_url'] = address(slot['write_requires']) or ''
            except Exception:
                slot['write_connect_url'] = ''
    return slots
