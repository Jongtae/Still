"""One declaration of a format the AI writes and Still validates (HARDEN-GENERIC-01 #1445 part 3).

The JSON Schema the AI reads (``settings_read``'s ``format``) and the validator that accepts or
refuses its draft are the same pydantic model, so a rule cannot live in the validator and be
missing from the format text (three live #1380 defects were exactly that).  Only mechanical
differences are normalized before validation (name case and separators, a host given with a
scheme, a missing label or use); anything that needs a judgment is refused with the exact item
and reason, so the AI can correct its draft.

pydantic is already in the base install through ``mcp-types`` (MIT); this module adds no
dependency closure.  It owns formats only: secret redaction and authority checks stay with
their callers.
"""
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, ValidationInfo, model_validator

from . import api_requests
from .api_credentials import FIELD, MAX_FIELDS, MAX_VALUE_CHARS

#: Longest owner-visible label of one secret-set value.
MAX_FIELD_LABEL = 80
FIELD_NAME = f'^{FIELD.pattern}$'
HOST_PATTERN = r'^[a-z0-9][a-z0-9.-]{0,252}(:[0-9]{1,5})?$'
READ_PATTERN = r'^(POST|PUT|PATCH) [a-z0-9.-]{1,253}(:[0-9]{1,5})?/[^\s?#]{0,300}$'


class FormatError(ValueError):
    """A draft that does not match its format; the message names each item and reason."""


def _read_text(item):
    if not isinstance(item, str):
        return item
    text = re.sub(r'\s+https?://', ' ', ' '.join(item.split()), count=1)
    method, _, rest = text.partition(' ')
    return f'{method.upper()} {rest}'


def _hosts(value):
    items = [part for part in re.split(r'[\s,]+', value) if part] if isinstance(value, str) else value
    if not isinstance(items, list):
        return items
    out = []
    for item in items:
        host = re.sub(r'^[a-z]+://', '', item.strip().lower()).split('/', 1)[0] if isinstance(item, str) else item
        if host not in out:
            out.append(host)
    return out


def _public(hosts):
    for host in hosts:
        if not api_requests.public_host(host):
            raise ValueError(f'호스트 {host}: 내부 주소나 IP 주소는 쓸 수 없어요. 서비스의 공개 도메인을 주세요.')
    return hosts


Host = Annotated[str, Field(pattern=HOST_PATTERN, max_length=259)]


class SecretField(BaseModel):
    """One value the owner enters on the one-time input page; code_run sees it as ``<SET>_<NAME>``."""
    model_config = ConfigDict(strict=True, extra='ignore')

    name: str = Field(pattern=FIELD_NAME, description='UPPER_SNAKE value name; a lower-case or hyphenated one is upper-cased')
    label: str = Field(default='', max_length=MAX_FIELD_LABEL,
                       description='what the owner enters, shown on the input page; defaults to the name')
    use: Literal[api_requests.FIELD_USES] = Field(
        default='header_or_body',
        description="header_or_body: masked; Still substitutes it only in headers and bodies sent to the set's hosts. "
                    'in_code: the real value inside the sandbox, for URL queries or signing; the run may then reach '
                    'only these hosts')

    @model_validator(mode='before')
    @classmethod
    def _mechanical(cls, item):
        if isinstance(item, str):
            item = {'name': item}
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            return item
        name = re.sub(r'[\s-]+', '_', item['name'].strip()).upper()
        return {'name': name, 'label': ' '.join(str(item.get('label') or name).split()),
                'use': item.get('use') or 'header_or_body'}


