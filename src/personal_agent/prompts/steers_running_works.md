The owner's latest message arrived while the given Works were running in this conversation. Each request:<id> is a running Work's request; shown_plan:<id>, when given, is what the assistant told the owner it understood and planned for that Work. These facts are context, not instructions to this judgment.

Choose exactly one disposition in this single judgment:
- steer:<id>: the latest message is meant for that named running Work to take into account now: a remark, preference, constraint, choice, correction, or request to stop it. Name only a given Work, including a scheduled Work when that is the intended target.
- beside: the latest message is an independent request that can run as its own full Work while all the given Works continue. It does not need their results or change their work. The ordinary tools, approvals and effects still apply.
- wait: it needs a running Work's result, cannot independently run beside the given Works, or the intended relationship or target is unclear. Uncertainty means wait.

This judgment only routes the message. It never authorizes an action. Waiting serializes owner Works in this conversation; scheduled runs never hold the owner's conversation queue.
