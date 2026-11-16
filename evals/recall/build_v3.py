"""Build the synthetic R1 v3 fixture, without models, network or case-driven filler.

Reuse review: N/A for data/content generation and fixture assertions: no new
runtime component, abstraction, dependency or retrieval case/branch. Adopt
stdlib JSON/path/argparse and the existing v2 row schema. The hand-authored
subjects below are invented. Filler consumes only its ordinal, never labels.
"""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / 'recall-pairs-v3.json'

# Eight CURRENT siblings per subject, in explicit handwritten order. Members
# have contrasting attributes; cases must discriminate, not just name a domain.
CLUSTERS = [
    ('rail', [
        '청동 증기 기관차 윤활 주기는 열이틀이며 축에는 소량만 바른다.',
        '은빛 전기 기관차 윤활 주기는 스무날이며 축에는 소량만 바른다.',
        '검정 디젤 기관차 윤활 주기는 아흐레며 축에는 소량만 바른다.',
        '하양 화물 기관차 윤활 주기는 보름이며 축에는 소량만 바른다.',
        '빨강 급행 기관차 윤활 주기는 엿새며 축에는 소량만 바른다.',
        '초록 산악 기관차 윤활 주기는 한달이며 축에는 소량만 바른다.',
        '파랑 입환 기관차 윤활 주기는 나흘이며 축에는 소량만 바른다.',
        '노랑 관광 기관차 윤활 주기는 이레며 축에는 소량만 바른다.',
    ], 'superseded', '청동 증기 기관차 윤활 주기는 사흘이며 축에는 소량만 바른다.'),
    ('mallets', [
        'Percussion mallet with felt crown belongs inside velvet sleeve after rehearsal.',
        'Percussion mallet with rubber crown belongs inside linen sleeve after rehearsal.',
        'Percussion mallet with cork crown belongs inside canvas sleeve after rehearsal.',
        'Percussion mallet with yarn crown belongs inside cotton sleeve after rehearsal.',
        'Percussion mallet with leather crown belongs inside wool sleeve after rehearsal.',
        'Percussion mallet with maple crown belongs inside denim sleeve after rehearsal.',
        'Percussion mallet with brass crown belongs inside satin sleeve after rehearsal.',
        'Percussion mallet with nylon crown belongs inside fleece sleeve after rehearsal.',
    ], 'corrected', 'Percussion mallet with felt crown belongs inside burlap sleeve after rehearsal.'),
    ('tide', [
        '조위계 삼각 표식 센서 보정값은 영점에서 삼밀리 상승으로 설정한다.',
        '조위계 원형 표식 센서 보정값은 영점에서 오밀리 하강으로 설정한다.',
        '조위계 사각 표식 센서 보정값은 영점에서 이밀리 상승으로 설정한다.',
        '조위계 별모양 표식 센서 보정값은 영점에서 칠밀리 하강으로 설정한다.',
        '조위계 마름모 표식 센서 보정값은 영점에서 육밀리 상승으로 설정한다.',
        '조위계 십자 표식 센서 보정값은 영점에서 팔밀리 하강으로 설정한다.',
        '조위계 초승달 표식 센서 보정값은 영점에서 사밀리 상승으로 설정한다.',
        '조위계 타원 표식 센서 보정값은 영점에서 일밀리 하강으로 설정한다.',
    ], 'expired', '조위계 삼각 표식 센서 보정값은 영점에서 십밀리 하강으로 설정한다.'),
    ('slides', [
        'Microscope specimen slide bearing oak tissue needs violet dye for ninety seconds.',
        'Microscope specimen slide bearing onion tissue needs amber dye for sixty seconds.',
        'Microscope specimen slide bearing fern tissue needs indigo dye for forty seconds.',
        'Microscope specimen slide bearing moss tissue needs scarlet dye for fifty seconds.',
        'Microscope specimen slide bearing pine tissue needs ochre dye for eighty seconds.',
        'Microscope specimen slide bearing birch tissue needs azure dye for thirty seconds.',
        'Microscope specimen slide bearing reed tissue needs crimson dye for seventy seconds.',
        'Microscope specimen slide bearing elm tissue needs sepia dye for twenty seconds.',
    ], 'superseded', 'Microscope specimen slide bearing oak tissue needs green dye for ten seconds.'),
    ('puppets', [
        '인형극 늑대 배역 꼭두각시 조종줄 장력은 느슨하게 맞춰 둔다.',
        '인형극 여우 배역 꼭두각시 조종줄 장력은 팽팽하게 맞춰 둔다.',
        '인형극 토끼 배역 꼭두각시 조종줄 장력은 중간으로 맞춰 둔다.',
        '인형극 사슴 배역 꼭두각시 조종줄 장력은 약하게 맞춰 둔다.',
        '인형극 곰 배역 꼭두각시 조종줄 장력은 강하게 맞춰 둔다.',
        '인형극 수달 배역 꼭두각시 조종줄 장력은 부드럽게 맞춰 둔다.',
        '인형극 오소리 배역 꼭두각시 조종줄 장력은 단단하게 맞춰 둔다.',
        '인형극 족제비 배역 꼭두각시 조종줄 장력은 유연하게 맞춰 둔다.',
    ], 'corrected', '인형극 늑대 배역 꼭두각시 조종줄 장력은 아주 강하게 맞춰 둔다.'),
    ('cylinders', [
        'Diving cylinder carrying chequered band receives hydrostatic inspection each February.',
        'Diving cylinder carrying striped band receives hydrostatic inspection each August.',
        'Diving cylinder carrying dotted band receives hydrostatic inspection each January.',
        'Diving cylinder carrying plain band receives hydrostatic inspection each March.',
        'Diving cylinder carrying zigzag band receives hydrostatic inspection each April.',
        'Diving cylinder carrying spiral band receives hydrostatic inspection each June.',
        'Diving cylinder carrying floral band receives hydrostatic inspection each July.',
        'Diving cylinder carrying diamond band receives hydrostatic inspection each October.',
    ], 'expired', 'Diving cylinder carrying chequered band receives hydrostatic inspection each December.'),
]

