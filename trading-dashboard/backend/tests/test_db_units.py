from backend.services import db


def test_timestamp_and_original_quantity():
    result = db.normalize_row({'timestamp':1700000000,'closed_at':1700000300,
                               'status':'closed','quantity':0,'initial_quantity':0.2})
    assert result['timestamp'] == 1700000300000
    assert result['quantity'] == 0.2
    assert db.normalize_row({'timestamp':1700000000000})['timestamp'] == 1700000000000


def test_readonly_connection_does_not_create_missing_database(tmp_path, monkeypatch):
    path = tmp_path/'missing.db'
    monkeypatch.setattr(db,'DB_PATH',str(path))
    assert db.get_connection() is None
    assert not path.exists()
