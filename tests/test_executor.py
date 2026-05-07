import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from execution.executor import smart_entry, cancel_all_open_orders

@pytest.mark.asyncio
async def test_smart_entry_success():
    """Test smart_entry splits the order correctly."""
    
    mock_exchange = AsyncMock()
    mock_exchange.create_limit_order.return_value = {"id": "123", "status": "open"}
    
    with patch("execution.executor._build_exchange", return_value=mock_exchange), \
         patch("execution.executor._close_exchange", new_callable=AsyncMock):
        
        # entry=60000, atr=1000, qty=1.0
        # Buy -> T1: 60000(0.5), T2: 59900(0.3), T3: 59800(0.2)
        orders = await smart_entry("buy", "BTC/USDT", 1.0, 60000, 1000, testnet=True)
        
        assert len(orders) == 3
        
        # Verify calls to exchange
        calls = mock_exchange.create_limit_order.call_args_list
        assert len(calls) == 3
        
        # Call 1 (T1)
        kwargs1 = calls[0].kwargs
        assert kwargs1["price"] == 60000.0
        assert kwargs1["amount"] == 0.5
        
        # Call 2 (T2)
        kwargs2 = calls[1].kwargs
        assert kwargs2["price"] == 59900.0
        assert kwargs2["amount"] == 0.3
        
        # Call 3 (T3)
        kwargs3 = calls[2].kwargs
        assert kwargs3["price"] == 59800.0
        assert kwargs3["amount"] == 0.2

@pytest.mark.asyncio
async def test_smart_entry_skips_small_orders():
    """Test smart_entry skips micro orders below Binance limit."""
    
    mock_exchange = AsyncMock()
    
    with patch("execution.executor._build_exchange", return_value=mock_exchange), \
         patch("execution.executor._close_exchange", new_callable=AsyncMock):
        
        # qty=0.0002. T1=0.0001, T2=0.00006 (skip), T3=0.00004 (skip)
        orders = await smart_entry("buy", "BTC/USDT", 0.0002, 60000, 1000, testnet=True)
        
        # Should only place 1 order
        assert len(mock_exchange.create_limit_order.call_args_list) == 1
        assert len(orders) == 1
