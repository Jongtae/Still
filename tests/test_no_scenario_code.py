"""SEC-EVAL-01 (#660): no decision/runtime branch on a named site, provider or request category.

Contract: ``docs/secretary-agency-contract.en.md`` "Probe scenarios and the
no-scenario-code rule" and completion-rule item 5.  Evidence class: static
structural check (stdlib ``ast``); it proves what the scanned source contains,
not how a live model behaves.

What is inspected.  Only string literals in positions where they steer
control flow:

- ``comparison``: an operand of ``==``/``!=``/``in``/``not in``/``is``,
  including a literal collection or a module/class constant it names;
- ``match-call``: an argument (or constant receiver) of a string-matching call
  such as ``startswith``, ``find``, ``re.search`` or a dict ``get``;
- ``match``: a ``case`` value or mapping key of a ``match`` statement;
- ``dispatch-key``: a key of a dict literal that is subscripted or ``get``-ed,
  directly or through the name it is bound to;
- ``match-table``: an element of a string collection (or ``re.compile``
  pattern, or a dict of such collections) bound to a name in any scope -
  module, class or function - that the module reads, iterated inline by a
  ``for``/comprehension, or handed inline to a helper call: the shape of a cue
  list consumed by a matcher.  A record that also holds a dict, number or
  ``None`` (a ``(template, params)`` pair) is a value, not a table.

Two rules apply to a literal in those positions:

- it names a site, provider or request category (``SITE_TOKENS``,
  ``CATEGORY_TOKENS``); such a literal is classified one by one;
- GUARD-NL-01 (#171): it is natural language - Hangul or CJK, or a
  multi-word English phrase (a regex's ``\\s`` reads as a space).  Such a cue
  may be classified one by one or as its whole table.

Message text is excluded by construction: an owner-facing Korean or English
sentence passed to a reply, a tool description or an error, a dict value or a
``get`` default is not in any of those positions, so it is never read.
``test_guard_catches_a_planted_scenario_branch`` and
``test_guard_catches_a_planted_natural_language_cue_table`` prove both
directions on planted modules.

The registry and adapter modules that must name providers are allowlisted
as whole modules (``ALLOWED_MODULES``); the provider-id enum offered to the
model is built at run time from that registry, so it has no literal here.

Anything else the scan finds must be listed in ``CLASSIFIED``, keyed by
``(module, literal)``, or (natural-language cues only) in ``CLASSIFIED_TABLES``,
keyed by ``(module, table or function name)``, with its classification and
reason, so every exception is reviewable in one place.  A new hit fails; a
listed entry that no longer occurs fails too, so the list cannot go stale.
#672 resolved the three open findings #660 reported.  The ``open-finding``
entries now listed are the cue tables GUARD-NL-01 (#171) found; each names
the issue that removes it, so the remaining work is tracked here and cannot
grow silently.
"""
import ast
import re
import tempfile
import textwrap
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'src' / 'personal_agent'

#: Decision and runtime modules.  ``decision*.py`` is globbed; ``preparations.py``
#: is scanned once it exists (SEC-ATTN-01 #659); ``orchestrator.py`` is the
#: ORCH-01 (#710) orchestration step and its worker catalogue; ``owner_model.py`` the
#: #805 owner-model upkeep; ``skills.py`` the #961 skill-supply glue.
SCANNED_MODULES = ('agent_runtime.py', 'conversation_handoff.py', 'current_context.py', 'browser_session.py',
                   'browser_worker.py', 'browser_jar.py',
                   'preparations.py', 'local_tools.py', 'quickstart_service.py', 'orchestrator.py', 'owner_model.py',
                   # SKILL-SUPPLY-02 (#961): the skill-supply glue stays free of site/category branches;
                   # skill content is data in packages, not scanned (#960 section 8).
                   'skills.py',
                   # API-READ-01 (#1216): the authenticated API call and its response provenance.
                   'api_requests.py',
                   # PROC-MEM-01 (#1486): the sandboxed code run and the per-connection function library.
                   'code_run.py', 'procedures.py',
                   # GUARD-NL-01 (#171): modules that read owner or page text.
                   'settings_orchestrator.py', 'telegram_presence.py',
                   'information_use.py',
                   # OWNER-ACT-01 (#172): the one judgment over owner acts and the open items it settles.
                   'owner_acts.py')

#: Modules whose job is to name providers.  Not scanned.
ALLOWED_MODULES = {
    'search_providers.py': 'provider registry and adapters (#655, #678): implements the connected AI\'s native search '
                           '(per owner-configured API route), Brave and the Bing RSS opt-in by name; the model-facing '
                           'provider enum is built from this registry at run time',
    # GUARD-NL-01 (#171): scanned first; they name their provider's connector id, OAuth scope and file
    # types, and hold no natural-language cue table (test_provider_adapters_are_allowlisted_with_a_reason).
    'calendar.py': 'Google Calendar provider adapter: its connector ids name the provider it calls',
    'connection_slots.py': "provider API slots (CONN-API): each slot names its connector id and the provider's "
                           'API host and path prefix the token may reach',
    'gmail.py': "Gmail provider adapter: compares the granted OAuth scope with Gmail's read-only scope URL",
    'google_drive_read.py': "Google Drive provider adapter: Drive's own Google Docs/Sheets/Slides MIME types "
                            'and its files.list query syntax',
}

#: Sites and providers from the probes and common Korean commerce/search hosts.
SITE_TOKENS = ('kyobo', 'coupang', 'yes24', 'aladin', 'naver', 'daum', 'kakao', 'google', 'bing', 'brave',
               '11st', 'gmarket', 'baemin', 'yogiyo')
#: Request categories the probes exercise.
CATEGORY_TOKENS = ('lunch', 'book', 'cart', '점심', '책', '장바구니')

