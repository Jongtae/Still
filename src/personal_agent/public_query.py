"""A lexical credential tripwire for outbound public search queries.

:func:`validate_public_query` inspects the literal text of a query before it
leaves for a search provider, for credential, identity and filesystem shapes.
It is a **tripwire**, not the private-egress boundary: a tripwire over surface
forms can always be paraphrased, reworded, translated or summarised past, and
owner text that has been laundered through a model is invisible to it.  Treat
every match as a useful late warning and every non-match as *no evidence at
all* about provenance.  Each Work's information-use audit (#826) records what
owner information a lookup used and where it went.

#23 moved it here from the retired category research tool, so it now guards
every ``web_search`` rather than only that tool's one search.
"""

import base64
import binascii
import re
import unicodedata
from urllib.parse import unquote


ALLOWED_QUERY_SOURCES = frozenset({'owner_public_request', 'public_task_input'})

# ONE separator definition for every label -> value shape below. Divergent
# per-pattern separator sets are exactly how `secret; x` and `secret -> x`
# leaked while `password; x` and `source -> private-notes.md` were blocked.
# No label path below may define its own separator alternation -- including
# the header-shaped labels (authorization, bearer, cookie/set-cookie, token,
# session-id cookies), which previously kept private ':'/'=' sets and let
# 'Cookie; PHPSESSID abc123def456' through.
LABEL_SEPARATOR = r'(?:\s*(?:[:=,;|]|->|=>|→)\s*|\s+(?:is|are|was|were|equals?|as)\s+)'
# Tier 1 - labels specific enough that a bare occurrence beside a non-topic
# word is already evidence of a credential.
CREDENTIAL_LABEL = (
    r'(?:password|passwd|passphrase|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|'
    r'auth[_ -]?token|bearer[_ -]?token|session[_ -]?token|client[_ -]?secret|secret[_ -]?key|'
    r'private[_ -]?key|(?:seed|recovery|mnemonic)[_ -]?phrase)'
)
# Tier 2 - ordinary English words that only carry a credential in an explicit
# assignment form. `secret` stays here deliberately: promoting it to tier 1
# would block public queries such as 'Secret Garden hotel Seoul'.
WEAK_CREDENTIAL_LABEL = r'(?:secret|credentials?|token|mnemonic)'
# Tier 3 - short numeric secrets. These need both a label and a numeric value,
# because 'pin' and 'otp' are ordinary words in public catalogue text.
NUMERIC_CREDENTIAL_LABEL = (
    r'(?:pin|otp|cvv|cvc|one[- ]time\s+(?:password|passcode|code|pin)|'
    r'security\s+code|verification\s+code|비밀번호|인증번호)'
)
PATH_LABEL = r'(?:path|file|source)'
# A SCREAMING_SNAKE environment-variable name cannot reach the tier-1 labels
# above: `\bapi[_ -]?key\b` has no word boundary inside `MY_API_KEY`, and
# `\bpassword\b` none inside `DB_PASSWORD`, so an underscore-prefixed label
# never falls through to LABEL_ASSIGNMENT and needs its own boundary. Like
# every other label path it must use LABEL_SEPARATOR: keeping a private
# `\s*=\s*` here is what left `PGPASSWORD: hunter2`, `MY_API_KEY: ...` and
# `DB_PASSWORD -> ...` allowed while `PGPASSWORD=hunter2` was blocked.
# The suffix list is a vocabulary, so it is the remaining place this defect
# class can hide: DB_PWD, APP_PASS and SSH_KEY are plausible real env-var
# names, and a terminal plural or version digit (MY_API_KEYS, MY_API_KEY2)
# defeated a terminal-only match. Both are closed below. A dot separator is
# accepted alongside the underscore for the same reason.
ENV_CREDENTIAL_SUFFIX = (
    r'(?:[_.](?:PASSWORD|PASSWD|PWD|PASS|SECRET|SECRET_KEY|SECRETKEY|PRIVATE_KEY|'
    r'PRIVATEKEY|CLIENT_SECRET|TOKEN|API_KEY|APIKEY|ACCESS_KEY|ACCESSKEY|KEY|AUTH|'
    r'CREDENTIAL|CREDENTIALS))'
)
# Tolerate a terminal plural or version suffix so MY_API_KEYS and MY_API_KEY_V2
# cannot slip past a match anchored on the bare suffix.
ENV_CREDENTIAL_TAIL = r'(?:S|ES|[0-9]{1,3}|_V[0-9]{1,3})?'
ENV_CREDENTIAL_LABEL = (
    rf'(?<![A-Za-z0-9])[A-Z][A-Z0-9_.]{{0,80}}{ENV_CREDENTIAL_SUFFIX}'
    rf'{ENV_CREDENTIAL_TAIL}(?![A-Za-z0-9_])'
)
ENV_CREDENTIAL_NAME = r'(?:PGPASSWORD|MYSQL_PWD|REDISCLI_AUTH)'
IDENTITY_LABEL = (
    r'(?:passport(?:\s*(?:no\.?|number))?|여권\s*번호|social\s+security\s+number|ssn|'
    r'national\s+id(?:\s*(?:no\.?|number))?|주민(?:등록)?\s*번호|card\s*(?:no\.?|number)|'
    r'카드\s*번호|account\s*(?:no\.?|number)|계좌\s*번호)'
)
LABEL_OCCURRENCE = re.compile(rf'(?i)\b{CREDENTIAL_LABEL}\b')
LABEL_ASSIGNMENT = re.compile(rf'(?i)\b(?:{CREDENTIAL_LABEL}|{WEAK_CREDENTIAL_LABEL})\b{LABEL_SEPARATOR}\S+')
NUMERIC_CREDENTIAL_ASSIGNMENT = re.compile(rf'(?i)\b{NUMERIC_CREDENTIAL_LABEL}\b{LABEL_SEPARATOR}\d[\d -]{{2,}}')
IDENTITY_ASSIGNMENT = re.compile(rf'(?i)\b{IDENTITY_LABEL}\b{LABEL_SEPARATOR}[A-Za-z0-9][A-Za-z0-9 -]{{4,}}')
HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(rf'(?i)\bauthorization\b{LABEL_SEPARATOR}\S+'),
    re.compile(rf'(?i)\bauthorization\b(?:{LABEL_SEPARATOR}|\s+)(?:bearer|basic)\s+\S+'),
    re.compile(rf'(?i)\bbearer\b{LABEL_SEPARATOR}\S{{8,}}'),
    re.compile(r'(?i)\b(?:sk_live_|rk_live_)[a-z0-9]{12,}\b'),
    re.compile(r'\bAIzaSy[A-Za-z0-9_-]{20,}\b'),
    re.compile(r'(?i)\bhf_[a-z0-9]{16,}\b'),
    re.compile(r'(?i)\bsk-[a-z0-9_-]{12,}\b'),
    re.compile(r'(?i)\b(?:gh[pousr]_[a-z0-9]{16,}|github_pat_[a-z0-9_]{16,}|glpat-[a-z0-9_-]{16,}|xox[baprs]-[a-z0-9-]{10,}|npm_[a-z0-9]{16,}|pypi-[a-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b'),
    re.compile(r'(?i)-----BEGIN [A-Z0-9 -]*PRIVATE KEY(?: BLOCK)?-----'),
    re.compile(r'(?i)\b[a-z][a-z0-9+.-]*://[^\s/@]*@'),
    re.compile(rf'(?i)\b(?:cookie|set-cookie)\b{LABEL_SEPARATOR}\S+'),
    re.compile(rf'(?i)\btoken\b{LABEL_SEPARATOR}[^&\s]+'),
    re.compile(rf'(?i)(?:\b(?:phpsessid|sessionid|jsessionid|csrftoken|connect\.sid|asp\.net_sessionid|laravel_session)|\.aspnetcore\.session){LABEL_SEPARATOR}[^&\s]+'),
    re.compile(r'(?i)\bsecret\s+[a-z0-9_-]{20,}\b'),
    re.compile(rf'(?i){ENV_CREDENTIAL_LABEL}{LABEL_SEPARATOR}\S+'),
    re.compile(rf'(?i)\b{ENV_CREDENTIAL_NAME}\b{LABEL_SEPARATOR}\S+'),
    re.compile(r'(?i)(?<![\w])(?:\$[A-Z_][A-Z0-9_]*|\$\{[A-Z_][A-Z0-9_]*\})[/\\][^\s`"\'\[\](){}]+'),
    re.compile(r'(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*(?![A-Za-z0-9_-])'),
    re.compile(
        rf'(?i)\b{PATH_LABEL}\b{LABEL_SEPARATOR}'
        r'(?:~?[/\\]\S+|[a-z]:[/\\]\S+|[^\s`"\'\[\](){}]+[/\\][^\s`"\'\[\](){}]+|'
        r'[^\s`"\'\[\](){}]+\.[a-z0-9]{1,16}\b)'
    ),
    # Identity-document and payment-instrument shapes need no label at all.
    re.compile(r'\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[ -]?\d{4}[ -]?\d{4}[ -]?\d{3,4}\b'),
    re.compile(r'\b\d{6}[-\s]?[1-4]\d{6}\b'),
    re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
    re.compile(r'(?i)(?:file://|(?<![:/\\])[/\\]{2,}[^/\\\s`"\'\[\](){}]+[/\\][^\s`"\'\[\](){}]+|(?<![\w:/\\])(?:\.{1,2}[/\\][^\s`"\'\[\](){}]+|~[/\\][^\s`"\'\[\](){}]+|/[^\s/`"\'\[\](){}]+(?:/[^\s`"\'\[\](){}]+)?)|(?<![\w])[a-z]:[/\\][^\s`"\'\[\](){}]+)'),
)
PUBLIC_CREDENTIAL_TOPICS = frozenset({
    'about','and','are','authentication','authorization','best','compare','comparison','documentation','docs','examples','explain','expiry','expiration','for','how','information','latest','overview',
    'format','guide','management','manager','permissions','policies','policy','requirements','revocation',
    'rotation','scopes','security','to','tutorial','practices','what',
})

