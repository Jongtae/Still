"""Fresh held-out pairs for #919 and #921 gap 1, written by Claude; v4 and the dev additions stay immutable.

The v4 held-out cases were read by PR-8 and later reruns, so under #827 they no longer
support a claim. This layer adds new target rows and new held-out cases that no run has
seen. Composition (``recall_regression.compose``): these rows come first, so they are the
oldest and the request-only profile fill cuts them like the v4 targets; the cases replace
the base held-out cases, and the dev cases are unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'recall-heldout-919-1.json'
NOTE = '[author:claude] [fresh] [cut] Older target outside the newest candidate window and alphabetical fill.'
PARTICLE = '[author:claude] [fresh] [particle] [cut] The query word carries a particle; the stored noun is bare.'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# (case id, kind, query, context, target ref, target content, note); None target: harm control.
CASES = [
    # Cross-language, Korean request -> English fact.
    ('h5c-quillrest', 'cross-language', '깃펜받침대 펠트덧댐 감싸개 종류?', '',
     't5c-quillrest', 'Quillrest cradle: its felt pad stays wrapped in linen tape.', NOTE),
    ('h5c-loupe', 'cross-language', '보석감정용 확대경알 청소천?', '',
     't5c-loupe', 'Jeweler loupe lens gets a microfiber wipe before every appraisal.', NOTE),
    ('h5c-darning', 'cross-language', '양말깁기용 나무알 넣어두는 깡통?', '',
     't5c-darning', 'Darning egg sits in the mending tin beside spare buttons.', NOTE),
    ('h5c-chalkline', 'cross-language', '먹줄통 분필가루 보충시점?', '',
     't5c-chalkline', 'Chalk line reel gets refilled with blue chalk after each framing job.', NOTE),
    # Cross-language, English request -> Korean fact.
    ('h5c-onggi', 'cross-language', 'Earthenware crock breathing pores winter oiling?', '',
     't5c-onggi', '옹기단지 숨구멍은 겨울 전에 들기름으로 문질러 둔다.', NOTE),
    ('h5c-hanji', 'cross-language', 'Mulberry lampshade tear patching paste?', '',
     't5c-hanji', '한지등갓 찢어진 틈은 쌀풀로 덧붙인다.', NOTE),
    ('h5c-bamboomat', 'cross-language', 'Bamboo summer sleeping slat grime removal?', '',
     't5c-bamboomat', '대자리 결 사이 먼지는 칫솔로 쓸어낸다.', NOTE),
    ('h5c-brassbowl', 'cross-language', 'Brassware tarnish polishing grit?', '',
     't5c-brassbowl', '놋그릇 얼룩은 기왓장 가루로 문질러 광을 낸다.', NOTE),
    # Particle: the request word carries a particle; the stored noun is bare.
    ('h5c-whetstone', 'paraphrase', '숫돌을 평평하게 되살리기?', '',
     't5c-whetstone', '숫돌 평면은 사포 위에 물을 뿌리고 원을 그리며 고른다.', PARTICLE),
    ('h5c-strawmat', 'paraphrase', '맷방석이 해어지면 보수재료?', '',
     't5c-strawmat', '맷방석 짚 올이 풀리면 삼끈으로 엮어 맨다.', PARTICLE),
    ('h5c-brazier', 'paraphrase', '화로에서 꺼낸 잿가루 처리법?', '',
     't5c-brazier', '화로 잿더미는 완전히 식은 뒤 철제 통에 모은다.', PARTICLE),
    ('h5c-mortar', 'paraphrase', '절구에 찧고 공이건조법?', '',
     't5c-mortar', '절구 공이는 쓰고 나면 거꾸로 세워 말린다.', PARTICLE),
    ('h5c-kettle', 'paraphrase', '주전자에 하얗게 낀 침전물 세척제?', '',
     't5c-kettle', '주전자 바닥 물때는 구연산 가루로 불려 닦는다.', PARTICLE),
    ('h5c-barrow', 'paraphrase', '외바퀴수레로 짐 옮기기 앞서 바람점검주기?', '',
     't5c-barrow', '외바퀴수레 바람통은 두 주마다 확인한다.', PARTICLE),
    # Paraphrase: the request describes the thing; the fact uses its usual name (same language).
    ('h5c-hourglass', 'paraphrase', '잘록한 유리관 알갱이 낙하식 시간측정기 위치?', '',
     't5c-hourglass', '모래시계는 삼 분짜리로 부엌 창가에 둔다.', NOTE),
    ('h5c-pedometer', 'paraphrase', '걸음수 헤아리는 소형기계 착용부위?', '',
     't5c-pedometer', '만보기는 주머니가 아니라 허리띠 왼쪽에 찬다.', NOTE),
    ('h5c-colander', 'paraphrase', 'Pasta draining perforated bowl hanging spot?', '',
     't5c-colander', 'Colander hangs from hooks above our stove.', NOTE),
    ('h5c-thermos', 'paraphrase', 'Vacuum bottle gasket swap timing?', '',
     't5c-thermos', 'Thermos flask silicone seal gets replaced each autumn.', NOTE),
    ('h5c-ironboard', 'paraphrase', '옷주름 펴는 받침대 천커버 세탁시기?', '',
     't5c-ironboard', '다리미판 커버천은 계절마다 세탁기로 빤다.', NOTE),
    ('h5c-dehumid', 'paraphrase', 'Moisture extracting appliance reservoir dumping hour?', '',
     't5c-dehumid', 'Dehumidifier bucket gets emptied before bedtime.', NOTE),
    # Short follow-ups: the context names the referent without the answer.
    ('h5c-knitting', 'short-follow-up', '그건 어떤식으로 보관했었지?', '뜨개질 연장 얘기로 돌아가자.',
     't5c-knitting', '털실 바구니 속 대바늘은 굵기별로 노끈으로 묶어 둔다.', NOTE),
    ('h5c-snorkel', 'short-follow-up', 'Elastic tightness while packing?', 'Underwater goggles again.',
     't5c-snorkel', 'Snorkel mask band: loosen two notches prior to storing.', NOTE),
    # Controls.
    ('h5c-pickleball', 'lexical-control', 'pickleball edgeguard', '',
     't5c-pickleball', 'Pickleball edgeguard gets checked weekly.',
     '[author:claude] [fresh] Lexical control: the request words are in the fact.'),
    ('h5c-lantern', 'harm-control', 'favorite lantern hue?', '',
     None, 'Lantern wick was trimmed after the last camping trip.',
     '[author:claude] [fresh] [incidental-noise] Harm control: a lexical lure that does not answer the question.'),
]


def build():
    base = json.loads((ROOT / 'recall-pairs-v4.json').read_text())
    additions = json.loads((ROOT / 'recall-gate-dev-1.json').read_text())
    rows, cases = [], []
    for index, (ident, kind, query, context, ref, content, note) in enumerate(CASES):
        lure = ref is None
        ref = ref or 't5c-' + ident.split('-', 1)[1]
        rows.append(dict(ref=ref, key=f'profile.v5.zq.k{index:02}', content=content))
        case = dict(id=ident, split='heldout', kind=kind, query=query,
                    needed=[] if lure else [ref], traps=[], note=note)
        if context:
            case['context'] = context
        if lure:
            case['irrelevant'] = [ref]
        cases.append(case)
    return dict(version='recall-heldout/1', base_digest=digest(base), additions_digest=digest(additions),
                rows=rows, cases=cases)


def render():
    return (json.dumps(build(), ensure_ascii=False, indent=2) + '\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail if the committed file differs')
    args = parser.parse_args()
    data = render()
    if args.check:
        if OUTPUT.read_bytes() != data:
            raise SystemExit('recall-heldout-919-1.json differs from build_heldout_919.py')
        return
    OUTPUT.write_bytes(data)


if __name__ == '__main__':
    main()