_ASCII = [token for token in (*SITE_TOKENS, *CATEGORY_TOKENS) if token.isascii()]
_HANGUL = [token for token in CATEGORY_TOKENS if not token.isascii()]
#: A token starts a word: ``kyobo`` in ``kyobobook.co.kr``, ``google`` in
#: ``google-calendar``, but not ``bing`` in ``combining`` or ``책`` in ``정책``.
TOKEN_PATTERN = re.compile('|'.join([rf'(?<![a-z0-9]){re.escape(token)}' for token in _ASCII] +
                                    [rf'(?<![가-힣]){re.escape(token)}' for token in _HANGUL]))

#: Methods and functions whose string arguments are match patterns.
MATCH_CALLS = frozenset({'startswith', 'endswith', 'find', 'rfind', 'index', 'rindex', 'count', 'search', 'match',
                         'fullmatch', 'findall', 'finditer', 'compile', 'fnmatch', 'get', '__contains__'})
_COLLECTION_CALLS = frozenset({'frozenset', 'set', 'tuple', 'list'})

#: Every scan hit outside the allowed modules, reviewed once.  A hit is classified
#: by its literal here, or else as a whole table (or the function an inline table
#: sits in) in ``CLASSIFIED_TABLES``.
#:
#: - ``enforcement``: code that keeps an invariant (payment floor, secret names,
#:   redaction, licence, SSRF); a match can only add a refusal or an approval
#:   requirement, never route a request.
#: - ``adapter``: a provider's or tool's own fixed format (an API error, CLI
#:   output, SQL, a connector registry id), not owner or page language.
#: - ``own-text``: Still matches text Still itself wrote (its own message,
#:   error, sentinel or rendered line), never owner or page language.
#: - ``open-finding``: owner, worker or page text interpreted by a fixed rule,
#:   left in place here and tracked by the issue that removes it.
#:   Interpretation is never ``enforcement``.
CLASSIFICATIONS = frozenset({'enforcement', 'adapter', 'own-text', 'open-finding'})
CLASSIFIED = {
    ('local_tools.py', 'metadata.google.internal'): (
        'enforcement', 'SSRF denylist of cloud metadata hosts (DENIED_PUBLIC_HOSTS); a security boundary that '
                       'refuses every request to that host, not a site the model may choose'),
    ('quickstart_service.py', 'google-drive-read'): (
        'adapter', 'connector registry identity of the owner-connected Drive capability (read grant/handoff); '
                   'capability dispatch by connector id, not a site or provider chosen for a request'),
    ('quickstart_service.py', 'google-gmail-read'): (
        'adapter', 'connector registry identity of the owner-connected Gmail capability'),
    ('quickstart_service.py', 'google-calendar'): (
        'adapter', 'connector registry identity of the owner-connected Calendar capability'),
    ('quickstart_service.py', 'google-calendar-write'): (
        'adapter', 'connector registry identity of the owner-connected Calendar write grant'),
    ('decision_qualification.py', 'google-gmail-read'): (
        'adapter', "connector registry identity given as the parked Work's connector in a qualification case; "
                   'the judgment under test, not this literal, decides the outcome'),
    # #672 removed the three open findings #660 listed here ("book" in
    # _CALENDAR_VERBS, "google it" in _RESEARCH_CUES, "google drive" in
    # AgentService.requests_drive_access); calendar create and Drive read are
    # now the DecisionEngine's capability-need judgment, research the loop's.
    ('agent_runtime.py', 'HANGUL JONGSEONG '): (
        'adapter', 'a Unicode character-database name prefix (unicodedata.name) used to build the jamo table; '
                   'a platform format, not owner language'),
    ('agent_runtime.py', '승인한 공개 페이지 범위를 벗어난'): (
        'own-text', "part of the shared network reader's own redirect refusal, matched to replace it with the "
                    "search-result wording; Still's message, not owner or page text"),
    # SettingsOrchestrator.handle_text's id-bearing compatibility forms; the
    # rest of that function is listed in CLASSIFIED_TABLES.
    ('settings_orchestrator.py', r'(?:confirm|확인)\s+([A-Za-z0-9_-]+)'): (
        'enforcement', 'the explicit `/settings 확인 <id>` command naming one draft id (never shown), an exact act '
                       'like a button; OWNER-ACT-01 (#172) kept it while the typed confirm/decline pair became one '
                       'judgment, and the draft still passes its digest, TTL and owner_typed checks'),
    ('settings_orchestrator.py', r'(?:cancel|취소)\s+([A-Za-z0-9_-]+)'): (
        'enforcement', 'the explicit `/settings 취소 <id>` command naming one draft id (never shown), an exact act '
                       'like a button; OWNER-ACT-01 (#172) kept it, and it cancels only that owned draft'),
}