def validate_public_query(query, query_source):
    """Reject an outbound public query carrying credential, identity or private-file shapes.

    ``query_source`` is a **required, non-defaulting caller assertion**, and
    nothing more. This function cannot observe where ``query`` actually came
    from, so the value proves nothing on its own. The caller must guarantee
    that the query text was composed only from owner-typed public request text
    (``owner_public_request``) or public task input (``public_task_input``),
    and never from file, Memory, document or evidence content. Passing an
    allowed value for text derived from private material is a caller contract
    violation this function has no way to detect. Do not cite ``query_source``
    as a private-egress control.

    The checks below are a **lexical tripwire** over surface forms: they catch
    a credential, identity number or filesystem path that was pasted or copied
    through literally. They cannot catch paraphrased, translated, summarised
    or model-laundered private content; see the module docstring.
    """
    if query_source not in ALLOWED_QUERY_SOURCES:
        raise ValueError('공개 검색어의 출처를 명시해야 합니다. 파일과 Memory 내용은 검색어로 사용할 수 없습니다.')
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
        raise ValueError('공개 검색어는 1~500자로 입력하세요.')
    public_query=query.strip()
    strip_controls=lambda value:''.join(character for character in value if unicodedata.category(character) != 'Cf')
    raw_scan=strip_controls(public_query)
    decoded_scan=strip_controls(unquote(raw_scan))
    scan_query=' '.join((raw_scan,decoded_scan,strip_controls(unquote(decoded_scan))))
    sensitive=any(pattern.search(scan_query) for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS)
    # Every scheme occurrence must be examined. Stopping at the first match
    # lets an allowlisted topic word shield a later credential, as in
    # 'basic room rates Basic dTpw', and transmits it to the search provider.
    # These two allowlist-aware scanners are label -> value paths as well, so
    # they take the shared separator too. Keeping a bare `\s+` here left
    # `basic, dXNlcjpwYXNz` (base64 `user:pass`) allowed on every separator
    # except a space -- the ND-3 defect class on the Basic-auth path.
    for bearer_value in re.finditer(rf'(?i)\bbearer(?:{LABEL_SEPARATOR}|\s+)([^\s,;:!?()\[\]{{}}]{{8,}})',scan_query):
        bearer_token=bearer_value.group(1).strip('"\'.-_/@#$%^&*+=\\|<>`~').casefold()
        if bearer_token not in PUBLIC_CREDENTIAL_TOPICS:
            sensitive=True
            break
    for basic_value in re.finditer(rf'(?i)\bbasic(?:{LABEL_SEPARATOR}|\s+)([A-Za-z0-9+/=]{{4,}})(?![A-Za-z0-9+/=])',scan_query):
        basic_token=basic_value.group(1)
        try:
            decoded=base64.b64decode(basic_token+'='*((-len(basic_token))%4),validate=True)
        except (binascii.Error,ValueError):
            decoded=b''
        if b':' in decoded:
            sensitive=True
            break
    sensitive=sensitive or any(pattern.search(scan_query) for pattern in
                               (LABEL_ASSIGNMENT,NUMERIC_CREDENTIAL_ASSIGNMENT,IDENTITY_ASSIGNMENT))
    labels=list(LABEL_OCCURRENCE.finditer(scan_query))
    if labels:
        topic_text=LABEL_OCCURRENCE.sub(' ',scan_query)
        tokens=[]
        for raw in re.findall(r'[^\s,;:!?()\[\]{}]+',topic_text):
            token=raw.strip('"\'.-_/@#$%^&*+=\\|<>`~').casefold()
            if token: tokens.append(token)
        if not tokens or any(token not in PUBLIC_CREDENTIAL_TOPICS for token in tokens): sensitive=True
    if sensitive:
        raise ValueError('자격 증명 정보나 개인 파일 내용은 공개 검색어로 전송할 수 없습니다.')
    return public_query