class CodeValues(BaseModel):
    """The values and reviewed reads of a code-run secret set, checked against the set's hosts."""
    model_config = ConfigDict(strict=True, extra='ignore')

    fields: list[SecretField] = Field(
        min_length=1, max_length=MAX_FIELDS,
        description='every value the requests need that only the owner can give, not only secrets: keys and the '
                    "owner's own identifiers the requests carry; to add one to an existing set, draft that set again "
                    'with its hosts, money flag and fields unchanged plus the new one, and the owner enters only the '
                    'new value. '
                    f'Each value is 1-{MAX_VALUE_CHARS} characters on one line; a value shorter than 8 characters '
                    'is not redacted from output')
    reads: list[Annotated[str, Field(pattern=READ_PATTERN), BeforeValidator(_read_text)]] = Field(
        default_factory=list, max_length=api_requests.MAX_READ_REQUESTS,
        description='"METHOD host/path" (POST, PUT or PATCH, no query) of non-GET requests that only read, for '
                    "example a token request; each host is one of the set's hosts; the owner reviews them on "
                    'confirmation')

    def _checked(self, hosts):
        names = [item.name for item in self.fields]
        if len(set(names)) != len(names):
            raise ValueError('fields의 name이 서로 달라야 합니다.')
        for index, item in enumerate(self.reads):
            host = item.split(' ', 1)[1].split('/', 1)[0]
            if host not in hosts:
                raise ValueError(f'reads[{index}]: 호스트 {host}가 이 묶음의 hosts에 없습니다.')
        self.reads = sorted(set(self.reads))
        return self

    @model_validator(mode='after')
    def _against_hosts(self, info: ValidationInfo):
        if type(self) is CodeValues:
            return self._checked((info.context or {}).get('hosts') or ())
        return self


class CodeSetDraft(CodeValues):
    """A code-run secret set (#1380): named values the owner enters, used only by code_run."""
    model_config = ConfigDict(strict=True, extra='forbid')

    name: str = Field(pattern=api_requests.NAME.pattern, max_length=40,
                      description='lower-case letters, digits and hyphens; spaces and underscores become hyphens')
    hosts: list[Host] = Field(min_length=1, max_length=8,
                              description='public domain names with an optional port; no IP address, localhost or '
                                          'private name; a scheme or path is dropped')
    money: bool = Field(default=True, description='whether money can move through this connection')
    note: str = Field(default='', max_length=api_requests.MAX_NOTE_CHARS)

    @model_validator(mode='before')
    @classmethod
    def _mechanical(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if isinstance(data.get('name'), str):
            data['name'] = re.sub(r'[\s_]+', '-', data['name'].strip().lower())
        if 'hosts' in data:
            data['hosts'] = _hosts(data['hosts'])
        return data

    @model_validator(mode='after')
    def _against_own_hosts(self):
        _public(self.hosts)
        return self._checked(self.hosts)


def _location(loc):
    out = ''
    for part in loc:
        out += f'[{part}]' if isinstance(part, int) else (f'.{part}' if out else str(part))
    return out


def refusal(model, exc):
    """One line per problem of a ``ValidationError``, naming the item; unknown keys list the allowed ones."""
    lines, extra = [], []
    for error in exc.errors(include_url=False):
        if error['type'] == 'extra_forbidden' and len(error['loc']) == 1:
            extra.append(str(error['loc'][0]))
            continue
        message = re.sub(r'^Value error, ', '', error['msg'])
        where = _location(error['loc'])
        value = error.get('input')
        shown = f' "{str(value)[:40]}"' if isinstance(value, (str, int, float)) and error['type'] != 'value_error' else ''
        lines.append(f'{where}{shown}: {message}' if where else message)
    if extra:
        lines.insert(0, f'쓸 수 없는 키: {", ".join(sorted(extra))} ({", ".join(model.model_fields)}만 씁니다)')
    return '; '.join(lines)


def validate(model, data, **context):
    """``model`` validated from ``data``, or ``FormatError`` naming every problem."""
    try:
        return model.model_validate(data, context=context or None)
    except ValidationError as exc:
        raise FormatError(refusal(model, exc)) from None


def schema_text(model):
    """The compact JSON Schema of ``model`` the AI reads as the format (titles dropped)."""
    def strip(node):
        if isinstance(node, dict):
            return {key: strip(value) for key, value in node.items() if key != 'title'}
        return [strip(item) for item in node] if isinstance(node, list) else node
    return json.dumps(strip(model.model_json_schema()), ensure_ascii=False, separators=(',', ':'))
