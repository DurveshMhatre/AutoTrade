import json
import time
from unittest.mock import AsyncMock
import pytest
import config
from core.database import init_db, get_open_trades
from core.runtime import TradingRuntime, size_entry, closed_candles
from execution.journal import OrderJournal, state


class Exchange:
    def __init__(self):
        self.prices = {'BTC/USDT':100, 'ETH/USDT':20, 'SOL/USDT':10}
        self.create_order = AsyncMock()
        self.fetch_order = AsyncMock()
        self.cancel_order = AsyncMock()

    def amount_to_precision(self, symbol, qty):
        import math
        return str(math.floor(qty * 1e6 + 1e-10) / 1e6)

    def price_to_precision(self, symbol, price):
        return str(round(price, 2))

    def market(self, symbol):
        return {'limits':{'amount':{'min':0.000001},'cost':{'min':0.01}}}

    async def fetch_ticker(self, symbol):
        return {'last':self.prices[symbol], 'timestamp':time.time()*1000}


@pytest.fixture
def setup(tmp_path):
    db = init_db(tmp_path/'paper.db')
    ex = Exchange()
    runtime = TradingRuntime(db, ex, paper=True)
    runtime.journal.slippage = 0
    yield db, ex, runtime
    db.close()


async def entry(runtime, symbol='BTC/USDT', qty=1):
    return await runtime.journal.submit(symbol,'buy',qty,runtime.exchange.prices[symbol],'entry',
        context={'atr':1,'regime':'STRONG_TREND_UP','stop':runtime.exchange.prices[symbol]-2.5})


@pytest.mark.asyncio
async def test_paper_roundtrip_and_idempotency(setup):
    db, ex, r = setup
    cid = await entry(r)
    r.journal.apply(cid)
    assert len(get_open_trades(db)) == 1
    ex.prices['BTC/USDT'] = 97
    await r.manage_positions()
    assert not get_open_trades(db)
    await r.refresh_account()
    assert r.balance == pytest.approx(config.PAPER_BALANCE-3-0.1-0.097)
    assert ex.create_order.await_count == 0


@pytest.mark.asyncio
async def test_two_tiers_and_restart_state(setup):
    db, ex, r = setup
    await entry(r)
    ex.prices['BTC/USDT'] = 108
    await r.manage_positions()
    trade = get_open_trades(db)[0]
    assert trade['quantity'] == pytest.approx(0.34)
    saved = json.loads(trade['manager_state'])
    assert saved['tier1_hit'] and saved['tier2_hit']
    restarted = TradingRuntime(db,ex,paper=True)
    await restarted.manage_positions()
    assert get_open_trades(db)[0]['quantity'] == pytest.approx(0.34)
    assert get_open_trades(db)[0]['stop_loss'] >= trade['stop_loss']


@pytest.mark.asyncio
async def test_symbol_specific_exit(setup):
    db, ex, r = setup
    await entry(r)
    await entry(r,'ETH/USDT')
    ex.prices['ETH/USDT'] = 17
    await r.manage_positions()
    assert [t['symbol'] for t in get_open_trades(db)] == ['BTC/USDT']


@pytest.mark.asyncio
async def test_daily_loss_latched_across_restart(setup):
    db, ex, r = setup
    await r.refresh_account()
    await entry(r,qty=90)
    ex.prices['BTC/USDT'] = 90
    await r.manage_positions()
    await r.refresh_account()
    assert not r.account_ok
    assert r.daily_loss > config.DAILY_LOSS_LIMIT_PCT
    restarted = TradingRuntime(db,ex,paper=True)
    await restarted.refresh_account()
    assert not restarted.account_ok