#: GUARD-NL-01 (#171): whole tables, keyed ``(module, name)``.  ``name`` is the
#: constant or table a literal was read through, or else the function an inline
#: table sits in.  A new table or function has a new name, so its cue literals
#: fail until listed here; an entry that no hit uses any more fails as stale.
CLASSIFIED_TABLES = {
    # --- enforcement ---------------------------------------------------------
    **{('browser_session.py', name): (
        'enforcement', '#758/#899/#936 payment floor: a control label or a site confirm question that may commit '
                       "a purchase or payment needs the owner's approval; over-inclusive by design, a match only "
                       'adds that requirement (#174 keeps these as the floor; its judgment can only add to them)')
       for name in ('COMMIT_PHRASES', 'COMMIT_VERBS', 'COMMIT_NOUN_END', 'COMMIT_NOUN_ALONE', 'COMMIT_NOUN_PRICE',
                    'COMMIT_QUESTION')},
    ('browser_session.py', 'action_input_safe.sensitive_name'): (
        'enforcement', 'secret field names (비밀번호, 인증번호, 토큰, 키): a value typed into such a field is never '
                       'approved silently; a match only refuses'),
    ('quickstart_service.py', '_SUBSCRIPTION_SECRET'): (
        'enforcement', 'secret names: a /search query that names a secret is not sent in the subscription CLI '
                       'preflight; a match only withholds'),
    ('agent_runtime.py', 'NOTES_SUMMARY_COMMANDS'): (
        'enforcement', 'source backfill: a past Work whose message was the notes-summary command may have spliced '
                       'every note, so it is never marked clean; a match only keeps a Work private'),
    ('telegram_presence.py', 'MASK_MARK'): (
        'enforcement', "redaction: the masking marks Still's own redaction writes; a step line carrying one is "
                       'never shown (#881)'),
    ('quickstart_service.py', 'AgentService._redact_provenance'): (
        'enforcement', 'redaction: the labels the path mask writes in place of the data and turn folders; '
                       'substituted for a path, not matched against owner text'),
    ('quickstart_service.py', 'AgentService._request_browser_login'): (
        'enforcement', "redaction: a URL carrying Still's [가림] mark is never opened as is, only its origin"),
    ('quickstart_service.py', 'AgentService.failed_preparation_watch_goals'): (
        'enforcement', 'redaction: a goal Still masked ([가림: ...]) is never reused as a goal'),
    ('skills.py', 'LICENCE_TEXTS'): (
        'enforcement', 'licence gate (#960/#1219): an external skill is enabled only under a recognised licence, '
                       'read from the fixed opening words of standard licence files'),
    # --- adapter -------------------------------------------------------------
    ('conversation_handoff.py', 'TELEGRAM_ENTITY_REJECTION'): (
        'adapter', "the Telegram Bot API's own error description for rejected message entities"),
    ('decision_adapters.py', 'CODEX_FEATURE_STAGES'): (
        'adapter', "the stage column of the Codex CLI's `features list` output"),
    ('decision_adapters.py', '_FEATURE_LINE'): (
        'adapter', "one line of the Codex CLI's `features list` output"),
    ('quickstart_service.py', 'AgentService._run_cli_attempt'): (
        'adapter', "the subscription CLI's own stdin diagnostic, read to record whether a photo was sent"),
    ('preparations.py', 'COLUMN_MIGRATIONS'): (
        'adapter', 'SQL column definitions of the preparations store migration'),
    # --- own-text ------------------------------------------------------------
    ('agent_runtime.py', 'PUBLIC_TASK_UNRESOLVED'): (
        'own-text', "Still's own refusal message, matched on the error it raised to pick the input_required code"),
    ('agent_runtime.py', 'lines'): (
        'own-text', 'deduplicates status lines Still itself wrote'),
    ('quickstart_service.py', 'lines'): (
        'own-text', 'deduplicates footer and status lines Still itself wrote'),
    ('quickstart_service.py', 'UNKNOWN_EFFECT_RETRY_REFUSAL'): (
        'own-text', "Still's own retry refusal, matched on the reason Still returned"),
    ('decision_routes.py', 'DecisionRoutes._first_qualified'): (
        'own-text', 'the "not reported" sentinel Still writes when a model does not report its identity'),
    ('information_use.py', 'BROWSER_SIGNINS_PREFIX'): (
        'own-text', "the browser sign-ins line Still's current context renders (quickstart BROWSER_SIGNINS_LINE)"),
    ('information_use.py', 'browser_signin_claims'): (
        'own-text', 'the "shared with you" note Still writes on a shared sign-in in that rendered line'),
    ('information_use.py', '_WORKING_NOTES'): (
        'own-text', 'the header line owner_model.WorkingNotes.block writes'),
    # --- open findings, each removed by the named issue ----------------------
    # OWNER-ACT-01 (#172) removed the calendar approve/cancel word tables listed here.
    # RULES-OUT-01 (#173, slice A) removed the knowledge, settings, workspace, mail and note
    # cue tables, the query-stripping tables, the inline reply/send verbs, the natural-language
    # owner "commands" in IntentClassifier.explicit and SettingsOrchestrator.handle_text's phrases:
    # those requests reach the worker.  It narrowed the workspace rules below to the owner's quoted
    # words and made a found saved result evidence for the worker, never the answer; #182 removed
    # those two forms once the worker could read the file workspace and save a result itself.  The
    # connection disconnect a settings phrase once drafted is the worker's settings_change draft since #183.
    # RULES-OUT-01 (#173, slice B) removed the correction/continuation cue tables, the lexical
    # skips of the follow-up judgment, the saved-context keyword trigger (now the explicit
    # `/context <request>` command) and preparations.same_goal's particle/filler tables.
    # RULES-OUT-01 (#173, slice C) removed the 컨텍스트: match on the AI's answer that hid the context
    # sources footer; the footer now comes from the typed record, always.
}


def scenario_tokens(text):
    """The site/provider/category tokens a literal names (case-insensitive)."""
    return sorted({match.group(0) for match in TOKEN_PATTERN.finditer(str(text).casefold())})


#: Hangul (syllables and jamo), kana and CJK ideographs.  CJK punctuation alone is not language.
_CJK = re.compile('[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯]')
#: A regex's ``\s``/``\b`` escapes read as a space, so ``express\s+checkout`` is a phrase too.
_REGEX_SPACE = re.compile(r'\\[sb][*+?]?')
#: Two English words separated by spaces; regex group brackets may sit between them
#: (``(?:order|buy)\s+now``, ``place\s+(?:your``).
_PHRASE = re.compile(r'[A-Za-z]{2,}\)*\??[ ]+\(*(?:\?[:!=])?[A-Za-z]{2,}')


#: A regex character range such as ``[가-힣]`` names an alphabet, not a word.
_CJK_RANGE = re.compile(f'{_CJK.pattern}-{_CJK.pattern}')