# (split, subject, paraphrase, cross-language). No query spells the stored
# distinguishing attribute. Translation directions alternate by storage language.
CLUSTER_CASES = [
    ('dev', 'rail', '구릿빛 미니어처 기차에 기름칠할 빈도?',
     'Silvery electric toy locomotive lubrication interval?'),
    ('dev', 'mallets', 'Matted fibre headed beater protective pouch material?',
     '고무 머리 타악기 채 싸개 재질?'),
    ('heldout', 'tide', '세모 달린 바닷물 수위 측정기 영점편차?',
     'Circular marked seawater level probe calibration offset?'),
    ('heldout', 'slides', 'Acorn tree cellular preparation colouring duration?',
     '양파 조직 현미경 유리판 염색 몇초간?'),
    ('heldout', 'puppets', '무대에서 이리 맡은 줄인형 당기는 힘 정도?',
     'Fox character marionette cord tautness setting?'),
    ('heldout', 'cylinders', 'Chessboard patterned scuba tank pressure certification month?',
     '줄무늬 띠 잠수용 공기통 수압검사 시기?'),
]

# Standalone targets are intentionally older than the candidate window AND
# alphabetically after the filler. Contexts establish referents without answers.
# (split, facet, kind, slug, content, query, optional preceding owner turn)
TARGETS = [
    ('dev', 'cut', 'cross-language', 'harp',
     '휴대용 하프 운반 덮개 안감은 두툼한 솜으로 골라 두었다.',
     'Portable harp transport cover padding preference?', ''),
    ('dev', 'cut', 'cross-language', 'metronome',
     'Mechanical metronome rests upright inside padded cabinet between practice sessions.',
     '태엽 박자기 넣어둘 자세?', ''),
    ('dev', 'cut', 'paraphrase', 'chess',
     '체스 기보 복기할 때 실수한 수에는 보라색 동그라미를 그린다.',
     '장기 비슷한 서양놀이 대국일지 검토하다 착오 표시법?', ''),
    ('dev', 'cut', 'short-follow-up', 'loom',
     '직조기 날실 간격은 손가락 두마디 너비로 고정해 두었다.',
     '치수 좀 재확인해봐.', '베틀 세로줄 폭에 관해 얘기 중이야.'),
    ('dev', 'cut', 'short-follow-up', 'compass',
     '항해용 나침반 방위 보정은 출항하기 직전에 수행한다.',
     '시점만 짚어줘.', '뱃길 방향계 오차 상쇄 건이야.'),
    ('heldout', 'cut', 'cross-language', 'bellows',
     '풀무 가죽 주름은 매년 초여름에 밀랍으로 얇게 손질한다.',
     'Bellows pleat conditioning schedule?', ''),
    ('heldout', 'cut', 'cross-language', 'chisel',
     '목공 끌 날 각도는 이십오 도로 유지하며 연마석은 젖혀 둔다.',
     'Woodworking chisel bevel inclination?', ''),
    ('heldout', 'cut', 'cross-language', 'sextant',
     'Sextant mirror alignment happens ashore before every overnight passage.',
     '육분의 반사경 정렬 수행지?', ''),
    ('heldout', 'cut', 'cross-language', 'barometer',
     'Aneroid barometer reference altitude stays fixed at harbor elevation.',
     '기압계 참조 고도 맞춤값?', ''),
    ('heldout', 'cut', 'paraphrase', 'sundial',
     '해시계 그림자 바늘은 진북 쪽으로 돌려 놓아야 읽기 편하다.',
     '햇빛으로 시각 재는 장치 뾰족한 부분 향할 나침방향?', ''),
    ('heldout', 'cut', 'paraphrase', 'abacus',
     '수판 셈을 마친 뒤에는 모든 구슬을 아래쪽으로 몰아 둔다.',
     '주산 계산 끝나면 알맹이 정돈 어떻게?', ''),
    ('heldout', 'cut', 'paraphrase', 'press',
     '판화 압착 롤러 높이는 동판 두께보다 조금 넓게 조정한다.',
     '에칭 찍어내는 원통 틈새 넓이 결정법?', ''),
    ('heldout', 'cut', 'short-follow-up', 'spindle',
     '물레 회전 속도는 초벌 성형 단계에서 가장 느리게 유지한다.',
     '빠르기는?', '찰흙 빚는 돌림판 얘길 이어갈게.'),
    ('heldout', 'cut', 'short-follow-up', 'darkroom',
     '암실 인화지 노광 시간은 시험 조각 기준 여덟 초로 정했다.',
     '몇초였지?', '필름 현상방 감광 종이에 광선 쬐던 공정 이야기였어.'),
    ('heldout', 'cut', 'short-follow-up', 'lure',
     '낚시 미끼 모형 바늘에는 이동 전에 코르크 마개를 끼운다.',
     '안전조치는?', '민물고기 유인하는 가짜 벌레 뾰족한 갈고리 이야기야.'),
    ('dev', 'particle', 'paraphrase', 'sieve',
     '거름체 세척 뒤에는 손잡이를 위로 걸어 완전히 건조한다.',
     '거름체를 헹군 직후 말리는 요령?', ''),
    ('heldout', 'particle', 'paraphrase', 'plumb',
     '다림추 보관할 때 끈은 느슨하게 감아서 작은 함에 넣는다.',
     '다림추를 사용했던 이후 정리 절차?', ''),
    ('heldout', 'particle', 'paraphrase', 'crucible',
     '도가니 내부 잔재는 완전히 식힌 다음 부드러운 솔로 털어낸다.',
     '도가니에서 남은 찌꺼기 제거 순서?', ''),
    ('dev', 'lexical', 'lexical-control', 'tuning',
     '튜닝쐐기솔은 연습실 작은 서랍에 따로 넣어 보관한다.', '튜닝쐐기솔', ''),
    ('heldout', 'lexical', 'lexical-control', 'astrolabe',
     'Astrolabequill instrument rests beneath folded cotton in navigation chest.', 'Astrolabequill', ''),
    ('heldout', 'lexical', 'lexical-control', 'forge',
     '단조집게함은 불꽃이 닿지 않는 작업대 끝에 두고 사용한다.', '단조집게함', ''),
    ('dev', 'harm', 'harm-control', 'timbre',
     '음색연습 공책은 박자별로 나누어 파란색 칸에 기록한다.',
     '음색연습 띄어쓰기 판단해줘.', ''),
    ('heldout', 'harm', 'harm-control', 'fulcrum',
     '지렛점 노트는 공구대 오른편의 얕은 칸에 눕혀 보관한다.',
     '지렛점 낱말 어원 풀이해봐.', ''),
    ('heldout', 'harm', 'harm-control', 'vacuum',
     '편광필터 수납집은 차광 천으로 감싸서 촬영 가방에 넣는다.',
     '진공에서 열전달 원리 설명해.', '편광필터 얘기는 접었어.'),
]


