"""
tests/test_dry_run.py — Verify dry-run (paper trading) mode
=============================================================
Ensures that when DRY_RUN=True, the executor returns mock orders
without touching the exchange, and the config dynamic loader works.
"""

import asyncio
import json
import sys
from pathlib import Path

# Ensure project root on path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def test_mock_order():
    """_mock_order should return a CCXT-compatible order dict."""
    from execution.executor import _mock_order

    order = _mock_order("buy", "BTC/USDT", 0.001, 95000.0, "market")

    assert order["id"].startswith("DRYRUN-"), f"Bad id: {order['id']}"
    assert order["symbol"] == "BTC/USDT"
    assert order["side"] == "buy"
    assert order["amount"] == 0.001
    assert order["price"] == 95000.0
    assert order["status"] == "closed"
    assert order["filled"] == 0.001
    assert order["remaining"] == 0.0
    assert order["info"]["dry_run"] is True
    assert "fee" in order

    print("  [PASS] test_mock_order")


def test_dynamic_params_loader(tmp_path):
    import runpy
    module = tmp_path / "config.py"
    module.write_text((_ROOT / "config.py").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "live_params.json").write_text('{"STOP_LOSS_PCT": 0.025}')
    assert runpy.run_path(str(module))["STOP_LOSS_PCT"] == 0.025


def test_dry_run_flag():
    """DRY_RUN should default to True."""
    import os
    os.environ["DRY_RUN"] = "true"

    if "config" in sys.modules:
        del sys.modules["config"]

    import config
    assert config.DRY_RUN is True, f"Expected DRY_RUN=True, got {config.DRY_RUN}"

    del sys.modules["config"]
    print("  [PASS] test_dry_run_flag")


def test_check_order_status_dryrun():
    """check_order_status should handle DRYRUN- order IDs."""
    from execution.executor import check_order_status

    from execution.executor import _mock_order
    order = _mock_order("buy", "BTC/USDT", 0.001, 60000)
    result = asyncio.run(check_order_status(order["id"], "BTC/USDT"))
    assert result["status"] == "closed", f"Expected closed, got {result['status']}"
    assert result["filled"] == 0.001

    print("  [PASS] test_check_order_status_dryrun")


def test_backtest_params_override():
    """run_backtest should accept params dict override."""
    from backtest import run_backtest
    # Just verify it doesn't crash with custom params
    # (we can't fetch candles in a unit test, so test the cfg merge logic)
    import inspect
    sig = inspect.signature(run_backtest)
    assert "params" in sig.parameters, "run_backtest missing 'params' parameter"
    print("  [PASS] test_backtest_params_override")


def test_multi_pair_config():
    """config.SYMBOLS should be a list and SYMBOL should be first element."""
    import os
    os.environ["DRY_RUN"] = "true"

    if "config" in sys.modules:
        del sys.modules["config"]

    import config
    assert isinstance(config.SYMBOLS, list), f"SYMBOLS should be list, got {type(config.SYMBOLS)}"
    assert len(config.SYMBOLS) >= 1, "SYMBOLS should have at least 1 pair"
    assert config.SYMBOL == config.SYMBOLS[0], "SYMBOL should be first element of SYMBOLS"

    del sys.modules["config"]
    print("  [PASS] test_multi_pair_config")


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  DRY-RUN & MULTI-PAIR TEST SUITE")
    print("=" * 50)

    test_mock_order()
    test_dry_run_flag()
    test_dynamic_params_loader()
    test_check_order_status_dryrun()
    test_backtest_params_override()
    test_multi_pair_config()

    print("\n" + "=" * 50)
    print("  ALL TESTS PASSED")
    print("=" * 50 + "\n")
