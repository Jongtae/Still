"""Build the synthetic R1 v4 fixture without models, embeddings or network.

Reuse review: N/A for data/content generation and fixture assertions: no new
runtime component, abstraction, dependency or retrieval case/branch. Stdlib
only; adopt JSON/path/argparse and the existing v3 row/case schema. All new
subjects are invented; v3 is read unchanged, never regenerated or rewritten.
"""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / 'recall-pairs-v4.json'

# Eight current siblings and one historical near-trap per new subject.
CLUSTERS = [
    ('cultures', [
        '표고 종균 배지의 수분율 목표는 칠십 퍼센트로 유지한다.',
        '느타리 종균 배지의 수분율 목표는 육십 퍼센트로 유지한다.',
        '팽이 종균 배지의 수분율 목표는 오십오 퍼센트로 유지한다.',
        '목이 종균 배지의 수분율 목표는 팔십 퍼센트로 유지한다.',
        '새송이 종균 배지의 수분율 목표는 육십오 퍼센트로 유지한다.',
        '양송이 종균 배지의 수분율 목표는 칠십오 퍼센트로 유지한다.',
        '만가닥 종균 배지의 수분율 목표는 오십 퍼센트로 유지한다.',
        '노루궁뎅이 종균 배지의 수분율 목표는 팔십오 퍼센트로 유지한다.',
    ], 'superseded', '표고 종균 배지의 수분율 목표는 사십 퍼센트로 유지한다.'),
    ('meteorites', [
        'Meteorite exhibiting chondrules requires desiccant renewal every fortnight.',
        'Meteorite exhibiting iron alloy requires desiccant renewal every week.',
        'Meteorite exhibiting olivine crystals requires desiccant renewal every third week.',
        'Meteorite exhibiting basaltic crust requires desiccant renewal every fourth week.',
        'Meteorite exhibiting carbon inclusions requires desiccant renewal every fifth week.',
        'Meteorite exhibiting nickel veins requires desiccant renewal every sixth week.',
        'Meteorite exhibiting brecciated fragments requires desiccant renewal every seventh week.',
        'Meteorite exhibiting glassy fusion requires desiccant renewal every eighth week.',
    ], 'corrected', 'Meteorite exhibiting chondrules requires desiccant renewal every day.'),
    ('cheeses', [
        '치즈 껍질이 갈라진 덩이는 숙성고 온도를 십이 도로 맞춘다.',
        '치즈 껍질이 주름진 덩이는 숙성고 온도를 십사 도로 맞춘다.',
        '치즈 껍질이 밀랍칠된 덩이는 숙성고 온도를 구 도로 맞춘다.',
        '치즈 껍질이 소금씻긴 덩이는 숙성고 온도를 십 도로 맞춘다.',
        '치즈 껍질이 잿빛인 덩이는 숙성고 온도를 십일 도로 맞춘다.',
        '치즈 껍질이 솜털덮인 덩이는 숙성고 온도를 팔 도로 맞춘다.',
        '치즈 껍질이 허브박힌 덩이는 숙성고 온도를 십삼 도로 맞춘다.',
        '치즈 껍질이 매끈한 덩이는 숙성고 온도를 칠 도로 맞춘다.',
    ], 'expired', '치즈 껍질이 갈라진 덩이는 숙성고 온도를 이십 도로 맞춘다.'),
    ('relays', [
        'Telegraph relay with single contact bank has actuation lag eleven milliseconds.',
        'Telegraph relay with double contact bank has actuation lag fourteen milliseconds.',
        'Telegraph relay with triple contact bank has actuation lag seventeen milliseconds.',
        'Telegraph relay with quadruple contact bank has actuation lag nineteen milliseconds.',
        'Telegraph relay with quintuple contact bank has actuation lag twenty milliseconds.',
        'Telegraph relay with sextuple contact bank has actuation lag twentyone milliseconds.',
        'Telegraph relay with septuple contact bank has actuation lag twentytwo milliseconds.',
        'Telegraph relay with octuple contact bank has actuation lag twentythree milliseconds.',
    ], 'superseded', 'Telegraph relay with single contact bank has actuation lag fifty milliseconds.'),
]

