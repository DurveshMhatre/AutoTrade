import pytest
import sqlite3
from core.database import init_db, save_autopsy, get_recent_autopsies, save_trade

def test_autopsy_database_schema(tmp_path):
    """Test the autopsies table allows inserting and retrieving."""
    db_file = tmp_path / "test.db"
    db = init_db(db_file)
    
    # Need a trade first due to foreign key
    trade = {
        "timestamp": 1000,
        "symbol": "BTC/USDT",
        "side": "buy",
        "price": 50000,
        "quantity": 1.0,
        "reason": "Test",
        "pnl": 0.0,
        "status": "closed",
        "stop_loss": 49000,
        "take_profit": 52000
    }
    save_trade(db, trade)
    
    # Find the trade ID
    trade_id = db.execute("SELECT id FROM trades LIMIT 1").fetchone()["id"]
    
    autopsy = {
        "timestamp": 1001,
        "trade_id": trade_id,
        "entry_grade": "A",
        "exit_quality": "good",
        "root_cause": None,
        "lesson": "Hold winners longer.",
        "pattern_detected": 0,
        "pattern_description": None
    }
    
    save_autopsy(db, autopsy)
    
    results = get_recent_autopsies(db)
    assert len(results) == 1
    assert results[0]["entry_grade"] == "A"
    assert results[0]["lesson"] == "Hold winners longer."
