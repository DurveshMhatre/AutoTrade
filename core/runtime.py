"""Spot runtime: independent exits, symbol-scoped signals and durable execution."""
import asyncio
import copy
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from core.database import init_db, get_open_trades, save_decision
from core.trade_manager import TrailingStopManager
from execution.journal import OrderJournal, state, set_state

log = logging.getLogger(__name__)


def closed_candles(candles, seconds, now=None):
    now = time.time() if now is None else now
    result = [c for c in candles if c['timestamp'] / 1000 + seconds <= now]
    if len(result) < 50 or now - (result[-1]['timestamp'] / 1000 + seconds) > seconds * 2:
        raise ValueError('Insufficient or stale closed candles')
    if any(a['timestamp'] >= b['timestamp'] for a, b in zip(result, result[1:])):
        raise ValueError('Candle timestamps must increase')
    if any(not math.isfinite(float(c[k])) or float(c[k]) <= 0
           for c in result for k in ('open', 'high', 'low', 'close')):
        raise ValueError('Invalid candle prices')
    return result


def size_entry(balance, equity, entry, stop, open_risk, tier=1.0):
    values = (balance, equity, entry, stop, open_risk, tier)
    if not all(math.isfinite(float(v)) for v in values) or not 0 < stop < entry or balance <= 0:
        return 0.0
    available_risk = max(0, equity * config.MAX_PORTFOLIO_RISK_PCT - open_risk)
    risk = min(equity * config.RISK_PER_TRADE_PCT * min(1, max(0, tier)), available_risk)
    # Reserve adverse entry/exit prices and commissions within the risk budget.
    unit_risk = entry - stop + entry * (2 * config.FEE_RATE + 2 * config.SLIPPAGE_PCT)
    return max(0, min(risk / unit_risk,
                      balance / (entry * (1 + config.FEE_RATE + config.SLIPPAGE_PCT)),
                      equity * config.MAX_ALLOCATION_PCT / (entry * (1 + config.SLIPPAGE_PCT))))


