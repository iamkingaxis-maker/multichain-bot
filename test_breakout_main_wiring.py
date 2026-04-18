import os
import sys
from unittest.mock import patch


def test_main_imports_breakout_when_enabled():
    """Ensure main.py's imports don't break when breakout_enabled=True."""
    with patch.dict(os.environ, {"BREAKOUT_ENABLED": "true"}, clear=False):
        if "main" in sys.modules:
            del sys.modules["main"]
        import main  # noqa: F401


def test_breakout_module_imports():
    """All breakout submodules can be imported without side effects."""
    import breakout  # noqa: F401
    import breakout.capital  # noqa: F401
    import breakout.scoring  # noqa: F401
    import breakout.paper_fill  # noqa: F401
    import breakout.data_client  # noqa: F401
    import breakout.database  # noqa: F401
    import breakout.state  # noqa: F401
    import breakout.scanner  # noqa: F401
    import breakout.strategy  # noqa: F401
    import breakout.execution  # noqa: F401
