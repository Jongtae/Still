"""Destination containment for owner-local connector HTTP.

Existing Solutions Review (C15), recorded here because it is the decision
this module encodes.

*Problem*: a connector request carries an owner OAuth bearer token in its
headers. The destination must stay the host the token was minted for, and
that has to hold across redirects -- an allowlist checked only on the URL the
caller named is an allowlist on the first hop.

*Considered*: **Adopt** `requests` / `httpx`, which have mature redirect
handling. Both strip `Authorization` on a cross-host redirect by default,
which is exactly the property wanted. Rejected for now: each is a new runtime
dependency for two call sites that issue GETs and one POST, they bring their
own TLS and connection-pool surface, and the repository already ships
stdlib-based owner-local HTTP in three places that would have to migrate
together to be worth it. Recorded as the obvious Adopt candidate if connector
HTTP grows.

*Considered*: **Adopt** stdlib defaults. Rejected on measured behaviour:
`urllib.request.HTTPRedirectHandler.redirect_request` strips only
`content-length` and `content-type`, and `http_error_302` permits `http`,
`https` and `ftp` targets -- so the default opener carries `Authorization`
verbatim to any host, including a scheme downgrade, for up to ten hops.
Independent review demonstrated an owner Gmail token arriving in cleartext at
a non-allowlisted host from a single allowlisted first hop.

*Decision*: **Adapt** -- stdlib `urllib` behind a narrow Still-owned
redirect guard. The guard re-applies the caller's own destination predicate
to every hop, so containment is a property of the opener rather than of the
one URL the caller happened to check.
"""
import json
from urllib.request import HTTPRedirectHandler, build_opener


def contained_opener(is_permitted):
    """An opener that re-checks the destination on every redirect hop.

    ``is_permitted(url)`` returns True for a destination this credential may
    reach. Refusing a hop returns ``None`` from ``redirect_request``, which
    makes urllib raise the original ``HTTPError`` -- so a refused redirect
    reaches the caller as the status it was, not as a silent success from
    wherever the chain ended.
    """
    if not callable(is_permitted):
        raise ValueError('a destination predicate is required')

    class Contained(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not is_permitted(newurl):
                return None
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    built = build_opener(Contained())
    # Carried so a caller's wiring can be asserted. Without it, a transport
    # that silently fell back to the bare default opener looked identical to
    # a contained one from every test that injected its own.
    built.agentos_destination_guard = is_permitted
    return built


# -- OAuth renewal policy shared by the Google connectors (#8) ---------------
#: An access token this close to its recorded expiry is renewed before a
#: request, so it does not expire while the request is in flight.  Acceptance
#: of a stored token still uses the exact recorded expiry.
RENEWAL_MARGIN_SECONDS = 120.0
#: The local encryption key could not be obtained (the key store refused or is
#: locked) or is not a usable key.  Neither says anything about the stored
#: grant, so neither requires reauthentication or clears a credential.
LOCAL_KEY_UNAVAILABLE = 'local_key_unavailable'
LOCAL_KEY_INVALID = 'local_key_invalid'
LOCAL_KEY_REASONS = frozenset({LOCAL_KEY_UNAVAILABLE, LOCAL_KEY_INVALID})


#: OAuth error codes (RFC 6749 section 5.2) that say the stored grant or the
#: client it belongs to is unusable.  Every other code -- ``invalid_request``,
#: ``unsupported_grant_type``, ``invalid_scope``, ``unauthorized_client`` --
#: can be a fault in the request Still sent, so it says nothing about the grant.
GRANT_REFUSED_ERRORS = frozenset({'invalid_grant', 'invalid_client'})
#: The most of a refusal body read to find its ``error`` code.  A real one is
#: a few hundred bytes of JSON.
_REFUSAL_BODY_LIMIT = 8192


def _oauth_error_code(error):
    """The ``error`` code of a token-endpoint refusal, or None when it cannot be read.

    The body is read once and bounded; it is parsed here and never logged or
    kept, so nothing of it reaches a status surface.
    """
    read = getattr(error, 'read', None)
    if not callable(read):
        return None
    try:
        raw = read(_REFUSAL_BODY_LIMIT + 1)
    except Exception:
        return None
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > _REFUSAL_BODY_LIMIT:
        return None
    try:
        body = json.loads(bytes(raw).decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None
    code = body.get('error') if isinstance(body, dict) else None
    return code if isinstance(code, str) and code else None


def token_endpoint_refused(error) -> bool:
    """Whether a failed OAuth token-endpoint call says the stored grant itself is unusable (#8, #246).

    RFC 6749 section 5.2: the token endpoint answers a request it refuses with
    HTTP 400, or 401 when client authentication failed, and names the reason
    in the body's ``error`` code.  Only ``invalid_grant`` (a revoked or
    expired refresh token) and ``invalid_client`` (a deleted or disabled
    client) say the stored grant cannot be used again, so only those are
    definitive.  A 401 whose body carries no readable code is still client
    authentication failing, which section 5.2 reserves for
    ``invalid_client``, so it is definitive too.

    Everything else keeps the stored credential so a later request renews it:
    another error code (it may be a fault in the request Still sent), a 400
    whose body cannot be read, no answer at all (a network failure or
    timeout), and a throttled or failing endpoint (429, 5xx).

    ``error`` is whatever the injected token exchange raised; urllib's
    ``HTTPError`` carries the status as ``code`` and the body behind
    ``read()``, which this consumes.
    """
    status = None
    for name in ('code', 'status'):
        value = getattr(error, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            status = value
            break
    if status not in (400, 401):
        return False
    code = _oauth_error_code(error)
    if code is None:
        return status == 401
    return code in GRANT_REFUSED_ERRORS
