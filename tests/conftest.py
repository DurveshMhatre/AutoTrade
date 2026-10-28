"""Tests must never use credentials or external network connections."""
import socket
import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    import config
    monkeypatch.setattr(config, 'DRY_RUN', True)
    monkeypatch.setattr(config, 'USE_AI_AGENTS', False)
    monkeypatch.setattr(config, 'LLM_REVIEW_PROVIDER', 'none')
    for key in ('BINANCE_API_KEY','BINANCE_SECRET','ANTHROPIC_API_KEY','GROQ_API_KEY',
                'TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','CRYPTOPANIC_API_KEY'):
        monkeypatch.setenv(key, '')
    original = socket.socket.connect
    def blocked(sock, address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'):
            return original(sock, address, *args, **kwargs)
        raise RuntimeError('External network disabled in offline tests')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
