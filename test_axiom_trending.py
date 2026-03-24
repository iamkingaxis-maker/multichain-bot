# test_axiom_trending.py
def test_trending_scanner_stores_auth_manager():
    """AxiomTrendingScanner stores auth_manager as self.auth_manager."""
    from unittest.mock import MagicMock, AsyncMock
    from feeds.axiom_trending_scanner import AxiomTrendingScanner

    auth = MagicMock()
    scanner = AxiomTrendingScanner(
        auth_manager=auth,
        trader=MagicMock(), signal_evaluator=MagicMock(),
        security_checker=MagicMock(), telegram=AsyncMock(),
        tracker=MagicMock(),
    )
    assert scanner.auth_manager is auth