# Each paraphrase selects sibling 0; each translation selects sibling 1.
CLUSTER_CASES = [
    ('cultures', '동고로 말려 먹는 균류 배양실 습윤 수치?',
     'Oyster mushroom spawn substrate moisture percentage?'),
    ('meteorites', 'Space rock containing primordial spherules humidity absorber replacement cadence?',
     '철합금 드러난 운석 방습제 교체 간격이?'),
    ('cheeses', '발효 유제품 덩어리 겉면 균열난 개체 숙성방 섭씨값?',
     'Wrinkled rind fermented dairy ripening chamber temperature?'),
    ('relays', 'Morse switch engaging solitary terminal array latency?',
     '이중 접점 전신 중계기 작동 지연량?'),
]

# Standalone rows are inserted before v3's filler. Opaque ordinal keys do not
# expose subjects; contexts establish referents without supplying answers.
# (facet, kind, slug, content, query, optional preceding owner turn)
TARGETS = [
    ('cut', 'cross-language', 'saddle',
     '승마 안장 복대의 버클은 왼편에서 두번째 구멍에 체결한다.',
     'Equestrian saddle girth fastening position?', ''),
    ('cut', 'cross-language', 'origami',
     '종이접기 학의 마지막 접힘은 날개 끝을 바깥쪽으로 젖혀 마무리한다.',
     'Origami crane final crease orientation?', ''),
    ('cut', 'cross-language', 'snowshoes',
     'Snowshoe bindings tighten until a fingertip can slip beneath each strap.',
     '설피 결속구 조이는 한도?', ''),
    ('cut', 'cross-language', 'roaster',
     'Coffee roasting drum preheating target remains two hundred degrees Celsius.',
     '커피 볶음통 예열 목표온도?', ''),
    ('cut', 'paraphrase', 'greenhouse',
     '온실 환기창 개방 기준은 실내 온도 이십팔 도 이상으로 정했다.',
     '투명 재배실 열기 빼는 덧창 열어두는 조건?', ''),
    ('cut', 'paraphrase', 'foil',
     'Fencing foil blade curvature must remain below eight millimetres during equipment checks.',
     'Competitive thrust sword bend allowance?', ''),
    ('cut', 'paraphrase', 'watercolour',
     '수채화 팔레트 혼색 칸은 그림을 끝낸 즉시 미지근한 물로 씻는다.',
     '투명 물감 섞는 접시 청소 적기는?', ''),
    ('cut', 'short-follow-up', 'lacrosse',
     '라크로스 스틱 그물망 깊이는 공 윗면이 테두리보다 높게 오도록 한다.',
     '깊숙함은?', '십자봉 구기 종목의 포획용 실주머니 건으로 돌아가자.'),
    ('cut', 'short-follow-up', 'hammock',
     '해먹 양끝 지지대 사이 거리는 몸길이의 두배로 확보해 둔다.',
     '서로 떨어뜨릴 폭은?', '매달아 누워 쉬는 그네 양단 버팀목에 대한 논의야.'),
    ('cut', 'short-follow-up', 'zither',
     '치터 공명판 균열 검사는 겨울 난방을 켜기 직전마다 진행한다.',
     '언제였더라?', '납작한 발현악기 울림통 금갔는지 살피던 얘기.'),
    ('particle', 'paraphrase', 'thimble',
     '골무 안쪽 땀기는 마른 헝겊으로 훔친 뒤 통풍시켜 없앤다.',
     '골무를 끼웠다가 벗으면 축축함 없애려면?', ''),
    ('particle', 'paraphrase', 'inkwell',
     '잉크병 주둥이 굳은 액체는 따뜻하게 적신 면봉으로 닦아낸다.',
     '잉크병에서 입구 딱딱해진 방울 처리하려면?', ''),
    ('lexical', 'lexical-control', 'gyroscope',
     'Gyrospindlecase latches shut before its rotating instrument travels anywhere.',
     'Gyrospindlecase', ''),
    ('lexical', 'lexical-control', 'embosser',
     '압인꽃판함은 눌러찍기 행사마다 손수레 하단에 싣고 간다.',
     '압인꽃판함', ''),
    ('harm', 'harm-control', 'syntax',
     '보어연습 문제집은 문법 수업 가는 날마다 가방 앞칸에 챙긴다.',
     '보어연습 문장성분 분석해줄래?', ''),
    ('harm', 'harm-control', 'eclipse',
     '축음기덮개는 재생이 끝나면 먼지가 앉지 않도록 닫아 둔다.',
     '월식 발생하는 천체 배치 알려줘.', '축음기덮개 이야긴 그만하자.'),
]