def natural_language(text):
    """#171: a literal is natural language when it holds Hangul or CJK, or a multi-word English phrase."""
    text = _CJK_RANGE.sub('', str(text))
    return bool(_CJK.search(text) or _PHRASE.search(_REGEX_SPACE.sub(' ', text)))


def _table(node, strict=False):
    """Whether ``node`` is a string table: a literal collection of strings, nested string tables or
    (unless ``strict``) computed entries such as ``msg(...)``, with at least one string, or
    ``frozenset``/``re.compile``/a union over one.  A record that also holds a dict, a number or
    ``None``, such as a ``(template, params)`` pair, is a value, not a table."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        def entry(element):
            if isinstance(element, ast.Constant):
                return isinstance(element.value, str)
            return _table(element, strict) or (not strict and isinstance(
                element, (ast.Call, ast.Name, ast.Attribute, ast.Starred)))
        return (all(entry(element) for element in node.elts)
                and any(isinstance(element, ast.Constant) or _table(element) for element in node.elts))
    if isinstance(node, ast.BinOp):
        return _table(node.left, strict) or _table(node.right, strict)
    if isinstance(node, ast.Call) and node.args:
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', None)
        return (name in _COLLECTION_CALLS and _table(node.args[0], strict)) or (
            name == 'compile' and isinstance(node.args[0], ast.Constant))
    return False


def _strings(node, constants=None, owner=None):
    """``(literal, name)`` pairs a node denotes: a constant, a literal collection
    (or a set union / tuple concatenation of them), a ``frozenset(...)``/``re.compile(...)``
    over one, or a constant it names.  ``name`` is the constant or table the literal was
    read through, else ``owner``."""
    constants = constants or {}
    if isinstance(node, ast.Constant):
        return [(node.value, owner)] if isinstance(node.value, str) else []
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [pair for element in node.elts for pair in _strings(element, constants, owner)]
    if isinstance(node, ast.BinOp) and (isinstance(node.op, ast.BitOr) or (
            isinstance(node.op, ast.Add) and isinstance(node.left, (ast.Tuple, ast.List)))):
        return _strings(node.left, constants, owner) + _strings(node.right, constants, owner)
    if isinstance(node, ast.Call) and node.args:
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', None)
        if name in _COLLECTION_CALLS or name == 'compile':
            return _strings(node.args[0], constants, owner)
    if isinstance(node, ast.Name):
        return [(value, node.id) for value in constants.get(node.id, [])]
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ('self', 'cls'):
        return [(value, node.attr) for value in constants.get(node.attr, [])]
    return []


def _bound_names(target):
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Attribute):
        return [target.attr]
    return []


def _scopes(tree):
    """``id(node) -> qualified name`` of the function or class each node sits in (``<module>`` at top level)."""
    scopes = {}

    def visit(node, name):
        for child in ast.iter_child_nodes(node):
            inner = name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = f'{name}.{child.name}' if name != '<module>' else child.name
            scopes[id(child)] = inner
            visit(child, inner)
    visit(tree, '<module>')
    return scopes


def scan_source(source, module):
    """``[(module, line, kind, literal, rules, name)]`` for one module's source.

    ``rules`` are the scenario tokens the literal names plus ``natural-language``
    (#171); ``name`` is the table or constant it was read through, or else the
    function it sits in, so a whole cue table can be classified once.
    """
    tree = ast.parse(source)
    scopes = _scopes(tree)
    top_level = {id(statement) for scope in [tree, *[node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]]
                 for statement in scope.body}
    # What a module or class constant's expression holds belongs to that name:
    # `CUES = tuple(re.compile(p) for p in (...))` is the table `CUES`.
    for statement in ast.walk(tree):
        if (id(statement) in top_level and isinstance(statement, (ast.Assign, ast.AnnAssign))
                and statement.value is not None):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            names = [name for target in targets for name in _bound_names(target)]
            if names:
                for node in ast.walk(statement.value):
                    scopes[id(node)] = names[0]
    # String collections and patterns are collected from every scope (module,
    # class and function bodies, #660 review); a name bound more than once
    # keeps every literal it was bound to, so the guard over-reports rather
    # than misses a shadowed cue list.  A single string is resolved only from
    # module/class constants: a local variable assigned one value and compared
    # to another (`provider = 'x'` then `provider == y`) is a value, not a table.
    constants = {}      # bound name -> string literals it holds
    collections = {}    # bound name -> [(line, literals)] of a string collection or pattern
    dicts = {}          # any bound name -> [(line, key)] of a dict literal's string keys
    for statement in ast.walk(tree):
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        single = isinstance(value, ast.Constant)
        if isinstance(value, ast.Dict):
            # A dict of cue lists (`{'body': ('본문', ...)}`) holds tables in its values.
            literals = [literal for item in value.values if _table(item) for literal, _name in _strings(item)]
        else:
            literals = [literal for literal, _name in _strings(value)] if single or _table(value) else []
        if not literals or (single and id(statement) not in top_level):
            continue
        for target in targets:
            for name in _bound_names(target):
                constants.setdefault(name, []).extend(literals)
                if not single:
                    collections.setdefault(name, []).append((statement.lineno, literals))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Dict):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            keys = [(key.lineno, key.value) for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)]
            for target in targets:
                for name in _bound_names(target):
                    dicts.setdefault(name, []).extend(keys)

    hits = []

    def add(line, kind, pairs, owner, language=True):
        # ``language=False``: a position whose literal is a value, not a pattern (a ``get`` default).
        for value, name in pairs:
            rules = scenario_tokens(value) + (['natural-language'] if language and natural_language(value) else [])
            if rules:
                hits.append((module, line, kind, value, rules, name or owner))

    loaded = set()
    dispatched = set()
    for node in ast.walk(tree):
        owner = scopes.get(id(node), '<module>')
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loaded.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            loaded.add(node.attr)
        if isinstance(node, ast.Compare):
            for operand in (node.left, *node.comparators):
                add(node.lineno, 'comparison', _strings(operand, constants), owner)
        elif isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name)):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            if name in MATCH_CALLS:
                for index, argument in enumerate((*node.args, *(keyword.value for keyword in node.keywords))):
                    add(node.lineno, 'match-call', _strings(argument, constants), owner,
                        language=not (name == 'get' and index > 0))
            elif name not in _COLLECTION_CALLS:
                # A cue list handed to a helper that matches it: `_cue_hits(text, lowered, ('답장', 'reply'))`.
                for argument in (*node.args, *(keyword.value for keyword in node.keywords)):
                    if _table(argument, strict=True):
                        add(node.lineno, 'match-table', _strings(argument), owner)
            if name in MATCH_CALLS and isinstance(node.func, ast.Attribute):
                add(node.lineno, 'match-call', _strings(node.func.value, constants), owner)
                if name == 'get':
                    receiver = node.func.value
                    if isinstance(receiver, ast.Dict):
                        add(node.lineno, 'dispatch-key',
                            _strings(ast.Tuple(elts=[k for k in receiver.keys if k], ctx=ast.Load())), owner)
                    dispatched.update(_bound_names(receiver))
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            if isinstance(node.value, ast.Dict):
                add(node.lineno, 'dispatch-key', [(key.value, None) for key in node.value.keys
                                                  if isinstance(key, ast.Constant) and isinstance(key.value, str)],
                    owner)
            dispatched.update(_bound_names(node.value))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and _table(node.iter):
            # An inline cue list iterated by a matcher: `any(c in text for c in ('a', 'b'))`.
            add(getattr(node, 'lineno', None) or node.iter.lineno, 'match-table', _strings(node.iter), owner)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                for pattern in ast.walk(case.pattern):
                    if isinstance(pattern, ast.MatchValue):
                        add(pattern.lineno, 'match', _strings(pattern.value), owner)
                    elif isinstance(pattern, ast.MatchMapping):
                        for key in pattern.keys:
                            add(pattern.lineno, 'match', _strings(key), owner)
    for name in dispatched & set(dicts):
        for line, key in dicts[name]:
            add(line, 'dispatch-key', [(key, name)], name)
    for name, bindings in collections.items():
        if name in loaded:
            for line, literals in bindings:
                add(line, 'match-table', [(literal, name) for literal in literals], name)
    return hits


def scanned_paths(root=SRC):
    names = [*SCANNED_MODULES, *sorted(path.name for path in root.glob('decision*.py'))]
    return [root / name for name in dict.fromkeys(names) if (root / name).is_file()]


def scan(paths):
    hits = []
    for path in paths:
        hits.extend(scan_source(path.read_text(encoding='utf-8'), path.name))
    return hits


def classified_by(hit):
    """The ``CLASSIFIED`` or ``CLASSIFIED_TABLES`` key that covers one hit, or None.

    A literal naming a site, provider or category must be listed by itself; only a
    natural-language cue (#171) may be covered by its table's entry, so adding a site
    to a listed cue table still fails.
    """
    module, _line, _kind, literal, rules, name = hit
    if (module, literal) in CLASSIFIED:
        return ('literal', module, literal)
    if rules == ['natural-language'] and (module, name) in CLASSIFIED_TABLES:
        return ('table', module, name)
    return None


class NoScenarioCodeTests(unittest.TestCase):
    def test_decision_and_runtime_code_names_no_site_provider_or_category(self):
        hits = scan(scanned_paths())
        unclassified = sorted({(module, line, kind, literal, name) for module, line, kind, literal, _rules, name
                               in hits if classified_by((module, line, kind, literal, _rules, name)) is None})
        self.assertEqual(unclassified, [], 'decision/runtime code branches on a named site, provider or request '
                                           'category, or matches owner or page text against a natural-language '
                                           'cue table (#171); make the model judge instead, or classify the '
                                           'literal in CLASSIFIED (or its table in CLASSIFIED_TABLES) with a '
                                           'reviewable reason')
        used = {classified_by(hit) for hit in hits}
        stale = sorted({('literal', *key) for key in CLASSIFIED} - used)
        stale += sorted({('table', *key) for key in CLASSIFIED_TABLES} - used)
        self.assertEqual(stale, [], 'a CLASSIFIED or CLASSIFIED_TABLES entry no longer occurs; remove it')

    def test_every_classification_is_known_and_an_open_finding_names_its_issue(self):
        for key, (classification, reason) in (*CLASSIFIED.items(), *CLASSIFIED_TABLES.items()):
            self.assertIn(classification, CLASSIFICATIONS, key)
            self.assertTrue(reason.strip(), key)
            if classification == 'open-finding':
                self.assertRegex(reason, r'^#\d+: ', key)

    def test_provider_adapters_are_allowlisted_with_a_reason(self):
        for module in ('calendar.py', 'connection_slots.py', 'gmail.py', 'google_drive_read.py'):
            self.assertIn(module, ALLOWED_MODULES)
            self.assertTrue(ALLOWED_MODULES[module].strip(), module)
        # GUARD-NL-01 (#171) checked them first: they name their provider, and hold no
        # natural-language cue table.
        hits = scan([SRC / module for module in ('calendar.py', 'connection_slots.py', 'gmail.py',
                                                 'google_drive_read.py')])
        self.assertEqual([hit for hit in hits if 'natural-language' in hit[4]], [])

    def test_natural_language_is_hangul_cjk_or_an_english_phrase(self):
        for text in ('승인', '注文する', '購入', 'go ahead', 'never mind', r'\bexpress\s+checkout\b',
                     r'\b(?:order|buy)\s+now\b', r'\bplace\s+(?:your\s+)?order\b'):
            self.assertTrue(natural_language(text), text)
        for text in ('ok', 'cancel', '/settings', 'google-calendar', 'text/html', r'[a-z]+|[가-힣]+',
                     r'^[\w.-]+$', '。！', 'x y'):
            self.assertFalse(natural_language(text), text)

    def test_scope_is_the_decision_and_runtime_modules(self):
        names = {path.name for path in scanned_paths()}
        for required in ('agent_runtime.py', 'conversation_handoff.py', 'current_context.py', 'browser_session.py',
                         'browser_worker.py', 'browser_jar.py',
                         'local_tools.py', 'quickstart_service.py', 'decision.py', 'decision_adapters.py',
                         'decision_routes.py', 'orchestrator.py',
                         # GUARD-NL-01 (#171): the modules that read owner or page text.
                         'settings_orchestrator.py', 'telegram_presence.py',
                         'information_use.py'):
            self.assertIn(required, names)
        self.assertFalse(names & set(ALLOWED_MODULES))
        for module in ALLOWED_MODULES:
            self.assertTrue((SRC / module).is_file(), module)

    def test_tokens_start_words(self):
        self.assertEqual(scenario_tokens('https://www.kyobobook.co.kr/cart'), ['cart', 'kyobo'])
        self.assertEqual(scenario_tokens('google-calendar'), ['google'])
        self.assertEqual(scenario_tokens('점심 뭐 먹지'), ['점심'])
        self.assertEqual(scenario_tokens('책 사줘'), ['책'])
        for harmless in ('combining', 'notebook', '정책', 'Descartes', 'provider'):
            self.assertEqual(scenario_tokens(harmless), [], harmless)

    def test_guard_catches_a_planted_scenario_branch(self):
        planted = textwrap.dedent('''
            import re
            _LUNCH_CUES = ('점심', 'lunch')
            _HOST = re.compile(r'coupang\\.com')
            HANDLERS = {'yes24': object, 'web': object}
            NOTICE = '장바구니에 담았습니다.'

            def plan(url, provider, text, site, reply):
                if 'kyobobook' in url:
                    return 'direct'
                if provider == 'naver':
                    provider = 'brave'
                if url.startswith('https://www.aladin.co.kr'):
                    return None
                if any(cue in text for cue in _LUNCH_CUES):
                    return 'menu'
                if _HOST.search(url):
                    return 'shop'
                if re.search(r'장바구니', text):
                    return 'cart'
                match site:
                    case '11st':
                        return 'mall'
                handler = HANDLERS[site]
                route = {'baemin': 1}.get(site)
                # A cue list bound inside the function, then consumed by a matcher.
                cues = ('kyobo', '점심 메뉴')
                if any(c in text for c in cues):
                    return 'local'
                # A cue list iterated inline.
                if any(word in text for word in ('gmarket', 'yogiyo')):
                    return 'inline'
                # Message text is not a branch condition and is never read.
                reply('책을 찾았습니다. 점심 추천도 준비할게요.', notice=NOTICE)
                description = 'Search Naver, Brave or Bing; add the book to the cart.'
                return handler, route, description
        ''')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'planted_runtime.py'
            path.write_text(planted, encoding='utf-8')
            hits = scan([path])
        by_literal = {}
        for _module, _line, kind, literal, _rules, _name in hits:
            by_literal.setdefault(literal, set()).add(kind)
        self.assertIn('comparison', by_literal['kyobobook'])
        self.assertIn('comparison', by_literal['naver'])
        self.assertIn('https://www.aladin.co.kr', by_literal)
        self.assertIn('match-table', by_literal['점심'])
        self.assertIn('match-table', by_literal['lunch'])
        self.assertIn('coupang\\.com', by_literal)
        self.assertIn('match-call', by_literal['장바구니'])
        self.assertIn('match', by_literal['11st'])
        self.assertIn('dispatch-key', by_literal['yes24'])
        self.assertIn('dispatch-key', by_literal['baemin'])
        self.assertIn('match-table', by_literal['kyobo'])
        self.assertIn('match-table', by_literal['점심 메뉴'])
        self.assertIn('match-table', by_literal['gmarket'])
        self.assertIn('match-table', by_literal['yogiyo'])
        # Owner-facing message text and a tool description are not conditions.
        for message in ('장바구니에 담았습니다.', '책을 찾았습니다. 점심 추천도 준비할게요.',
                        'Search Naver, Brave or Bing; add the book to the cart.'):
            self.assertNotIn(message, by_literal)
        # 'brave' is assigned, not compared: a value, not a branch.
        self.assertNotIn('brave', by_literal)
        # A planted branch is unclassified, so the main assertion would fail on it.
        self.assertTrue(all((path.name, literal) not in CLASSIFIED for literal in by_literal))

    def test_guard_catches_a_planted_natural_language_cue_table(self):
        """#171: a natural-language cue table in a steering position is caught; reply text is not."""
        planted = textwrap.dedent('''
            import re
            YES_WORDS = frozenset({'승인', '넵', 'go ahead', 'ok'})
            _KINDS = {'body': ('본문', 'full message')}
            _DONE_RE = re.compile(r'\\b(?:that\\s+is\\s+all|끝났어)\\b')
            READY = '준비됐어요. 확인해 주세요.'
            LABELS = {'create': '새 일정을 만들까요?'}

            def route(text, reply, cue_hits):
                if text.strip() in YES_WORDS:
                    reply(READY)
                    return 'approve'
                if text == '어떻게 복구':
                    return 'recover'
                if text.startswith(('메모:', 'note this')):
                    return 'note'
                if any(word in text for word in ('취소', 'never mind')):
                    return 'cancel'
                if cue_hits(text, ('답장', 'reply to')):
                    return 'mail'
                if any(cue_hits(text, cues) for cues in _KINDS.values()) or _DONE_RE.search(text):
                    return 'done'
                # Owner-facing message text is not a condition and is never read.
                reply('일정을 만들었어요. 다른 것도 도와드릴까요?')
                reply(LABELS.get('create', '알 수 없는 요청이에요. 다시 말씀해 주세요.'))
                reason = ('기본 AI({main})를 아직 확인하지 않았습니다.', {'main': 'x'})
                return reason
        ''')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings_orchestrator.py'
            path.write_text(planted, encoding='utf-8')
            hits = scan([path])
        by_literal = {}
        for _module, _line, kind, literal, rules, name in hits:
            self.assertEqual(rules, ['natural-language'], literal)
            by_literal.setdefault(literal, set()).add((kind, name))
        # Cue tables and cue literals, read through their table name or their function.
        self.assertIn(('match-table', 'YES_WORDS'), by_literal['승인'])
        self.assertIn(('comparison', 'YES_WORDS'), by_literal['넵'])
        self.assertIn(('match-table', 'YES_WORDS'), by_literal['go ahead'])
        self.assertIn(('comparison', 'route'), by_literal['어떻게 복구'])
        self.assertIn(('match-call', 'route'), by_literal['메모:'])
        self.assertIn(('match-call', 'route'), by_literal['note this'])
        self.assertIn(('match-table', 'route'), by_literal['취소'])
        self.assertIn(('match-table', 'route'), by_literal['never mind'])
        self.assertIn(('match-table', 'route'), by_literal['답장'])
        self.assertIn(('match-table', 'route'), by_literal['reply to'])
        self.assertIn(('match-table', '_KINDS'), by_literal['본문'])
        self.assertIn(('match-table', '_KINDS'), by_literal['full message'])
        self.assertIn('\\b(?:that\\s+is\\s+all|끝났어)\\b', by_literal)
        # A single English word is not natural language by itself; its table is caught by its phrases.
        self.assertNotIn('ok', by_literal)
        # Owner-facing message text is ignored: a reply argument, a constant passed to a reply,
        # a dict value or a ``get`` default, and a (template, params) record.
        for message in ('준비됐어요. 확인해 주세요.', '일정을 만들었어요. 다른 것도 도와드릴까요?',
                        '새 일정을 만들까요?', '알 수 없는 요청이에요. 다시 말씀해 주세요.',
                        '기본 AI({main})를 아직 확인하지 않았습니다.'):
            self.assertNotIn(message, by_literal)
        # None of them is classified, even in a module that has classified tables:
        # a new cue table, or a new cue function, fails the main assertion.
        self.assertTrue(hits)
        self.assertEqual([hit for hit in hits if classified_by(hit) is not None], [])

    def test_a_site_added_to_a_classified_cue_table_still_fails(self):
        planted = "COMMIT_PHRASES = frozenset({'결제하기', 'coupang'})\n\ndef ok(text):\n    return text in COMMIT_PHRASES\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'browser_session.py'
            path.write_text(planted, encoding='utf-8')
            hits = scan([path])
        covered = {hit[3]: classified_by(hit) for hit in hits}
        self.assertEqual(covered['결제하기'], ('table', 'browser_session.py', 'COMMIT_PHRASES'))
        self.assertIsNone(covered['coupang'])



#: C16 amendment (#974): skill content may carry domain and site know-how, so core code must
#: treat every skill alike.  A string literal anywhere in core that names one skill (exactly,
#: or as a path to it) would let core special-case that skill's content.
SKILL_ROOT = SRC / 'bundled_skills'


def skill_names():
    """The bundled skills' names: the skills core could most easily special-case."""
    return sorted(path.name for path in SKILL_ROOT.iterdir() if (path / 'SKILL.md').is_file())


def skill_name_literals(paths, names):
    """``(module, line, literal)`` of each string literal that names one of ``names``, anywhere in it.

    Bounded by word characters and hyphens, so ``personal-agentos-skills`` (a User-Agent) does not
    name ``agentos-skills``.  Installed skills are runtime data and cannot be scanned statically.
    """
    hits = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literal = node.value
                if any(re.search(rf'(?<![\w-]){re.escape(name)}(?![\w-])', literal) for name in names):
                    hits.append((path.name, node.lineno, literal))
    return hits


class CoreNamesNoSkill(unittest.TestCase):
    """#974: know-how lives in skill content; core code never names a particular skill."""

    def test_no_core_module_names_a_skill(self):
        names = skill_names()
        self.assertTrue(names, 'the bundled skills exist')
        self.assertEqual(skill_name_literals(sorted(SRC.rglob('*.py')), names), [])

    def test_guard_catches_a_planted_skill_branch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'planted.py'
            path.write_text(textwrap.dedent("""
                SPECIAL = 'bundled_skills/agentos-management/SKILL.md'
                HINT = 'For settings, first skill_load agentos/agentos-management.'
                def pick(entry):
                    if entry['name'] == 'agentos-management':
                        return 'special'
                    return 'personal-agentos-skills'  # a substring is not a name
            """))
            hits = skill_name_literals([path], ['agentos-management', 'agentos-skills'])
        self.assertEqual(sorted(literal for _module, _line, literal in hits),
                         ['For settings, first skill_load agentos/agentos-management.', 'agentos-management',
                          'bundled_skills/agentos-management/SKILL.md'])


#: #974 review: only the skill-supply glue reads skill files; core never opens one on its own.
SKILL_FILE_LITERALS = ('SKILL.md', 'bundled_skills')
SKILL_FILE_NAMES = ('BUNDLED_ROOT',)


def skill_file_references(paths):
    hits = []
    for path in paths:
        if path.name == 'skills.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        # #346 moves the synthetic qualification fixture into runtime. It writes
        # a canary skill, never reads or chooses a real skill. Exempt only that
        # exact write target; reads and all other references remain forbidden.
        synthetic_writes = set()
        if path.name == 'cli_qualification.py':
            for function in tree.body:
                if isinstance(function, ast.FunctionDef) and function.name == 'populate_codex_home':
                    for call in ast.walk(function):
                        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                                and call.func.attr == 'write_text'):
                            target = call.func.value
                            if (isinstance(target, ast.BinOp) and isinstance(target.op, ast.Div)
                                    and isinstance(target.left, ast.Name) and isinstance(target.right, ast.Constant)
                                    and target.right.value == 'SKILL.md'):
                                synthetic_writes.add(id(target.right))
        for node in ast.walk(tree):
            if id(node) in synthetic_writes:
                continue
            text = (node.value if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    else node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                    else node.name if isinstance(node, ast.alias) else None)
            if text is not None and (any(item in text for item in SKILL_FILE_LITERALS) if isinstance(node, ast.Constant)
                                     else text in SKILL_FILE_NAMES):
                hits.append((path.name, node.lineno, text))
    return hits


class CoreReadsNoSkillFiles(unittest.TestCase):
    def test_only_the_skill_supply_glue_refers_to_skill_files(self):
        self.assertEqual(skill_file_references(sorted(SRC.rglob('*.py'))), [])

    def test_guard_catches_a_planted_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'planted.py'
            path.write_text("from personal_agent.skills import BUNDLED_ROOT\nTEXT = (BUNDLED_ROOT / 'x' / 'SKILL.md').read_text()\n")
            hits = skill_file_references([path])
        self.assertEqual(sorted(text for _module, _line, text in hits), ['BUNDLED_ROOT', 'BUNDLED_ROOT', 'SKILL.md'])



#: #975 review: installed skills are runtime data, so naming them cannot be enumerated.  Structurally,
#: core may not compare a skill's identity with a string literal, select one by a literal key, or load
#: skill text anywhere but the model's own ``skill_load`` / ``skill_resource`` dispatch.
#: The one allowed literal is the bundled package prefix (bundled versus installed: supply and removal).
ALLOWED_SKILL_LITERALS = frozenset({'agentos/'})
SKILL_COLLECTIONS = frozenset({'entries', 'loaded'})


def _skill_identity(node):
    """Whether ``node`` reads a skill's identity: ``x['skill']``, ``x.get('skill')`` or a binding's entries/loaded."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Constant) and sub.slice.value == 'skill':
            return True
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == 'get'
                and sub.args and isinstance(sub.args[0], ast.Constant) and sub.args[0].value == 'skill'):
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in SKILL_COLLECTIONS:
            return True
    return False


def _string_literals(node):
    return [sub.value for sub in ast.walk(node) if isinstance(sub, ast.Constant) and isinstance(sub.value, str)]


def skill_selections(paths):
    """``(module, line, literal)`` where core compares, matches or indexes a skill identity by a literal."""
    hits = []
    for path in paths:
        if path.name == 'skills.py':
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'), filename=str(path))):
            literals = []
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                if any(_skill_identity(item) for item in operands):
                    literals = [value for item in operands if not _skill_identity(item) for value in _string_literals(item)]
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MATCH_CALLS
                  and _skill_identity(node.func.value)):
                literals = [value for arg in node.args for value in _string_literals(arg)]
            elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                  and node.value.attr in SKILL_COLLECTIONS):
                literals = _string_literals(node.slice)
            hits += [(path.name, node.lineno, value) for value in literals if value not in ALLOWED_SKILL_LITERALS]
    return hits


def skill_text_loads(paths):
    """``(module, function, line)`` of each call that loads skill text: ``<...skills or binding>.load/resource(...)``."""
    hits = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for function in [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(function):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ('load', 'resource')):
                    continue
                owner = node.func.value
                label = owner.attr if isinstance(owner, ast.Attribute) else owner.id if isinstance(owner, ast.Name) else ''
                if 'skill' in label.lower() or 'binding' in label.lower():
                    hits.append((path.name, function.name, node.lineno))
    return sorted(set(hits))


class CoreSelectsNoSkill(unittest.TestCase):
    """#974/#975: no core special case for any skill, bundled or installed, and one way in for its text."""

    def test_no_skill_identity_is_compared_with_a_literal(self):
        self.assertEqual(skill_selections(sorted(SRC.rglob('*.py'))), [])

    def test_skill_text_is_loaded_only_by_the_model_tool_dispatch(self):
        loads = skill_text_loads(sorted(path for path in SRC.rglob('*.py') if path.name != 'skills.py'))
        self.assertEqual({(module, function) for module, function, _line in loads}, {('agent_runtime.py', 'execute')})

    def test_guards_catch_planted_selections_and_loads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'planted.py'
            path.write_text(textwrap.dedent("""
                def route(row, binding, request):
                    if row['skill'] == 'internal-comms/internal-comms':
                        pass
                    if row.get('skill', '').startswith('shop-'):
                        pass
                    entry = binding.entries['some-pkg/any-skill']
                    if row['skill'].startswith('agentos/'):
                        pass  # the bundled package prefix: allowed
                    return binding.load('some-pkg/any-skill')
            """))
            selections = sorted(literal for _module, _line, literal in skill_selections([path]))
            loads = skill_text_loads([path])
        self.assertEqual(selections, ['internal-comms/internal-comms', 'shop-', 'some-pkg/any-skill'])
        self.assertEqual([(module, function) for module, function, _line in loads], [('planted.py', 'route')])


if __name__ == '__main__':
    unittest.main()


class SyntheticQualificationSkillCanary(unittest.TestCase):
    def test_only_the_synthetic_write_is_exempt_and_a_read_is_still_caught(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cli_qualification.py'
            path.write_text("def populate_codex_home(home):\n    (home / 'SKILL.md').write_text('synthetic canary')\n")
            self.assertEqual(skill_file_references([path]), [])
            path.write_text(path.read_text() + "    return (home / 'SKILL.md').read_text()\n")
            self.assertEqual([hit[2] for hit in skill_file_references([path])], ['SKILL.md'])