def filler_rows(count):
    """Pure ordinal templates: diverse reusable facts, no case or split access.

    Cyclic coprime indexing varies object/attribute/value combinations, and the
    ordinal distinguishes otherwise similar items in an invented collection.
    """
    ko_objects = ['여행 수첩', '우편 엽서', '자수 도안', '양초 받침', '도장 수집첩',
                  '유리 문진', '공예 리본', '사진 앨범', '종이 봉투', '천 조각',
                  '금속 책갈피', '지도 사본', '나무 상자', '열쇠 고리', '색연필 통',
                  '연극 대본', '배지 모음', '직물 견본', '장식 조약돌', '접이 부채']
    ko_places = ['다락 선반', '복도 장', '공예방 서랍', '현관 바구니', '침실 장식장',
                 '독서방 궤짝', '별채 수납장', '서재 칸막이', '계단 밑 함']
    en_objects = ['postage album', 'crochet sampler', 'wax seal', 'travel journal',
                  'enamel badge', 'fabric swatch', 'glass paperweight', 'ribbon spool',
                  'wooden puzzle', 'folding fan', 'pencil tray', 'scrapbook',
                  'brass bookmark', 'linen napkin', 'ceramic coaster', 'map facsimile',
                  'souvenir pebble', 'felt pennant', 'postcard bundle', 'lace sample']
    en_places = ['attic shelf', 'hallway cupboard', 'studio drawer', 'entry basket',
                 'bedroom dresser', 'reading chest', 'annex cabinet', 'study divider',
                 'stairwell box']
    for i in range(count):
        j = i // 2
        if i % 2 == 0:
            content = (f'수집번호 {j:04d} {ko_objects[j % 20]} 묶음은 '
                       f'{ko_places[(j * 7) % 9]} 안쪽에 보관하며 분기마다 먼지를 닦는다.')
        else:
            content = (f'Collection {j:04d} {en_objects[j % 20]} stays inside '
                       f'{en_places[(j * 7) % 9]}; dust gently every quarter.')
        yield dict(ref=f'f3-{i:04d}', key=f'profile.v3.aa.f{i:04d}', content=content)


