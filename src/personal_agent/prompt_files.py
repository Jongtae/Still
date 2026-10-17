"""Model-facing prompt text, one Markdown file per prompt or fragment (#1508).

The files live in ``prompts/`` next to this module and ship as package data,
the same way ``messages.json`` and the bundled skills do.  Each
module keeps its own constant (``CORE_INSTRUCTIONS = prompt('worker_core')``),
so callers do not change.  A file holds the prompt text verbatim; the single
newline that ends the file is not part of it.  The few placeholders
(``{set}``, ``{query}``, ...) are filled with ``str.format`` by the module
that owns the constant.
"""

from pathlib import Path

PROMPTS = Path(__file__).with_name('prompts')


def prompt(name):
    """The text of ``prompts/<name>.md``, without the newline that ends the file."""
    return PROMPTS.joinpath(f'{name}.md').read_text(encoding='utf-8').removesuffix('\n')
