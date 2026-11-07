"""One register throughout fixed messages: soft 해요체 / です・ます."""
import json
import re
from pathlib import Path


CATALOG = Path(__file__).parents[1] / 'src/personal_agent/messages.json'
FORMAL_KO = re.compile(r'[가-힣](?:니다|니까)')
PLAIN_KO = re.compile(r'[다어아야지해네래자니냐게걸까][.?!](?=\s|[\'"”’」』)\]}〉》】]|$)')
PLAIN_JA = re.compile(r'である|だ[。！？]')


def register_violations(locale, text):
    """Return the offending text, including forms inside multi-line messages."""
    if locale == 'ko':
        formal = [match.group() for match in FORMAL_KO.finditer(text)
                  if (ord(match.group()[0]) - 0xAC00) % 28 == 17]
        return (formal + re.findall(r'십시오|말씀', text)
                + PLAIN_KO.findall(text))
    if locale == 'ja':
        return PLAIN_JA.findall(text)
    raise ValueError(locale)


def test_register_checker_examples():
    for text in ('확인했습니다.', '확인했습니다: 결과', '맞습니까?', '오십시오.', '말씀해 주세요.',
                 '했다.', '했어?', '가자! ”', '“그래야 해.”', '첫 줄이에요.\n한다.'):
        assert register_violations('ko', text), text
    for text in ('확인했어요.', '곧 시작할게요.', '할까요?', '연결', '파일.zip'):
        assert not register_violations('ko', text), text
    for text in ('そうだ。', 'そうだ！', 'そうだ？', 'これは例である', '例である。'):
        assert register_violations('ja', text), text
    assert not register_violations('ja', '確認しました。')


def test_catalog_uses_one_register():
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    violations = [f'{message_id} [{locale}]: {matched!r}'
                  for message_id, translations in catalog.items()
                  for locale in ('ko', 'ja')
                  for matched in register_violations(locale, translations[locale])]
    assert not violations, ('One register: soft 해요체 / です・ます; '
                            'no Korean formal/honorific/plain endings or Japanese だ・である.\n'
                            + '\n'.join(violations))
