"""Binding #819 credential table: shape/context detection, never entropy alone."""
import time
import unittest
from credential_cases_819 import KNOWN_LIMITS, SHOULD_MASK, SHOULD_NOT_MASK
from personal_agent.bounded_execution import SECRET_PATTERN


class CredentialPattern819(unittest.TestCase):
    def test_should_mask(self):
        for case_id, text, credential in SHOULD_MASK:
            with self.subTest(case_id=case_id):
                self.assertNotIn(credential, SECRET_PATTERN.sub('[redacted]', text))

    def test_should_not_mask(self):
        for case_id, text, unchanged in SHOULD_NOT_MASK:
            with self.subTest(case_id=case_id):
                self.assertEqual(unchanged, SECRET_PATTERN.sub('[redacted]', text))

    def test_prose_punctuation_is_not_a_credential_shape(self):
        for text in ('The secret: butter.', 'Password: Enter instructions.',
                     'Basic $9', 'Authorization: Pending review.'):
            with self.subTest(text=text):
                self.assertEqual(SECRET_PATTERN.sub('[redacted]', text), text)


def _best_time(text, runs=3):
    best = None
    for _ in range(runs):
        start = time.perf_counter()
        SECRET_PATTERN.sub('[redacted]', text)
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    return best


def test_the_pattern_is_linear_on_hostile_input():
    """Growth, not wall-clock: an 8x longer input takes about 8x longer when the scan is linear and about
    64x when it backtracks quadratically.  A fixed time bound flaked on a loaded CI runner (#839)."""
    small, large = 25_000, 200_000
    seeds = {
        'hyphen chain': 'a-',
        'kebab chain': 'lorem-ipsum-',
        'curl commands': 'curl ',
        'mysql commands': 'mysql ',
        'dot chain': 'a.',
        'unterminated PEM': '-----BEGIN PRIVATE KEY-----\n',
        'label and word': 'token word ',
    }
    for name, seed in seeds.items():
        texts = [(seed * (size // len(seed) + 1))[:size] for size in (small, large)]
        short, long = (_best_time(text) for text in texts)
        ratio = long / max(short, 1e-4)
        assert ratio < 24, f'{name}: {long:.3f}s for {large} vs {short:.3f}s for {small} characters ({ratio:.1f}x)'
        # A catastrophic pattern takes minutes; this backstop only catches that, whatever the runner's load.
        assert long < 5.0, f'{name}: {long:.3f}s for {large} characters'
    json_line = '{"message": "' + 'curl ' * 1342 + 'x' * (large - 6725) + '"}'
    assert _best_time(json_line, runs=1) < 5.0


def test_command_redaction_preserves_surrounding_text():
    for text, expected in (
        ('run mysql -u root -phunter2 db', 'run mysql -u root -p[redacted] db'),
        ('curl -u alice:hunter2 https://example.com', 'curl -u [redacted] https://example.com'),
        ('machine api.example.com login alice password Zx9Qw3Er5Ty7',
         'machine api.example.com login alice password [redacted]'),
    ):
        assert SECRET_PATTERN.sub('[redacted]', text) == expected


def test_known_limits_document_conflicting_original_shapes():
    for (case_id, text, credential), reason in KNOWN_LIMITS:
        assert reason, case_id
        assert credential in SECRET_PATTERN.sub('[redacted]', text), case_id