# Written by Claude (Anthropic) outside the GPT family, for the Doc2Query-- shared-vocabulary check (#921)
CLAUDE_CLUSTERS = [('kx',
  ['청자 유약 찻잔은 첫 사용 전 쌀뜨물에 사흘 담가 둔다.',
   '백자 유약 찻잔은 첫 사용 전 쌀뜨물에 하루 담가 둔다.',
   '분청 유약 찻잔은 첫 사용 전 쌀뜨물에 이틀 담가 둔다.',
   '흑유 유약 찻잔은 첫 사용 전 쌀뜨물에 나흘 담가 둔다.',
   '천목 유약 찻잔은 첫 사용 전 쌀뜨물에 닷새 담가 둔다.',
   '진사 유약 찻잔은 첫 사용 전 쌀뜨물에 엿새 담가 둔다.',
   '재유 유약 찻잔은 첫 사용 전 쌀뜨물에 이레 담가 둔다.',
   '녹유 유약 찻잔은 첫 사용 전 쌀뜨물에 여드레 담가 둔다.'],
  'corrected',
  '청자 유약 찻잔은 첫 사용 전 쌀뜨물에 열흘 담가 둔다.'),
 ('ky',
  ['Ridge tent with cotton canvas fly needs reproofing spray every second season.',
   'Ridge tent with silnylon fly needs reproofing spray every third season.',
   'Ridge tent with polyester fly needs reproofing spray every fourth season.',
   'Ridge tent with dyneema fly needs reproofing spray every fifth season.',
   'Ridge tent with ripstop fly needs reproofing spray every sixth season.',
   'Ridge tent with laminated fly needs reproofing spray every season.',
   'Ridge tent with vinyl fly needs reproofing spray every seventh season.',
   'Ridge tent with acrylic fly needs reproofing spray every eighth season.'],
  'superseded',
  'Ridge tent with cotton canvas fly needs reproofing spray every tenth season.')]

CLAUDE_CLUSTER_CASES = [('kx',
  '청록 비색 사기 찻그릇 길들이기 곡물 불림 일수?',
  'White porcelain glazed teacup, rice starch bath, soak length?'),
 ('ky',
  'Woven natural textile outer sheet, triangular shelter, waterproofing frequency?',
  '실리콘 입힌 나일론 겉천 쓰는 맞배형 텐트 방수제 갱신 텀?')]

CLAUDE_TARGETS = [('cut',
  'cross-language',
  'daegeum',
  '대금 취구는 연주가 끝나면 알코올 솜으로 닦아 말린다.',
  'Bamboo transverse flute embouchure cleaning method?',
  ''),
 ('cut',
  'cross-language',
  'sourdough',
  'Sourdough starter jar gets fed twice daily with equal weights of rye and water.',
  '천연 발효종 단지에 밥 주는 횟수?',
  ''),
 ('cut',
  'paraphrase',
  'sansevieria',
  '현관 화분의 산세베리아는 흙이 완전히 마르면 한 컵만 물을 준다.',
  '입구 근처 뱀 혀 닮은 다육 식물 관수 기준이 뭐였지?',
  ''),
 ('cut',
  'paraphrase',
  'kayak',
  'Kayak spray skirt rinses in fresh water and dries inside out after each paddle.',
  'Narrow canoe cockpit apron postpaddling upkeep?',
  ''),
 ('cut',
  'short-follow-up',
  'drill',
  '전동 드릴 배터리는 한 달에 한 번 절반까지 충전해 보관한다.',
  '어느 선까지 담아 둬?',
  '천공 연장 전원팩 얘기.'),
 ('cut',
  'short-follow-up',
  'fountainpen',
  'Fountain pen nib gets flushed with lukewarm water whenever the ink colour changes.',
  'Flushing cadence?',
  'Refillable writing tool, metal tip, again.'),
 ('particle', 'paraphrase', 'millstone', '맷돌 손잡이 나무못은 장마철마다 들기름을 먹여 둔다.', '맷돌을 돌리는 막대 습기 관리는?', ''),
 ('harm',
  'harm-control',
  'lightning',
  '번개모양 책갈피는 소설책 마지막 장 사이에 꽂아 둔다.',
  '번개모양 구름이 생기는 과학적 이유는?',
  '')]