class TradingRuntime:
    def __init__(self, db, exchange, paper=True):
        self.db, self.exchange, self.paper = db, exchange, paper
        self.journal = OrderJournal(db, exchange, paper, config.FEE_RATE, config.SLIPPAGE_PCT)
        self.lock = asyncio.Lock()
        self.prices = {}
        self.balance = 0.0
        self.equity = 0.0
        self.daily_loss = 0.0
        self.account_ok = False
        self.management_ok = True
        self.sentiment_cache = None
        self.sentiment_time = 0
        if state(db, 'initial_balance') is None and paper:
            set_state(db, 'initial_balance', config.PAPER_BALANCE)

    async def refresh_account(self):
        self.account_ok = False
        trades = get_open_trades(self.db)
        symbols = set(config.SYMBOLS) | {t['symbol'] for t in trades}
        marks = {}
        for symbol in symbols:
            ticker = await self.exchange.fetch_ticker(symbol)
            price = float(ticker.get('last') or 0)
            ts = ticker.get('timestamp')
            if not math.isfinite(price) or price <= 0 or (ts and abs(time.time() - ts / 1000) > 60):
                raise ValueError(f'Invalid/stale ticker for {symbol}')
            marks[symbol] = price
        self.prices = marks
        if self.paper:
            delta = self.db.execute('SELECT COALESCE(SUM(cash_delta),0) FROM cash_events').fetchone()[0]
            self.balance = state(self.db, 'initial_balance') + delta
        else:
            account = await self.exchange.fetch_balance()
            self.balance = float(account.get('USDT', {}).get('free', 0))
            # Unrelated/manual holdings are not adopted silently into this strategy.
            for symbol in symbols:
                base = symbol.split('/')[0]
                actual = float(account.get(base, {}).get('total', 0))
                tracked = sum(t['quantity'] for t in trades if t['symbol'] == symbol)
                if abs(actual - tracked) * marks[symbol] > 1.0:
                    raise RuntimeError(f'{symbol} holdings differ from ledger; reconcile before entries')
        self.equity = self.balance + sum(t['quantity'] * marks[t['symbol']] for t in trades)
        if not math.isfinite(self.equity) or self.equity <= 0:
            raise ValueError('Account equity unavailable')
        if state(self.db, 'initial_balance') is None:
            set_state(self.db, 'initial_balance', self.equity)
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        baseline = state(self.db, 'day_equity')
        if not baseline or baseline['day'] != day:
            baseline = {'day': day, 'equity': self.equity}
            set_state(self.db, 'day_equity', baseline)
        self.daily_loss = max(0, (baseline['equity'] - self.equity) / baseline['equity'])
        # Once tripped, stay latched for the UTC day even if prices recover.
        if self.daily_loss >= config.DAILY_LOSS_LIMIT_PCT:
            set_state(self.db, 'loss_halt_day', day)
        self.account_ok = state(self.db, 'loss_halt_day') != day

    async def manage_positions(self):
        self.management_ok = await self.journal.reconcile()
        for trade in get_open_trades(self.db):
            try:
                ticker = await self.exchange.fetch_ticker(trade['symbol'])
                price = float(ticker.get('last') or 0)
                ts = ticker.get('timestamp')
                if not math.isfinite(price) or price <= 0 or (ts and abs(time.time() - ts / 1000) > 60):
                    raise ValueError('Invalid exit price')
                if trade['side'] != 'buy' or not trade.get('manager_state'):
                    raise RuntimeError('Legacy position requires explicit reconciliation; cannot invent stop state')
                manager = TrailingStopManager.__new__(TrailingStopManager)
                manager.__dict__.update(json.loads(trade['manager_state']))
                proposed = copy.deepcopy(manager)
                atr = max(manager.R / 2, price * 0.0001)
                result = proposed.update(price, atr)
                if result['actions']:
                    await self.journal.release_protection(trade['id'])
                    fresh = self.db.execute('SELECT * FROM trades WHERE id=?', (trade['id'],)).fetchone()
                    if fresh['status'] != 'open':
                        continue  # stop filled while canceling
                    close_all = any(a['action'] == 'close_all' for a in result['actions'])
                    # Both tiers in one tick are one sale and one atomic ledger update.
                    qty = fresh['quantity'] if close_all else min(fresh['quantity'],
                        fresh['initial_quantity'] * sum(a.get('pct', 0) for a in result['actions']))
                    try:
                        self.journal.normalize(trade['symbol'], qty, price)
                    except ValueError:
                        qty = fresh['quantity']  # partial below minimum: close full tradable remainder
                    await self.journal.submit(trade['symbol'], 'sell', qty, price, 'exit', trade['id'],
                                              {'manager_state': vars(proposed)})
                elif proposed.trailing_stop != manager.trailing_stop:
                    await self.journal.release_protection(trade['id'])
                    with self.db:
                        self.db.execute("UPDATE trades SET stop_loss=?,manager_state=? WHERE id=? AND status='open'",
                            (proposed.trailing_stop, json.dumps(vars(proposed)), trade['id']))
                fresh = self.db.execute('SELECT * FROM trades WHERE id=?', (trade['id'],)).fetchone()
                if fresh['status'] == 'open':
                    await self.journal.protect(dict(fresh))
            except Exception:
                self.account_ok = False
                self.management_ok = False
                log.exception('Position %s management failed; new entries blocked', trade['id'])

    async def monitor(self, stop):
        while not stop.is_set():
            try:
                async with self.lock:
                    await self.manage_positions()
                    set_state(self.db, 'heartbeat', time.time())
            except Exception:
                self.account_ok = False
                log.exception('Exit monitor failed')
            try:
                await asyncio.wait_for(stop.wait(), timeout=config.MONITOR_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def notifications(self, stop):
        """Delivery and optional post-trade analysis never hold the execution lock."""
        from monitoring.telegram_alerts import send_telegram_alert
        from core.database import save_autopsy
        from agents.autopsy_agent import run_autopsy_agent
        while not stop.is_set():
            try:
                cursor = state(self.db, 'last_notification', 0)
                rows = self.db.execute('''SELECT e.rowid AS event_id,e.*,o.symbol,o.side,o.trade_id
                    FROM cash_events e JOIN execution_orders o ON o.client_id=e.client_id
                    WHERE e.rowid>? ORDER BY e.rowid''',(cursor,)).fetchall()
                for row in rows:
                    sent = await send_telegram_alert(
                        f"{row['side'].upper()} {row['symbol']} | Realized PnL ${row['realized_pnl']:.2f}")
                    if not sent:
                        break
                    set_state(self.db,'last_notification',row['event_id'])
                if config.USE_AI_AGENTS:
                    trades = self.db.execute("""SELECT * FROM trades WHERE status='closed'
                        AND id NOT IN (SELECT trade_id FROM autopsies) ORDER BY id LIMIT 1""").fetchall()
                    for trade in trades:
                        result = await asyncio.to_thread(run_autopsy_agent,dict(trade),{})
                        save_autopsy(self.db,dict(result,trade_id=trade['id'],timestamp=int(time.time())))
            except Exception:
                log.exception('Notification/post-trade review failed')
            try:
                await asyncio.wait_for(stop.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    async def analyze(self, symbol):
        from core.data_feed import fetch_ohlcv, fetch_multi_timeframe, fetch_order_book
        from core.indicators import compute_indicators, compute_mtf_indicators, compute_vwap_and_poc
        from agents.regime_agent import run_regime_agent
        from agents.mtf_agent import run_mtf_agent
        from agents.sr_agent import run_sr_agent
        from agents.trend_agent import run_trend_agent
        from agents.mean_reversion_agent import run_mean_reversion_agent
        from agents.order_flow_agent import run_order_flow_agent
        from agents.orchestrator import run_orchestrator
        from agents.sentiment_agent import run_sentiment_agent
        from agents.news_agent import run_news_agent
        candles = closed_candles(await fetch_ohlcv(symbol, config.TIMEFRAME, config.CANDLE_LIMIT + 1,
                                                 exchange=self.exchange), 300)
        candle_id = candles[-1]['timestamp']
        if state(self.db, 'last_candle:' + symbol) == candle_id:
            return
        market = compute_indicators(candles)
        mtf_candles = await fetch_multi_timeframe(symbol, config.MTF_TIMEFRAMES, exchange=self.exchange)
        mtf = {'5m': market}
        for tf, seconds in [('1h',3600), ('4h',14400), ('1d',86400)]:
            mtf_candles[tf] = closed_candles(mtf_candles.get(tf, []), seconds)
            mtf[tf] = compute_mtf_indicators(mtf_candles[tf])
        # Agent code may block on an LLM: never run it on the exit event loop.
        regime = await asyncio.to_thread(run_regime_agent, mtf, force=True)
        strategy_key = {'trend_follow':'TREND_FOLLOWING','mean_revert':'MEAN_REVERSION'}.get(
            regime.get('strategy_bias'), 'FLAT')
        strategy = config.STRATEGIES[strategy_key]
        set_state(self.db, 'last_candle:' + symbol, candle_id)
        if strategy['action'] == 'NO_TRADE':
            return
        sr = await asyncio.to_thread(run_sr_agent, mtf_candles['1h'], market['close'])
        mtf_result = await asyncio.to_thread(run_mtf_agent, mtf)
        if strategy_key == 'MEAN_REVERSION':
            signal = await asyncio.to_thread(run_mean_reversion_agent, market, sr, regime['regime'])
        else:
            signal = await asyncio.to_thread(run_trend_agent, market, regime['regime'])
        book = await fetch_order_book(symbol, config.ORDER_BOOK_DEPTH, exchange=self.exchange)
        flow = await asyncio.to_thread(run_order_flow_agent, book, compute_vwap_and_poc(candles), market['close'])
        if time.time() - self.sentiment_time > 1800 or self.sentiment_cache is None:
            self.sentiment_cache = await run_sentiment_agent()
            self.sentiment_time = time.time()
        headlines = []
        if os.getenv('CRYPTOPANIC_API_KEY'):
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get('https://cryptopanic.com/api/v1/posts/', params={
                    'auth_token':os.getenv('CRYPTOPANIC_API_KEY'),'public':'true','filter':'hot',
                    'currencies':symbol.split('/')[0]}) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    headlines = [{'title':r.get('title','')} for r in data.get('results',[])[:10]]
        news = await asyncio.to_thread(run_news_agent, headlines)
        async with self.lock:
            await self.refresh_account()
            portfolio = {'usdt_balance': self.balance, 'open_positions':len(get_open_trades(self.db)),
                         'daily_loss_pct': self.daily_loss}
        result = await asyncio.to_thread(run_orchestrator, market, portfolio, trend_data=signal,
                    regime_data=regime, mtf_data=mtf_result, sr_data=sr,
                    order_flow_data=flow, sentiment_data=self.sentiment_cache,
                    news_data=news, active_strategy=strategy)
        # SELL reduces exposure even if entry filters veto it.
        if signal.get('signal') == 'SELL':
            async with self.lock:
                for trade in get_open_trades(self.db):
                    if trade['symbol'] != symbol:
                        continue
                    await self.journal.release_protection(trade['id'])
                    fresh = self.db.execute('SELECT * FROM trades WHERE id=?', (trade['id'],)).fetchone()
                    if fresh['status'] == 'open':
                        await self.journal.submit(symbol, 'sell', fresh['quantity'], self.prices[symbol], 'exit', trade['id'])
            return
        if (not result.get('trade_approved') or result.get('final_signal') != 'BUY'
            or result.get('conviction_score',0) / 100 < config.MIN_CONFIDENCE):
            self.decision(result, False)
            return
        from agents.trade_review import review_trade
        review = await review_trade(symbol, market, signal, result)
        if not review['approved']:
            self.decision({'final_signal':'HOLD','summary': review['reason']}, False)
            return
        async with self.lock:
            await self.manage_positions()
            await self.refresh_account()
            trades = get_open_trades(self.db)
            if (not self.account_ok or not self.management_ok or self.journal.blocked()
                or len(trades) >= min(config.MAX_POSITIONS, strategy['max_positions'])
                or any(t['symbol'] == symbol for t in trades)
                or any(not t.get('manager_state') for t in trades)):
                return
            # Reject signals that became stale during remote analysis.
            if time.time() - (candle_id / 1000 + 300) > 300:
                return
            price = self.prices[symbol]
            atr = float(market.get('atr') or 0)
            if not math.isfinite(atr) or atr <= 0:
                return
            manager = TrailingStopManager(price, 'buy', atr, regime['regime'],
                sl_override=signal.get('suggested_sl'), tp_override=signal.get('suggested_tp'))
            if not 0 < manager.initial_stop < price < manager.tier1_target:
                return
            open_risk = sum(max(0, t['price'] - t['stop_loss']) * t['quantity'] for t in trades)
            tier = {'full':1, '100':1, '75':0.75, '50':0.5}.get(str(result.get('position_size_tier')),0.5)
            tier *= min(1.0, max(0.0, float(strategy.get('size_multiplier',1.0))))
            tier *= min(1.0, max(0.0, float(regime.get('position_size_multiplier',1.0))))
            qty = size_entry(self.balance, self.equity, price, manager.initial_stop, open_risk, tier)
            if qty <= 0:
                return
            try:
                await self.journal.submit(symbol, 'buy', qty, price, 'entry', context={
                    'atr':atr,'regime':regime['regime'],'stop':manager.initial_stop,
                    'target': manager.tier1_target if regime['regime']=='RANGING' else None,
                    'reason': result.get('summary','')})
            except ValueError as exc:
                log.info('%s entry rejected: %s', symbol, exc)
                return
            await self.manage_positions()
            self.decision(result, True)

    def decision(self, result, approved):
        save_decision(self.db, {'timestamp':int(time.time()), 'signal':result.get('final_signal','HOLD'),
            'confidence':result.get('conviction_score',0)/100, 'reason':result.get('summary',''),
            'approved':int(approved)})


async def run(once=False):
    from core.data_feed import ExchangeSession
    paper = config.DRY_RUN
    testnet = os.getenv('BINANCE_TESTNET','true').lower() == 'true'
    mode = 'paper' if paper else 'testnet' if testnet else 'live'
    path = Path(__file__).resolve().parents[1] / f'trading_bot_{mode}.db'
    db = init_db(path)
    stop = asyncio.Event()
    try:
        async with ExchangeSession(authenticated=not paper, testnet=False if paper else testnet) as exchange:
            await exchange.load_markets()
            runtime = TradingRuntime(db, exchange, paper)
            async with runtime.lock:
                await runtime.manage_positions()
                await runtime.refresh_account()
            log.info('Started %s mode, ledger=%s, equity=%.2f', mode, path, runtime.equity)
            monitor = asyncio.create_task(runtime.monitor(stop))
            notifications = asyncio.create_task(runtime.notifications(stop))
            try:
                while True:
                    failures = []
                    for symbol in config.SYMBOLS:
                        try:
                            await runtime.analyze(symbol)
                        except Exception:
                            failures.append(symbol)
                            log.exception('Analysis failed for %s; exit monitoring continues', symbol)
                    if once:
                        if failures:
                            raise RuntimeError('Cycle failed for: ' + ', '.join(failures))
                        break
                    await asyncio.sleep(15)
            finally:
                stop.set()
                await monitor
                notifications.cancel()
                await asyncio.gather(notifications, return_exceptions=True)
                # Leave acknowledged exchange protection in place on shutdown.
    finally:
        db.close()
