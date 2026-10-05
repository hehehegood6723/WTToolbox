"""WTToolbox - a War Thunder companion toolkit.

Pure-Python core layer.  Nothing in :mod:`wttoolbox.core` imports Qt, so the
whole backend can be exercised from the command line.
"""

from __future__ import annotations

__all__ = ["__version__", "APP_NAME", "APP_NAME_ZH"]

__version__ = "1.1.0"
APP_NAME = "WTToolbox"
APP_NAME_ZH = "战雷工具箱"