def build():
    v3 = json.loads((HERE / 'recall-pairs-v3.json').read_text(encoding='utf-8'))
    rows = list(v3['rows'])
    cases = [dict(case, split='dev') for case in v3['cases']]
    standalone = []
    targets = [('gpt', 't4', f't{ordinal:02d}', target) for ordinal, target in enumerate(TARGETS)]
    targets += [('claude', 't4c', f'u{ordinal:02d}', target)
                for ordinal, target in enumerate(CLAUDE_TARGETS)]
    for author, prefix, ordinal, (facet, kind, slug, content, query, context) in targets:
        ref = f'{prefix}-{slug}'
        standalone.append(dict(ref=ref, key=f'profile.v4.zzcut.{ordinal}', content=content))
        note = f'[author:{author}] '
        note += f'[{facet}] ' if facet in {'cut', 'particle'} else ''
        if slug == 'eclipse':
            note += '[misleading-context] '
        note += {'cut': 'Older target outside the newest candidate window and alphabetical fill.',
                 'particle': 'Reverse prefix only: inflected query token is longer than the stored noun.',
                 'lexical': 'Exact unique stored word is a request-only lexical control.',
                 'harm': 'Grammar or astronomy explanation needs no personal Memory.'}[facet]
        case = dict(id=f'h4{"c" if author == "claude" else ""}-{slug}', split='heldout', kind=kind, query=query,
                    needed=[] if facet == 'harm' else [ref], traps=[], note=note)
        if facet == 'harm':
            case['irrelevant'] = [ref]
        if context:
            case['context'] = context
        cases.append(case)
    insertion = next(i for i, row in enumerate(rows) if row['ref'].startswith('f3-'))
    rows[insertion:insertion] = standalone
    clusters = [('c4', cluster) for cluster in CLUSTERS]
    clusters += [('c4c', cluster) for cluster in CLAUDE_CLUSTERS]
    for prefix, (slug, contents, exclusion, old_content) in clusters:
        trap = f'{prefix}-{slug}-old'
        old = dict(ref=trap, key=f'profile.v4.bb.{slug}.m0', content=old_content)
        if exclusion == 'expired':
            old.update(key=f'profile.v4.bb.{slug}.expired', valid_until='2026-03-01')
        rows.append(old)
        for i, content in enumerate(contents):
            row = dict(ref=f'{prefix}-{slug}-{i}', key=f'profile.v4.bb.{slug}.m{i}', content=content)
            if i == 0 and exclusion != 'expired':
                row['after'] = trap
                if exclusion == 'corrected':
                    row['corrected'] = True
            rows.append(row)
    cluster_cases = [('gpt', 'h4', 'c4', cluster) for cluster in CLUSTER_CASES]
    cluster_cases += [('claude', 'h4c', 'c4c', cluster) for cluster in CLAUDE_CLUSTER_CASES]
    for author, case_prefix, row_prefix, (slug, para, cross) in cluster_cases:
        for i, (kind, query) in enumerate([('paraphrase', para), ('cross-language', cross)]):
            cases.append(dict(
                id=f'{case_prefix}-{slug}-{i}', split='heldout', kind=kind, query=query,
                needed=[f'{row_prefix}-{slug}-{i}'],
                irrelevant=[f'{row_prefix}-{slug}-{j}' for j in range(8) if j != i],
                traps=[f'{row_prefix}-{slug}-old'],
                note=f'[author:{author}] [cluster] [near-trap] Attribute paraphrase/translation distinguishes one of eight current siblings; stale sibling is ineligible.'))
    return dict(version='recall-pairs/4', owner=v3['owner'], today=v3['today'], rows=rows, cases=cases)


def render():
    return (json.dumps(build(), ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='compare bytes without writing')
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != expected:
            parser.exit(1, 'v4 fixture differs; regenerate with python evals/recall/build_v4.py\n')
        print('v4 fixture is byte-identical')
    else:
        OUTPUT.write_bytes(expected)


if __name__ == '__main__':
    main()
