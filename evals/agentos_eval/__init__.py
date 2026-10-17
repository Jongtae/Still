"""EVAL-LOOP-01 (#821): continuous owner-realistic evaluation of Still.

Development tooling only.  Still is driven as a black box over its local
HTTP API from isolated sandbox instances; nothing here is imported by
``src/personal_agent``.  Only ``task.py`` imports Inspect AI; every other
module is standard library so its unit tests run without the eval extra.
"""
