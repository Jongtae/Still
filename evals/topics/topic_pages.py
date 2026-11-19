"""Pure page operations, chronological replay, and experiment scoring."""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from hashlib import sha256
import math
import random
from statistics import mean


PAGE_CHARS = 2500
MIN_GAIN = 0.09
SECTIONS = ('standard', 'decision', 'state')
HEADINGS = ('standards (the owner\'s words):', 'decisions:', 'latest state:')


class LeakError(Exception):
    pass


def new_page(topic_id, name, summary):
    return {'topic_id': topic_id, 'name': name, 'summary': summary, 'items': []}


def compact(text):
    return ' '.join(text.split())


def short_ref(work_id):
    return work_id[:8]


def check_support(verdict, support, context):
    if verdict == 'no':
        return verdict, False
    span = compact(support)
    if span and span in compact(context):
        return verdict, False
    return {'yes': 'partial', 'partial': 'no'}[verdict], True


def _nonempty(text):
    return isinstance(text, str) and bool(text.strip())


def _apply_op(page, op, sources, order):
    if not isinstance(op, dict) or op.get('op') not in ('add', 'update', 'remove'):
        return 'bad_op'
    kind = op['op']
    item_id = op.get('id')
    if kind == 'add' and op.get('section') not in SECTIONS:
        return 'bad_op'
    if kind != 'add' and not _nonempty(item_id):
        return 'bad_op'
    if not _nonempty(op.get('reason' if kind == 'remove' else 'text')):
        return 'bad_op'
    if kind != 'remove':
        thread = op.get('thread', '')
        expires = op.get('expires', '')
        if not isinstance(thread, str) or not isinstance(expires, str):
            return 'bad_op'
        if expires:
            try:
                if len(expires) != 10 or date.fromisoformat(expires).isoformat() != expires:
                    return 'bad_op'
            except ValueError:
                return 'bad_op'

    source = op.get('source')
    if source is None or (isinstance(source, str) and not source.strip()):
        return 'missing_source'
    if not isinstance(source, str) or source not in sources:
        return 'unknown_source'
    item = next((item for item in page['items'] if item['id'] == item_id), None)
    if kind != 'add' and item is None:
        return 'unknown_item'

    section = op['section'] if kind == 'add' else item['section']
    raw_quote = op.get('quote')
    quote = compact(raw_quote) if isinstance(raw_quote, str) else None
    if ((kind != 'remove' and section == 'standard') or 'quote' in op) and (
        not quote or quote not in compact(sources[source]['owner_text'])
    ):
        return 'quote_not_verbatim'

    day = sources[source]['day']
    if kind == 'remove':
        page['items'].remove(item)
        page.setdefault('removed', []).append({
            'id': item_id, 'day': day, 'source': source, 'reason': op['reason'],
        })
        return None

    if kind == 'add':
        key = f"{page['topic_id']}|{section}|{compact(op['text']).casefold()}|{source}"
        item_id = 'i' + sha256(key.encode()).hexdigest()[:8]
        if any(item['id'] == item_id for item in page['items']):
            return 'bad_op'
        item = {'id': item_id, 'section': section, 'history': []}
        page['items'].append(item)
    item.update(text=op['text'], quote=quote, source=source, day=day,
                thread=compact(thread) or item.get('thread'), expires=expires or item.get('expires'))
    item['history'].append({'op': kind, 'day': day, 'source': source, 'order': order})
    return None


def apply_answer(page, answer, *, day, sources):
    page = deepcopy(page)
    report = {'applied': 0, 'refused': []}
    ops = answer.get('ops') if isinstance(answer, dict) else None
    if not isinstance(ops, list):
        rewrite = isinstance(answer, str) or (
            isinstance(answer, dict) and bool(answer.keys() & {'page', 'items', 'text', 'markdown'})
        )
        report['refused'].append({'index': None, 'code': 'rewrite' if rewrite else 'bad_op'})
        return page, report

    existing = {item['id'] for item in page['items']}
    targeted = {
        op['id'] for op in ops
        if isinstance(op, dict) and op.get('op') in ('update', 'remove')
        and isinstance(op.get('id'), str) and op['id'] in existing
    }
    if len(targeted) > max(2, math.ceil(len(existing) / 2)):
        report['refused'].append({'index': None, 'code': 'rewrite'})
        return page, report
    # Same-day updates must be ordered across items, even if their list positions are unchanged.
    latest_order = max((item['history'][-1].get('order', 0) for item in page['items'] if item['history']), default=0)
    for index, op in enumerate(ops):
        code = _apply_op(page, op, sources, latest_order + index + 1)
        if code:
            report['refused'].append({'index': index, 'code': code})
        else:
            report['applied'] += 1
    return page, report


