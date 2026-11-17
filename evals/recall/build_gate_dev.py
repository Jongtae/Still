"""Deterministic dev-only additions; v4 remains an immutable input."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'recall-gate-dev-1.json'


def build():
    base = json.loads((ROOT/'recall-pairs-v4.json').read_text())
    rows = [
        dict(ref='gate-second-row',key='profile.gatedev.qaa',content='Celesta casing: ivory felt cushions the hinged lid.'),
        dict(ref='gate-saturated-row',key='profile.gatedev.qbb',content='Octant cradle: cork cradles protect the brass arc.'),
        dict(ref='gate-noise-row',key='profile.gatedev.qcc',content='Marquetry veneer inventory: rosewood offcuts occupy the shallow tray.'),
        dict(ref='gate-annotated-row',key='profile.gatedev.qdd',content='도편수첩 보관 기록은 자주색 비단으로 감싼 작은 함에 남겼다.'),
    ]
    rows += [dict(ref=f'gate-fill-{i}',key=f'profile.gatedev.x{i:02}',
                  content=f'쐐깃덧널궤짝 표찰묶음 {i:02}번은 톱밥완충겹판 윗칸쐐기함쐐깃덧널궤짝.') for i in range(24)]
    def case(ident,kind,query,needed,irrelevant,note):
        return dict(id=ident,split='dev',kind=kind,query=query,needed=needed,traps=[],irrelevant=irrelevant,note=note)
    cases = [
        case('gate-second','paraphrase','건반타격상자의 덮개 선택?', ['gate-second-row'], [], '[second-query] A translated second selector may recover the omitted celesta fact.'),
        case('gate-saturated','paraphrase','팔분각도계의 수납 선택?', ['gate-saturated-row'], [], '[cut] The target precedes all v4 rows and falls beyond saturated request-only A.'),
        case('gate-noise','harm-control','권양기축의 윤활점도?', [], ['gate-noise-row'], '[incidental-noise] A marquetry selector would add only unrelated noise.'),
        case('gate-annotated','lexical-control','도편수첩', ['gate-annotated-row'], [], '[annotation] Add one synthetic upkeep annotation after the plan; the row was already in A.'),
    ]
    raw = json.dumps(base,sort_keys=True,ensure_ascii=False).encode()
    return dict(version='recall-gate-dev/1',base_digest=hashlib.sha256(raw).hexdigest(),rows=rows,cases=cases)


def render():
    return (json.dumps(build(),ensure_ascii=False,indent=2)+'\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args = parser.parse_args()
    if args.check:
        if OUTPUT.read_bytes() != render():
            raise SystemExit('dev additions differ')
        print('byte-identical')
    else:
        OUTPUT.write_bytes(render())


if __name__ == '__main__':
    main()