@pytest.mark.asyncio
async def test_uncertain_submit_recovered_without_duplicate(setup):
    db, ex, r = setup
    j = OrderJournal(db,ex,paper=False)
    ex.create_order.side_effect = TimeoutError('accepted but response lost')
    assert await j.submit('BTC/USDT','buy',1,100,'entry',context={
        'atr':1,'regime':'STRONG_TREND_UP','stop':97.5}) is None
    assert j.blocked()
    ex.fetch_order.return_value = {'id':'123','status':'closed','filled':1,'cost':100,'average':100,
                                  'fee':{'currency':'USDT','cost':0.1}}
    await j.reconcile()
    await j.reconcile()
    assert len(get_open_trades(db)) == 1
    assert ex.create_order.await_count == 1
    assert not j.blocked()


@pytest.mark.asyncio
async def test_failed_exit_does_not_commit_target_flags(setup):
    db, ex, r = setup
    await entry(r)
    r.journal.paper = False
    ex.create_order.side_effect = TimeoutError()
    ex.prices['BTC/USDT'] = 108
    await r.manage_positions()
    trade = get_open_trades(db)[0]
    assert trade['quantity'] == 1
    assert not json.loads(trade['manager_state'])['tier1_hit']
    assert r.journal.blocked()


@pytest.mark.asyncio
async def test_partial_ioc_fill_records_only_actual_inventory(setup):
    db, ex, r = setup
    j = OrderJournal(db,ex,paper=False)
    ex.create_order.return_value = {'id':'ioc','status':'canceled','filled':0.4,'cost':40,'average':100,
                                  'fee':{'currency':'BTC','cost':0.0004}}
    cid = await j.submit('BTC/USDT','buy',1,100,'entry',context={
        'atr':1,'regime':'STRONG_TREND_UP','stop':97.5})
    assert get_open_trades(db)[0]['quantity'] == pytest.approx(0.3996)
    j.apply(cid)
    assert db.execute('SELECT COUNT(*) FROM cash_events').fetchone()[0] == 1


def test_sizing_obeys_cash_and_risk():
    qty = size_entry(1000,1000,100,98,0)
    assert qty*100*(1+config.FEE_RATE+config.SLIPPAGE_PCT) <= 1000
    assert qty*(2+100*(2*config.FEE_RATE+2*config.SLIPPAGE_PCT)) <= 20
    assert size_entry(1000,1000,100,98,50) == 0
    assert size_entry(1000,1000,100,float('nan'),0) == 0


def test_closed_candles_reject_stale_and_exclude_incomplete():
    now = 100000
    data = [{'timestamp':(now-300*i)*1000,'open':1,'high':1,'low':1,'close':1} for i in range(60,-1,-1)]
    assert len(closed_candles(data,300,now)) == 60
    with pytest.raises(ValueError):
        closed_candles(data,300,now+2000)


def test_review_schema():
    from agents.trade_review import validate_review
    assert validate_review({'decision':'REJECT','reason':'missing data'})['approved'] is False
    with pytest.raises(ValueError):
        validate_review({'decision':'BUY','reason':'trust me'})


