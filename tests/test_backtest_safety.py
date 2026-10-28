from unittest.mock import AsyncMock
import pytest


@pytest.mark.asyncio
async def test_walk_forward_scores_disjoint_windows(monkeypatch):
    import backtest
    mock = AsyncMock(return_value=([], {'total_trades':1,'total_pnl':1,'win_rate':50,'max_drawdown_pct':1}))
    monkeypatch.setattr(backtest,'run_backtest',mock)
    data = [{'timestamp':i} for i in range(3500)]
    result = await backtest.walk_forward_backtest(data,train_candles=2000,test_candles=500)
    assert result['windows'] == 3
    starts = []
    for call in mock.call_args_list:
        window = call.args[0]
        assert len(window) == 700
        starts.append(window[200]['timestamp'])
    assert starts == [2000,2500,3000]


@pytest.mark.asyncio
async def test_backtest_long_only_next_bar_costs(monkeypatch):
    import backtest
    monkeypatch.setattr(backtest,'compute_indicators',lambda c: {'close':c[-1]['close']})
    monkeypatch.setattr(backtest,'_local_trend_signal',lambda m: {'signal':'BUY','confidence':0.9})
    data = [{'timestamp':1700000000000+i*300000,'open':100,'high':100,'low':100,'close':100,'volume':1}
            for i in range(5)]
    trades, report = await backtest.run_backtest(data,lookback=1)
    assert len(trades) == 1
    assert trades[0]['entry_idx'] == 2
    assert trades[0]['pnl'] < 0  # flat market loses round-trip fees and slippage
    monkeypatch.setattr(backtest,'_local_trend_signal',lambda m: {'signal':'SELL','confidence':0.9})
    trades, _ = await backtest.run_backtest(data,lookback=1)
    assert not trades