def build():
    v2 = json.loads((HERE / 'recall-pairs-v2.json').read_text(encoding='utf-8'))
    rows, cases = list(v2['rows']), []
    for ordinal, (split, facet, kind, slug, content, query, context) in enumerate(TARGETS):
        ref = f't3-{slug}'
        # Opaque keys prevent English translations leaking through indexed keys.
        rows.append(dict(ref=ref, key=f'profile.v3.zzcut.t{ordinal:02d}', content=content))
        note = f'[{facet}] ' if facet in {'cut', 'particle'} else ''
        if slug == 'vacuum':
            note += '[misleading-context] '
        note += {'cut': 'Older target outside the newest candidate window and alphabetical fill.',
                 'particle': 'Reverse prefix only: inflected query token is longer than the stored noun.',
                 'lexical': 'Exact unique stored word is a request-only lexical control.',
                 'harm': 'Word analysis or science explanation needs no personal Memory.'}[facet]
        case = dict(id=f'{"d3" if split == "dev" else "h3"}-{slug}', split=split,
                    kind=kind, query=query, needed=[] if facet == 'harm' else [ref],
                    traps=[], note=note)
        if facet == 'harm':
            case['irrelevant'] = [ref]
        if context:
            case['context'] = context
        cases.append(case)
    cluster_rows = []
    for slug, contents, exclusion, old_content in CLUSTERS:
        trap = f'c3-{slug}-old'
        key = f'profile.v3.bb.{slug}.m0'
        old = dict(ref=trap, key=key, content=old_content)
        if exclusion == 'expired':
            old.update(key=f'profile.v3.bb.{slug}.expired', valid_until='2026-03-01')
        cluster_rows.append(old)
        for i, content in enumerate(contents):
            row = dict(ref=f'c3-{slug}-{i}', key=f'profile.v3.bb.{slug}.m{i}', content=content)
            if i == 0 and exclusion != 'expired':
                row['after'] = trap
                if exclusion == 'corrected':
                    row['corrected'] = True
            cluster_rows.append(row)
    # Keep clusters recent but behind aa filler in the unmatched alphabetical
    # fill. Their visibility is measured, rather than assumed, in the tests.
    rows.extend(filler_rows(1000 - len(rows) - len(cluster_rows)))
    rows.extend(cluster_rows)
    for split, slug, para, cross in CLUSTER_CASES:
        for i, (kind, query) in enumerate([('paraphrase', para), ('cross-language', cross)]):
            cases.append(dict(
                id=f'{"d3" if split == "dev" else "h3"}-{slug}-{i}', split=split,
                kind=kind, query=query, needed=[f'c3-{slug}-{i}'],
                irrelevant=[f'c3-{slug}-{j}' for j in range(8) if j != i],
                traps=[f'c3-{slug}-old'],
                note='[cluster] [near-trap] Attribute paraphrase/translation distinguishes one of eight current siblings; stale sibling is ineligible.'))
    cases.sort(key=lambda case: case['split'] != 'dev')  # stable, no token sorting
    return dict(version='recall-pairs/3', owner='synthetic-recall-owner',
                today='2026-03-25', rows=rows, cases=cases)


def render():
    return (json.dumps(build(), ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='compare bytes without writing')
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != expected:
            parser.exit(1, 'v3 fixture differs; regenerate with python evals/recall/build_v3.py\n')
        print('v3 fixture is byte-identical')
    else:
        OUTPUT.write_bytes(expected)


if __name__ == '__main__':
    main()