@pytest.mark.asyncio
async def test_analysis_enters_once_per_closed_candle(setup, monkeypatch):
    from core import data_feed, indicators
    from agents import regime_agent, mtf_agent, sr_agent, trend_agent, order_flow_agent, orchestrator, sentiment_agent, news_agent
    db, ex, r = setup
    now = time.time()
    def candles(seconds, count=210):
        return [{'timestamp':int((now-seconds*(i+1))*1000),'open':100,'high':101,
                 'low':99,'close':100,'volume':1} for i in range(count-1,-1,-1)]
    monkeypatch.setattr(data_feed,'fetch_ohlcv',AsyncMock(return_value=candles(300)))
    monkeypatch.setattr(data_feed,'fetch_multi_timeframe',AsyncMock(return_value={
        '1h':candles(3600),'4h':candles(14400),'1d':candles(86400)}))
    monkeypatch.setattr(data_feed,'fetch_order_book',AsyncMock(return_value={'bids':[],'asks':[]}))
    monkeypatch.setattr(indicators,'compute_indicators',lambda c: {'close':100,'atr':1})
    monkeypatch.setattr(indicators,'compute_mtf_indicators',lambda c: {})
    monkeypatch.setattr(indicators,'compute_vwap_and_poc',lambda c: {})
    monkeypatch.setattr(regime_agent,'run_regime_agent',lambda *a,**k: {'regime':'STRONG_TREND_UP','strategy_bias':'trend_follow'})
    monkeypatch.setattr(mtf_agent,'run_mtf_agent',lambda *a: {})
    monkeypatch.setattr(sr_agent,'run_sr_agent',lambda *a: {})
    monkeypatch.setattr(trend_agent,'run_trend_agent',lambda *a: {'signal':'BUY','confidence':0.9})
    monkeypatch.setattr(order_flow_agent,'run_order_flow_agent',lambda *a: {})
    monkeypatch.setattr(sentiment_agent,'run_sentiment_agent',AsyncMock(return_value={}))
    monkeypatch.setattr(news_agent,'run_news_agent',lambda *a: {})
    monkeypatch.setattr(orchestrator,'run_orchestrator',lambda *a,**k: {
        'trade_approved':True,'final_signal':'BUY','conviction_score':90,'position_size_tier':'full'})
    await r.analyze('BTC/USDT')
    assert len(get_open_trades(db)) == 1
    await r.analyze('BTC/USDT')
    assert len(get_open_trades(db)) == 1


@pytest.mark.asyncio
async def test_protective_stop_cancel_fill_race_does_not_double_sell(setup):
    db, ex, r = setup
    await entry(r)
    r.journal.paper = False
    ex.create_order.return_value = {'id':'stop','status':'open','filled':0}
    await r.journal.protect(get_open_trades(db)[0])
    assert not r.journal.blocked()
    ex.fetch_order.return_value = {'id':'stop','status':'closed','filled':1,'average':97,
                                  'cost':97,'fee':{'currency':'USDT','cost':0.097}}
    await r.journal.release_protection(get_open_trades(db)[0]['id'])
    assert not get_open_trades(db)
    assert ex.create_order.await_count == 1


@pytest.mark.asyncio
async def test_definitively_rejected_protection_emergency_exit(setup):
    import ccxt
    db, ex, r = setup
    await entry(r)
    r.journal.paper = False
    ex.create_order.side_effect = [ccxt.InvalidOrder('stop rejected'),
        {'id':'exit','status':'closed','filled':1,'average':100,'cost':100,
         'fee':{'currency':'USDT','cost':0.1}}]
    with pytest.raises(RuntimeError,match='emergency'):
        await r.journal.protect(get_open_trades(db)[0])
    assert not get_open_trades(db)


@pytest.mark.asyncio
async def test_reconciliation_failure_blocks_entries_despite_resting_stop(setup):
    db, ex, r = setup
    await entry(r)
    r.journal.paper = False
    ex.create_order.return_value = {'id':'stop','status':'open','filled':0}
    await r.journal.protect(get_open_trades(db)[0])
    ex.fetch_order.side_effect = TimeoutError()
    await r.manage_positions()
    assert not r.management_ok


@pytest.mark.asyncio
async def test_missing_commission_reconciles_before_inventory_update(setup):
    db, ex, r = setup
    j = OrderJournal(db,ex,paper=False)
    order = {'id':'order','status':'closed','filled':1,'cost':100,'average':100}
    ex.create_order.return_value = order
    ex.fetch_my_trades = AsyncMock(side_effect=TimeoutError())
    await j.submit('BTC/USDT','buy',1,100,'entry',context={
        'atr':1,'regime':'STRONG_TREND_UP','stop':97.5})
    assert j.blocked()
    assert not get_open_trades(db)
    ex.fetch_my_trades.side_effect = None
    ex.fetch_my_trades.return_value = [{'order':'order','amount':1,
                                      'fee':{'currency':'BTC','cost':0.001}}]
    ex.fetch_order.return_value = order
    await j.reconcile()
    assert get_open_trades(db)[0]['quantity'] == pytest.approx(0.999)
    assert not j.blocked()
