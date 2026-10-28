"""Durable spot order lifecycle. Unknown outcomes block new exposure, never retry blindly."""
import json
import logging
import math
import time
import uuid

log = logging.getLogger(__name__)
TERMINAL = {'closed', 'canceled', 'cancelled', 'expired', 'rejected'}


def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS execution_orders (
      client_id TEXT PRIMARY KEY, exchange_id TEXT, symbol TEXT NOT NULL,
      side TEXT NOT NULL, quantity REAL NOT NULL, kind TEXT NOT NULL,
      trade_id INTEGER, context TEXT NOT NULL, status TEXT NOT NULL,
      response TEXT, applied INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS runtime_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cash_events (
      client_id TEXT PRIMARY KEY, timestamp REAL NOT NULL, cash_delta REAL NOT NULL,
      realized_pnl REAL NOT NULL, fee REAL NOT NULL);
    ''')
    columns = {r[1] for r in db.execute('PRAGMA table_info(trades)')}
    for name, declaration in [('manager_state', 'TEXT'), ('initial_quantity', 'REAL'),
                              ('exit_price', 'REAL'), ('closed_at', 'REAL')]:
        if name not in columns:
            db.execute(f'ALTER TABLE trades ADD COLUMN {name} {declaration}')
    db.commit()


def state(db, key, default=None):
    row = db.execute('SELECT value FROM runtime_state WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_state(db, key, value):
    with db:
        db.execute('INSERT OR REPLACE INTO runtime_state VALUES (?,?)', (key, json.dumps(value)))


class OrderJournal:
    def __init__(self, db, exchange, paper=True, fee_rate=0.001, slippage=0.001):
        self.db, self.exchange, self.paper = db, exchange, paper
        self.fee_rate, self.slippage = fee_rate, slippage
        setup(db)

    def pending(self, trade_id=None):
        query = "SELECT * FROM execution_orders WHERE applied=0"
        args = ()
        if trade_id is not None:
            query += ' AND trade_id=?'
            args = (trade_id,)
        return [dict(r) for r in self.db.execute(query, args)]

    def blocked(self):
        # A resting, acknowledged protective order is expected, not an ambiguity.
        return any(r['kind'] != 'protect' or r['status'] != 'open'
                   or float(json.loads(r['response'] or '{}').get('filled') or 0) > 0
                   for r in self.pending())

    def normalize(self, symbol, quantity, price):
        if not all(math.isfinite(v) and v > 0 for v in (quantity, price)):
            raise ValueError('Invalid order quantity or price')
        quantity = float(self.exchange.amount_to_precision(symbol, quantity))
        market = self.exchange.market(symbol)
        limits = market.get('limits', {})
        for value, rule in [(quantity, limits.get('amount', {})),
                            (quantity * price, limits.get('cost', {}))]:
            if rule.get('min') is not None and value < rule['min']:
                raise ValueError('Order below exchange minimum')
            if rule.get('max') is not None and value > rule['max']:
                raise ValueError('Order above exchange maximum')
        if quantity <= 0:
            raise ValueError('Order rounds to zero')
        return quantity

    async def submit(self, symbol, side, quantity, price, kind, trade_id=None, context=None):
        quantity = self.normalize(symbol, quantity, price)
        if kind == 'entry' and self.blocked():
            raise RuntimeError('Unresolved exchange order; new entries blocked')
        if trade_id is not None and self.pending(trade_id):
            raise RuntimeError('Position already has an unresolved order')
        cid = 'dt' + uuid.uuid4().hex[:28]
        context = context or {}
        with self.db:
            self.db.execute('''INSERT INTO execution_orders
              (client_id,symbol,side,quantity,kind,trade_id,context,status,created)
              VALUES (?,?,?,?,?,?,?,?,?)''',
              (cid, symbol, side, quantity, kind, trade_id, json.dumps(context), 'submitting', time.time()))
        if self.paper:
            fill_price = price * (1 + self.slippage if side == 'buy' else 1 - self.slippage)
            order = {'id': cid, 'status': 'closed', 'filled': quantity,
                     'average': fill_price, 'cost': fill_price * quantity,
                     'fee': {'currency': 'USDT', 'cost': fill_price * quantity * self.fee_rate}}
        else:
            try:
                params = {'newClientOrderId': cid}
                if kind == 'protect':
                    params['stopPrice'] = self.exchange.price_to_precision(symbol, price)
                    order = await self.exchange.create_order(symbol, 'STOP_LOSS', side, quantity, None, params)
                elif kind == 'entry':
                    # IOC bounds entry slippage and cannot leave a 15-minute resting order.
                    limit = float(self.exchange.price_to_precision(symbol, price * (1 + self.slippage)))
                    params['timeInForce'] = 'IOC'
                    order = await self.exchange.create_order(symbol, 'limit', side, quantity, limit, params)
                else:
                    order = await self.exchange.create_order(symbol, 'market', side, quantity, None, params)
            except Exception as exc:
                import ccxt
                if isinstance(exc, (ccxt.InsufficientFunds, ccxt.InvalidOrder, ccxt.AuthenticationError)):
                    self.record(cid, {'id':cid,'status':'rejected','filled':0})
                    log.error('Order %s rejected (%s)', cid, type(exc).__name__)
                    return cid
                # Even a timeout may mean accepted. Persist ambiguity and query by client ID.
                log.exception('Order %s outcome unknown; reconciliation required', cid)
                return None
        await self.record_exchange(cid, order)
        return cid

    async def record_exchange(self, cid, order):
        """Fetch actual commissions before applying a live fill to inventory."""
        if (not self.paper and order.get('status') in TERMINAL
                and float(order.get('filled') or 0) > 0
                and not (order.get('fees') or order.get('fee'))):
            row = self.db.execute('SELECT * FROM execution_orders WHERE client_id=?',(cid,)).fetchone()
            try:
                fills = await self.exchange.fetch_my_trades(row['symbol'], params={'orderId':order['id']})
                fills = [f for f in fills if str(f.get('order')) == str(order['id'])]
                if abs(sum(float(f['amount']) for f in fills) - float(order['filled'])) > 1e-8:
                    raise ValueError('Incomplete trade/commission history')
                fees = [fee for fill in fills for fee in
                        (fill.get('fees') or ([fill['fee']] if fill.get('fee') else []))]
                if not fees:
                    raise ValueError('Commission details unavailable')
                order = dict(order, fees=fees)
            except Exception:
                self.record(cid, dict(order,status='fee_pending'))
                log.exception('Fill commission unavailable; inventory update deferred for %s',cid)
                return
        self.record(cid, order)

    def record(self, cid, order):
        with self.db:
            self.db.execute('UPDATE execution_orders SET exchange_id=?,status=?,response=? WHERE client_id=?',
                            (str(order.get('id') or cid), order.get('status') or 'unknown', json.dumps(order), cid))
        self.apply(cid)

    def apply(self, cid):
        row = dict(self.db.execute('SELECT * FROM execution_orders WHERE client_id=?', (cid,)).fetchone())
        if row['applied'] or row['status'] not in TERMINAL:
            return
        order = json.loads(row['response'])
        qty = float(order.get('filled') or 0)
        if qty == 0:
            with self.db:
                self.db.execute('UPDATE execution_orders SET applied=1 WHERE client_id=?', (cid,))
            return
        cost = float(order.get('cost') or qty * float(order.get('average') or 0))
        price = cost / qty
        if not all(math.isfinite(x) and x > 0 for x in (qty, cost, price)):
            raise ValueError('Filled order has invalid execution details')
        base, quote = row['symbol'].split('/')
        fees = order.get('fees') or ([order['fee']] if order.get('fee') else [])
        fee, base_fee = 0.0, 0.0
        if not fees:
            # Conservative estimate until exchange supplies commission details.
            fee = cost * self.fee_rate
        for f in fees:
            amount = float(f.get('cost') or 0)
            if f.get('currency') == base:
                base_fee += amount
                fee += amount * price
            elif f.get('currency') == quote:
                fee += amount
            else:
                # Reserve a quote-equivalent estimate for third-token commission.
                fee += cost * self.fee_rate
        context = json.loads(row['context'])
        now = time.time()
        with self.db:
            if row['kind'] == 'entry':
                from core.trade_manager import TrailingStopManager
                net_qty = qty - base_fee
                if net_qty <= 0:
                    raise ValueError('Fees consume filled quantity')
                manager = TrailingStopManager(price, 'buy', context['atr'], context['regime'],
                    sl_override=context['stop'], tp_override=context.get('target'))
                cur = self.db.execute('''INSERT INTO trades
                  (timestamp,symbol,side,price,quantity,reason,pnl,status,stop_loss,take_profit,manager_state,initial_quantity)
                  VALUES (?,?,?,?,?,?,?,'open',?,?,?,?)''',
                  (int(now), row['symbol'], 'buy', price, net_qty, context.get('reason',''), -fee,
                   manager.trailing_stop, manager.tier1_target, json.dumps(vars(manager)), net_qty))
                self.db.execute('UPDATE execution_orders SET trade_id=? WHERE client_id=?', (cur.lastrowid, cid))
                cash_delta, pnl = -(cost + fee - base_fee * price), -fee
            else:
                trade = dict(self.db.execute('SELECT * FROM trades WHERE id=?', (row['trade_id'],)).fetchone())
                sold = qty + base_fee
                if sold > trade['quantity'] + 1e-9:
                    raise ValueError('Exchange sold more than tracked quantity; manual reconciliation required')
                remaining = max(0.0, trade['quantity'] - sold)
                pnl = (price - trade['price']) * qty - fee
                cash_delta = cost - fee + base_fee * price
                manager_state = context.get('manager_state')
                # Only commit target flags once the requested exit is fully filled.
                if qty < row['quantity'] * (1 - 1e-8):
                    manager_state = None
                manager_state = json.dumps(manager_state) if manager_state else trade['manager_state']
                stop = json.loads(manager_state)['trailing_stop'] if manager_state else trade['stop_loss']
                self.db.execute('''UPDATE trades SET quantity=?,pnl=pnl+?,status=?,exit_price=?,
                  closed_at=?,manager_state=?,stop_loss=? WHERE id=?''',
                  (remaining,pnl,'closed' if remaining < 1e-12 else 'open',price,
                   now if remaining < 1e-12 else None,manager_state,stop,row['trade_id']))
            self.db.execute('INSERT INTO cash_events VALUES (?,?,?,?,?)', (cid,now,cash_delta,pnl,fee))
            self.db.execute('UPDATE execution_orders SET applied=1 WHERE client_id=?', (cid,))

    async def reconcile(self):
        healthy = True
        for row in self.pending():
            if row['status'] in TERMINAL:
                self.apply(row['client_id'])
                continue
            if self.paper:
                raise RuntimeError('Incomplete paper transaction requires review')
            try:
                if row['exchange_id']:
                    order = await self.exchange.fetch_order(row['exchange_id'], row['symbol'])
                else:
                    order = await self.exchange.fetch_order(None, row['symbol'],
                        {'origClientOrderId': row['client_id']})
                await self.record_exchange(row['client_id'], order)
                if order.get('status') == 'open' and row['kind'] != 'protect':
                    fresh = dict(self.db.execute('SELECT * FROM execution_orders WHERE client_id=?',
                                                 (row['client_id'],)).fetchone())
                    await self.cancel(fresh)
            except Exception:
                healthy = False
                log.exception('Cannot reconcile %s; new entries remain blocked', row['client_id'])
        return healthy

    async def cancel(self, row):
        # Always fetch final state: cancel can race a fill.
        await self.exchange.cancel_order(row['exchange_id'], row['symbol'])
        order = await self.exchange.fetch_order(row['exchange_id'], row['symbol'])
        await self.record_exchange(row['client_id'], order)
        if order.get('status') not in TERMINAL:
            raise RuntimeError('Cancellation not yet confirmed')
        if self.db.execute('SELECT applied FROM execution_orders WHERE client_id=?',
                           (row['client_id'],)).fetchone()[0] != 1:
            raise RuntimeError('Canceled order fill accounting is unresolved')

    async def release_protection(self, trade_id):
        for row in self.pending(trade_id):
            if row['kind'] != 'protect' or row['status'] != 'open':
                raise RuntimeError('Position has unresolved execution')
            await self.cancel(row)

    async def protect(self, trade):
        if self.paper or self.pending(trade['id']):
            return
        cid = await self.submit(trade['symbol'], 'sell', trade['quantity'], trade['stop_loss'],
                               'protect', trade['id'])
        if cid is None:
            raise RuntimeError('Protection outcome unknown; reconcile before further orders')
        row = self.db.execute('SELECT status FROM execution_orders WHERE client_id=?',(cid,)).fetchone()
        if row['status'] == 'rejected':
            # Definite rejection means no competing stop: reduce unprotected exposure.
            ticker = await self.exchange.fetch_ticker(trade['symbol'])
            await self.submit(trade['symbol'],'sell',trade['quantity'],float(ticker['last']),
                              'exit',trade['id'])
            raise RuntimeError('Protective stop rejected; emergency exit submitted')
