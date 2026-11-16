"""Eval-only bounded-snapshot observations; never an R1 preference criterion.

Adapt the real MemoryService snapshot via a scoped search seam; adopt stdlib
mock and the existing canonical eligibility API. No bounds are reimplemented.
"""
import re
from unittest.mock import patch

from personal_agent.memory_service import MemoryService


def facets(case):
    leading = re.match(r'(?:\[[^]]+\]\s*)*', case['note'])[0]
    return re.findall(r'\[([^]]+)\]', leading)


def measure(store, data, case, returned):
    """Count all needed entries shown, including unmatched alphabetical fill."""
    service = MemoryService(store, private_read_sink=MemoryService.NO_EGRESS_GUARD)
    service._today = lambda: data['today']
    definitions = {row['ref']: row for row in data['rows']}
    needed = {(definitions[ref]['key'], definitions[ref]['content']): ref for ref in case['needed']}

    def describe(snapshot):
        shown = sorted({needed[(row['memory_key'], row['content'])] for row in snapshot['entries']
                        if (row['memory_key'], row['content']) in needed})
        return dict(needed_in_snapshot=len(shown), needed_total=len(case['needed']),
                    needed_refs=shown, snapshot_omitted=snapshot['omitted_count'],
                    snapshot_shown=len(snapshot['entries']))

    reference = service.profile_snapshot(data['owner'], query=case['query'][:500])
    matched = []
    for ident in dict.fromkeys(returned):
        row = store.memory(ident, owner_id=data['owner'], current_only=False)
        if row and row['state'] == 'current' and service.holds(row, data['today']) and service.is_profile_key(row['memory_key']):
            matched.append(row)
    # This affects only this service instance during the diagnostic call.
    with patch.object(service, 'search_memories', return_value={'memories': matched}):
        snapshot = service.profile_snapshot(data['owner'], query=case['query'][:500])
    return dict(id=case['id'], split=case['split'], kind=case['kind'], facets=facets(case),
                **describe(snapshot), request_only=describe(reference))


def summarize(rows):
    def totals(group, reference=False):
        values = [row['request_only'] if reference else row for row in group]
        return dict(cases=len(values), needed_in_snapshot=sum(r['needed_in_snapshot'] for r in values),
                    needed_total=sum(r['needed_total'] for r in values),
                    snapshot_omitted=sum(r['snapshot_omitted'] for r in values))

    scores = {}
    for split in sorted({row['split'] for row in rows}):
        group = [row for row in rows if row['split'] == split]
        scores[split] = dict(total=totals(group), request_only=totals(group, True),
                            by_kind={kind: totals([r for r in group if r['kind'] == kind])
                                     for kind in sorted({r['kind'] for r in group})},
                            by_facet={facet: totals([r for r in group if facet in r['facets']])
                                      for facet in sorted({f for r in group for f in r['facets']})})
    return dict(cases=rows, scores=scores, diagnostic=True, verdict=None)