def _item_line(item, include_ids):
    ref = f"work:{short_ref(item['source'])}"
    text = compact(item['text'])
    if include_ids:
        text = f"[{item['id']}] {text}"
    if item['section'] == 'state':
        details = [f"as of {item['day']}"]
        if item.get('expires'):
            details.append(f"until {item['expires']}")
        if include_ids and item.get('thread'):
            details.append(f"thread: {item['thread']}")
        details.append(ref)
        if item['quote']:
            text = f'"{item["quote"]}" — {text}'
        return f'- {text} ({", ".join(details)})'
    if item['quote']:
        return f'- "{item["quote"]}" — {text} ({ref}, {item["day"]})'
    return f'- [{item["day"]}] {text} ({ref})'


def _recency(positioned_item):
    position, item = positioned_item
    latest = item['history'][-1] if item['history'] else {}
    return item['day'], latest.get('day', ''), latest.get('order', 0), position


def render(page, budget=PAGE_CHARS, *, include_ids=False, as_of=None):
    name, summary = compact(page['name']), compact(page['summary'])
    header = (
        f'topic_page "{name}" — {summary} (from earlier conversations; '
        'each item cites its source Work):'
    )
    lines = [header, *HEADINGS]
    items, threads, hidden = [], set(), 0
    for _, item in sorted(enumerate(page['items']), key=_recency, reverse=True):
        if as_of is not None and item['section'] == 'state':
            thread = compact(item.get('thread') or '').casefold()
            superseded = thread in threads
            if thread:
                # Expiry of the newest item does not revive the state it replaced.
                threads.add(thread)
            if superseded or (item.get('expires') and item['expires'] < as_of):
                hidden += 1
                continue
        items.append(item)
    items.sort(key=lambda item: SECTIONS.index(item['section']))

    notice = []
    if hidden:
        noun = 'item' if hidden == 1 else 'items'
        notice.append(f'{hidden} older state {noun} not shown (expired or superseded).')
    omitted = len(items)

    def footers(count):
        return [*notice, f'{count} more items omitted by the context limit.'] if count else notice

    reserved = sum(len(line) + 1 for line in footers(omitted))
    while lines and len('\n'.join(lines)) + reserved > budget:
        lines.pop()
    if not lines:
        rendered = '\n'.join(footers(omitted))
        if len(rendered) <= budget:
            return rendered
        return next((line for line in footers(omitted) if len(line) <= budget), '')

    used = len('\n'.join(lines))
    selected = defaultdict(list)
    for item in items:
        if HEADINGS[SECTIONS.index(item['section'])] not in lines:
            continue
        line = _item_line(item, include_ids)
        remaining = sum(len(line) + 1 for line in footers(omitted - 1))
        if used + len(line) + 1 + remaining <= budget:
            selected[item['section']].append(line)
            used += len(line) + 1
            omitted -= 1
    rendered = [lines[0]]
    for section, heading in zip(SECTIONS, lines[1:]):
        rendered.extend([heading, *selected[section]])
    rendered.extend(footers(omitted))
    return '\n'.join(rendered)


def replay(topics, works, judge, *, until=None):
    return _replay(topics, works, judge, until=until)


def _replay(topics, works, judge, *, until=None, resume=None, checkpoint=None, fatal_errors=(),
            retry=None, max_retries=None):
    result = resume if resume is not None else {
        'snapshots': {topic['id']: [] for topic in topics}, 'refused': {}, 'applied': 0, 'calls': 0,
    }
    snapshots = result['snapshots']
    pages = {
        topic['id']: snapshots[topic['id']][-1][1] if snapshots[topic['id']]
        else new_page(topic['id'], topic['name'], topic['summary'])
        for topic in topics
    }
    done = {day for history in snapshots.values() for day, _ in history}
    days = defaultdict(lambda: defaultdict(list))
    for work in works:
        if work['day'] not in done and (until is None or work['day'] < until):
            for topic_id in dict.fromkeys(work['topics']):
                if topic_id in pages:
                    days[work['day']][topic_id].append(work)

    refused = Counter(result['refused'])
    if retry:
        result.setdefault('retries', 0)

    def attempt(day, active, call, *args):
        payload = {
            topic_id: {'page': deepcopy(pages[topic_id]), 'rendered': render(pages[topic_id]),
                       'works': deepcopy(day_works)}
            for topic_id, day_works in active.items()
        }
        try:
            answers = call(day, payload, *args)
        except fatal_errors:
            raise
        except Exception:
            refused['judge_error'] += 1
            result['calls'] += 1
            return dict.fromkeys(active, 'judge_error'), {}
        result['calls'] += 1
        rejected, accepted = {}, {}
        for topic_id, day_works in active.items():
            answer = answers.get(topic_id) if isinstance(answers, dict) else None
            sources = {work['id']: work for work in day_works}
            page, report = apply_answer(pages[topic_id], answer, day=day, sources=sources)
            result['applied'] += report['applied']
            for refusal in report['refused']:
                refused[refusal['code']] += 1
                if refusal['index'] is None:
                    rejected[topic_id] = refusal['code']
            if topic_id not in rejected:
                accepted[topic_id] = page
        pages.update(accepted)
        return rejected, accepted

    for day, active in sorted(days.items()):
        pending = result.get('pending_retry')
        if isinstance(pending, dict):
            rejected = pending['topics']
            pages.update(pending['accepted'])
        elif pending:  # An older checkpoint recorded only the first answer's refusal codes.
            rejected = dict.fromkeys(active, pending[0])
        else:
            rejected, accepted = attempt(day, active, judge)
            if retry and rejected:
                result['pending_retry'] = {'topics': rejected, 'accepted': accepted}
                result['refused'] = dict(refused)
                if checkpoint is not None:
                    checkpoint(result)
        if retry and rejected:
            if max_retries is not None and result['retries'] >= max_retries:
                refused['retry_capped'] += 1
            else:
                attempt(day, {topic_id: active[topic_id] for topic_id in rejected},
                        retry, sorted(set(rejected.values())))
                result['retries'] += 1
        result.pop('pending_retry', None)
        result['refused'] = dict(refused)
        for topic_id, page in pages.items():
            snapshots[topic_id].append((day, deepcopy(page)))
        if checkpoint is not None:
            checkpoint(result)
    return result


