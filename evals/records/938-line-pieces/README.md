# #938 slice 2: the line-run guidance sentence, before and after

Design D2 in [`docs/design/line-pieces-938.en.md`](../../../docs/design/line-pieces-938.en.md) asks for a small measurement of the sentence `lines.piece_guidance` before and after. The sentence went in with #980.

**Evidence class:** live local observation. It used one checkout (`ca3cec16`), one model (OpenAI `gpt-6.2-sol` on the Responses API, the owner's configured main AI) and two epochs per arm. It is not a calibrated quality score.

**Method** (`evals/line_pieces_938.py`):
- **Setup:** The model-free line fixture (`tests/test_entrusted_lines_798._LineCase`) provides the temporary store, fake clock, tick, worker and fixture decision engine. The real model replaces the scripted one only for the line's run. The setup turn that opens the line stays scripted, so both arms start from the same state.
- **Outside reads:** They go to a fixed synthetic world, one page per scenario.
- **Arms:** The `without` arm blanks only `lines.piece_guidance`. Everything else in the run is identical.

## Scenarios

| Scenario | The page says | Expected |
|---|---|---|
| `refund-pending` | The refund is processing and the next update is in about 2 hours | One piece about 2 hours ahead, with a handoff |
| `signup-opens-later` | Sign-up opens today, 1.5 hours from now | One piece at about the opening time, with a handoff |
| `one-go-summary` | Today's notice, complete | No piece |

## Results (`results.json`)

| | Without the sentence | With the sentence |
|---|---|---|
| A piece was scheduled where one was expected | 3 of 4 | **4 of 4** |
| The first `schedule_preparation` call was accepted | 0 of 3 calls made | **4 of 4** |
| Due time near the page's hint | 3 of 3 pieces | 4 of 4 pieces |
| Handoff written into the piece's goal (40 characters or more) | 3 of 3 pieces | 4 of 4 pieces |
| No piece for the one-go task | 2 of 2 | 2 of 2 |
| Mean seconds, scheduling scenarios | 30.7 | **23.1** |
| Mean model calls, scheduling scenarios | 6.75 | **5.25** |

**Without the sentence:**
- **First call refused:** Every first scheduling call was refused with `line_piece_invalid`. The worker sent a recurring or windowed shape that a piece may not have, then corrected it on a second call.
- **No piece in one run:** In one `refund-pending` run the worker scheduled nothing, and the work stopped there.

**With the sentence:** The first call had the right shape every time. Runs were about 25% faster and used fewer model calls.

**No over-scheduling:** The sentence caused none. The one-go summary scheduled no piece in either arm.

**Handoffs:** In both arms they read like the design's progress note: what was checked, the state found, and what the next piece should do, including when to stop scheduling.

## Discarded first run (`run1-results.json`)

The first run had a harness bug.
- **The bug:** The sign-up page said "today" for an opening three hours after a 21:10 run, which was past midnight.
- **Effect:** The worker read the time as ambiguous. That produced one 5-minute re-check and one run with no piece.
- **What is kept:** That scenario's numbers are not used above. The `refund-pending` and `one-go-summary` results from that run agree with the table: without the sentence, every scheduling run first had a refused call; with it, every first call was accepted.

## Observed, not part of this measurement

In several replies the worker told the owner that the next check's result would be "kept, not sent" right away. The fixture's root row has no delivery mode set. This concerns what the owner is told about a piece's delivery, which slice 3 (owner view) covers.

## Reproduce

```sh
PYTHONPATH=src:tests python evals/line_pieces_938.py --epochs 2 --out evals/records/938-line-pieces
```

The model and key are read read-only from the owner's live data folder, unless `--model` is given and the `OPENAI_API_KEY` environment variable is set. Twelve runs took about six minutes.
