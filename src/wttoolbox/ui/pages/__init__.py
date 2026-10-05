"""Top-level pages.

Every page follows the same contract::

    class SomePage(QWidget):
        def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None: ...

so ``MainWindow`` can instantiate them uniformly and
``tools/render_page.py`` can render any of them in isolation.
"""

from __future__ import annotations

__all__ = ["home", "sound", "tools", "library", "settings"]
