"""Still-held connections as read-only ``api_request`` slots (CONN-API).

The owner's AI decides at request time what it needs from a connected
service - one event in full, a message body, a file's revisions - and calls
the provider's own API through ``api_request``.  Still keeps what an AI
must not own: the token (resolved, renewed and revision-checked per call by
the connector's existing machinery, never shown), the provider API host and
path prefix the token may reach, and read-only methods for a read grant.
No field list, endpoint list or question is defined here (C16).

The tools that already exist for these services (``calendar_query``,
``drive_search``/``drive_read``) stay; this only removes the blocker that
anything outside their fixed shape was unreachable except by the browser.
"""

#: Connector id -> the slot it serves: provider API hosts, path prefixes and a note for the AI.
#: Every one is a read grant, so only GET/HEAD are allowed.  ``shortcuts`` are the
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
              'note': "the owner's Gmail, read-only (Gmail API v1, users/me)",
              'shortcuts': ()},
}


def shortcut_slot(action):
    """The slot whose service ``action`` reads a fixed shape of, or None (#1325)."""
    return next((name for name, spec in SLOT_SPECS.items() if action in spec['shortcuts']), None)


def _slot(name, token):
    spec = SLOT_SPECS[name]
    return {'name': name, 'note': spec['note'], 'hosts': tuple(spec['hosts']), 'paths': tuple(spec['paths']),
            'header': 'Authorization', 'scheme': 'Bearer', 'subject': None, 'revision': 1,
            'max_age_seconds': 900, 'read_only': True, 'connection': name, 'token': token,
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
    return {name: _slot(name, _relayed_token) for name in names if name in SLOT_SPECS}


def names(slots):
    """The Still-held connection slots among ``slots``, by name only (never a token)."""
    return tuple(sorted(name for name, slot in (slots or {}).items() if isinstance(slot, dict) and slot.get('connection')))


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
    """The connection slots this owner can use now: one per connected read connector, else none."""
    from .calendar import CALENDAR_CONNECTOR_ID
    from .calendar_oauth import DRIVE_GRANT, READ_GRANT
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
    gmail = getattr(service, 'gmail', None)
    if gmail is not None:
        from .gmail import GMAIL_CONNECTOR_ID
        if connected(GMAIL_CONNECTOR_ID):
            slots['gmail'] = _slot('gmail', _gmail_token(gmail, owner_id))
    return slots
