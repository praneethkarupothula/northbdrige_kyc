from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select

from app.models.entities import TS
from tests.helpers import start


def test_sqlite_timestamp_roundtrip_restores_utc():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    table = Table("timestamps", metadata, Column("id", Integer, primary_key=True), Column("created_at", TS))
    metadata.create_all(engine)
    local = datetime(2026, 11, 11, 13, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    with engine.begin() as conn:
        conn.execute(table.insert(), [{"id": 1, "created_at": local},
                                      {"id": 2, "created_at": datetime(2026, 11, 11, 8)}])
        dates = conn.scalars(select(table.c.created_at).order_by(table.c.id)).all()
    assert dates == [datetime(2026, 11, 11, 8, tzinfo=UTC)] * 2
    assert all(d.tzinfo is not None for d in dates)
    engine.dispose()


def test_application_api_includes_timestamp_offset(applicant):
    a = applicant()
    app = start(a)
    refreshed = a.get(f"/v1/applications/{app['id']}").json()
    date = datetime.fromisoformat(refreshed["created_at"].replace("Z", "+00:00"))
    assert date.utcoffset() == timedelta(0)
