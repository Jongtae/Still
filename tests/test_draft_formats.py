"""#1445 part 3: the format the AI reads and the validator are one declaration.

Three live #1380 defects were rules the validator enforced but the format text never stated
(an unknown key, a port, a short value).  The format is now the validator's own JSON Schema;
these tests pin that the text carries it and that the schema and the validator agree on
drafts in canonical form.
"""
import json
import unittest

from personal_agent import draft_formats
from personal_agent.api_requests import FIELD_USES, MAX_NOTE_CHARS
from personal_agent.draft_formats import CodeSetDraft, FormatError, schema_text, validate
from personal_agent.settings_orchestrator import SettingsOrchestrator

try:
    import jsonschema
except ImportError:  # optional extra ``schema-validation``
    jsonschema = None

HOST = 'api.example.com:9443'
GOOD = {'name': 'example-broker', 'hosts': [HOST], 'money': True, 'note': 'Read-only account data',
        'fields': [{'name': 'APP_KEY', 'label': 'App key', 'use': 'header_or_body'},
                   {'name': 'ACCOUNT', 'label': 'Account number', 'use': 'in_code'}],
        'reads': [f'POST {HOST}/oauth2/token']}
#: Canonical-form drafts each breaking one rule the schema states.
BAD = {
    'unknown key': {**GOOD, 'endpoint': 'x'},
    'money as text': {**GOOD, 'money': 'true'},
    'long note': {**GOOD, 'note': 'x' * (MAX_NOTE_CHARS + 1)},
    'no fields': {**GOOD, 'fields': []},
    'nine fields': {**GOOD, 'fields': [{'name': f'K{i}', 'label': 'x', 'use': 'in_code'} for i in range(9)]},
    'field name': {**GOOD, 'fields': [{'name': '1BAD', 'label': 'x', 'use': 'in_code'}]},
    'field use': {**GOOD, 'fields': [{'name': 'K', 'label': 'x', 'use': 'query'}]},
    'long label': {**GOOD, 'fields': [{'name': 'K', 'label': 'x' * 81, 'use': 'in_code'}]},
    'GET read': {**GOOD, 'reads': [f'GET {HOST}/v1']},
    'read with query': {**GOOD, 'reads': [f'POST {HOST}/token?x=1']},
    'no hosts': {**GOOD, 'hosts': []},
    'host form': {**GOOD, 'hosts': ['api_example.com:9443'], 'reads': []},
    'set name': {**GOOD, 'name': 'example.broker'},
}


class OneDeclaration(unittest.TestCase):
    def test_the_format_text_carries_the_validators_schema(self):
        orchestrator = SettingsOrchestrator.__new__(SettingsOrchestrator)
        orchestrator._row = lambda *args, **extra: extra
        text = orchestrator._api_connections()['connect']['format']
        self.assertIn(schema_text(CodeSetDraft), text)
        schema = json.loads(schema_text(CodeSetDraft))
        self.assertFalse(schema['additionalProperties'], 'an unknown key is stated, not only refused')
        self.assertEqual(schema['$defs']['SecretField']['properties']['use']['enum'], list(FIELD_USES))
        self.assertIn(':', schema['properties']['hosts']['items']['pattern'], 'a port is part of the stated host form')

    def test_canonical_drafts_validate_and_each_broken_rule_is_refused_by_name(self):
        self.assertEqual(validate(CodeSetDraft, GOOD).model_dump(), GOOD)
        for name, draft in BAD.items():
            with self.subTest(name), self.assertRaises(FormatError):
                validate(CodeSetDraft, draft)

    @unittest.skipIf(jsonschema is None, 'jsonschema (schema-validation extra) is not installed')
    def test_the_schema_the_ai_reads_agrees_with_the_validator(self):
        checker = jsonschema.Draft202012Validator(json.loads(schema_text(CodeSetDraft)))
        self.assertEqual(list(checker.iter_errors(GOOD)), [])
        for name, draft in BAD.items():
            with self.subTest(name):
                self.assertTrue(list(checker.iter_errors(draft)), f'{name}: the validator refuses what the schema allows')

    def test_rules_across_keys_are_stated_in_the_schema_text(self):
        # Rules no JSON Schema keyword expresses are stated in the descriptions the AI reads.
        text = schema_text(CodeSetDraft)
        for rule in ("each host is one of the set's hosts", 'no IP address, localhost or private name',
                     f'1-{draft_formats.MAX_VALUE_CHARS} characters'):
            self.assertIn(rule, text)
        for draft, expected in (({**GOOD, 'reads': ['POST other.example.net/token']}, 'other.example.net'),
                                ({**GOOD, 'hosts': ['127.0.0.1:8080']}, '내부 주소'),
                                ({**GOOD, 'fields': GOOD['fields'] + GOOD['fields'][:1]}, 'name')):
            with self.subTest(expected), self.assertRaises(FormatError) as caught:
                validate(CodeSetDraft, draft)
            self.assertIn(expected, str(caught.exception))

    def test_an_unknown_key_refusal_lists_the_allowed_keys(self):
        with self.assertRaises(FormatError) as caught:
            validate(CodeSetDraft, {**GOOD, 'endpoint': 'x', 'probe': {}})
        message = str(caught.exception)
        self.assertIn('endpoint, probe', message)
        for key in CodeSetDraft.model_fields:
            self.assertIn(key, message)


if __name__ == '__main__':
    unittest.main()
