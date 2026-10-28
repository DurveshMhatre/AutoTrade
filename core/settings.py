"""Fail-fast configuration and single-process ownership of the local ledger."""
from contextlib import contextmanager
import math
import os
from pathlib import Path


def validate_settings(cfg):
    if not isinstance(cfg.SYMBOLS, list) or not cfg.SYMBOLS or len(set(cfg.SYMBOLS)) != len(cfg.SYMBOLS):
        raise ValueError('SYMBOLS must contain unique pairs')
    if any(not isinstance(s, str) or not s.endswith('/USDT') for s in cfg.SYMBOLS):
        raise ValueError('This spot runtime supports USDT-quoted pairs')
    if cfg.TIMEFRAME != '5m':
        raise ValueError('Runtime currently requires TIMEFRAME=5m')
    for name in ['RISK_PER_TRADE_PCT','DAILY_LOSS_LIMIT_PCT','STOP_LOSS_PCT',
                 'TAKE_PROFIT_PCT','MAX_ALLOCATION_PCT','MAX_PORTFOLIO_RISK_PCT']:
        value = getattr(cfg,name)
        if not isinstance(value,(int,float)) or not math.isfinite(value) or not 0 < value < 1:
            raise ValueError(f'{name} must be a finite fraction between zero and one')
    if not isinstance(cfg.MAX_POSITIONS,int) or cfg.MAX_POSITIONS < 1:
        raise ValueError('MAX_POSITIONS must be a positive integer')
    for name in ('FEE_RATE','SLIPPAGE_PCT','MIN_CONFIDENCE'):
        value = getattr(cfg,name)
        if not isinstance(value,(int,float)) or not math.isfinite(value) or not 0 <= value < 1:
            raise ValueError(f'{name} must be a finite fraction between zero and one')
    if not math.isfinite(cfg.PAPER_BALANCE) or cfg.PAPER_BALANCE <= 0:
        raise ValueError('PAPER_BALANCE must be positive')
    for name in ('DRY_RUN','BINANCE_TESTNET'):
        if os.getenv(name, 'true').lower() not in ('true','false'):
            raise ValueError(f'{name} must be true or false')
    if not cfg.DRY_RUN and not all(os.getenv(k) for k in ('BINANCE_API_KEY','BINANCE_SECRET')):
        raise ValueError('Exchange credentials are required outside paper mode')
    if cfg.LLM_REVIEW_PROVIDER not in ('none','groq'):
        raise ValueError('LLM_REVIEW_PROVIDER must be none or groq')
    if cfg.LLM_REVIEW_PROVIDER == 'groq' and not (os.getenv('GROQ_API_KEY') and cfg.LLM_REVIEW_MODEL):
        raise ValueError('Groq review requires GROQ_API_KEY and LLM_REVIEW_MODEL')


@contextmanager
def process_lock():
    path = Path(__file__).resolve().parents[1] / '.bot.lock'
    with path.open('a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b'0')
                handle.flush()
                handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