def page_at(snapshots, day):
    return max(
        ((saved, page) for saved, page in snapshots if saved < day),
        key=lambda entry: entry[0], default=(None, None),
    )[1]


def assert_no_leak(page, request):
    for item in page['items']:
        for citation in (item, *item['history']):
            if citation['source'] == request['id'] or citation['day'] >= request['day']:
                raise LeakError('page_leak')


def before_window(works, heldout_from):
    return [work for work in works if work['day'] < heldout_from]


def in_window(works, start, end):
    return [work for work in works if start <= work['day'] and (end is None or work['day'] < end)]


def attach_page(strength):
    return strength >= 2


def decide(summary):
    runs = summary['runs']
    a, b = ([run[arm]['needed'] for run in runs] for arm in 'AB')
    deltas = [right - left for left, right in zip(a, b)]
    reasons = []
    if not runs or any(delta <= 0 for delta in deltas):
        reasons.append('not_every_run')
    if runs and mean(deltas) <= max(max(a) - min(a), max(b) - min(b)):
        reasons.append('within_noise')
    if summary['ci_b_minus_a'][0] <= 0:
        reasons.append('ci_includes_zero')
    if runs and mean(run['B']['misleading'] - run['A']['misleading'] for run in runs) > 0:
        reasons.append('more_misleading')
    if summary['ci_b_minus_c'][0] <= 0:
        reasons.append('search_not_worse')
    return {'integrate': not reasons, 'reasons': reasons}


def early_stop(runs, ci=None):
    return len(runs) >= 2 and ((ci is not None and ci[1] < MIN_GAIN) or all(
        run['B']['needed'] <= run['A']['needed'] and run['B']['misleading'] > run['A']['misleading']
        for run in runs[:2]
    ))


def decide_revised(summary):
    runs = summary['runs']
    b = [run['B']['needed'] for run in runs]
    reasons = []
    for arm in 'AC':
        baseline = [run[arm]['needed'] for run in runs]
        deltas = [right - left for left, right in zip(baseline, b)]
        label = arm.lower()
        if not runs or any(delta <= 0 for delta in deltas):
            reasons.append(f'not_every_run_vs_{label}')
        if runs and mean(deltas) <= max(max(baseline) - min(baseline), max(b) - min(b)):
            reasons.append(f'within_noise_vs_{label}')
        if summary[f'ci_b_minus_{label}'][0] <= 0:
            reasons.append(f'ci_vs_{label}_includes_zero')
    for metric in ('traps', 'misleading_other'):
        if runs and mean(run['B'][metric] - run['A'][metric] for run in runs) > 0:
            reasons.append(f'more_{metric}')
    return {'integrate': not reasons, 'reasons': reasons}


def early_stop_revised(runs, ci=None):
    return early_stop(runs, ci) or (len(runs) >= 2 and all(
        run['B']['needed'] <= run['C']['needed'] for run in runs[:2]
    ))


def bootstrap_delta(deltas, iters=2000, seed=0):
    units = defaultdict(list)
    for request_id, entry in sorted(deltas.items()):
        if isinstance(entry, tuple):
            topic_id, values = entry
            unit = ('topic', topic_id)
        else:
            values = entry
            unit = ('request', request_id)
        if values:
            units[unit].append(mean(values))
    if not units:
        return 0.0, 0.0
    rng = random.Random(seed)
    groups = list(units.values())
    samples = sorted(
        mean(value for group in rng.choices(groups, k=len(groups)) for value in group)
        for _ in range(iters)
    )

    def percentile(fraction):
        position = (len(samples) - 1) * fraction
        low, high = math.floor(position), math.ceil(position)
        return samples[low] + (samples[high] - samples[low]) * (position - low)

    return percentile(.025), percentile(.975)
