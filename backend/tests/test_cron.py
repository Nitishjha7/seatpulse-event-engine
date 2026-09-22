"""
/api/cron/tick — the manual job runner for deployments with no persistent
worker process. See routers/cron.py.
"""


def test_tick_rejects_missing_secret(client):
    res = client.post("/api/cron/tick")
    assert res.status_code == 401


def test_tick_rejects_wrong_secret(client):
    """
    CRON_SECRET is empty in the test environment (same as docker-compose,
    which runs a real worker and has no use for this endpoint) — so no
    header value, correct-looking or not, should be accepted.
    """
    res = client.post("/api/cron/tick", headers={"X-Cron-Secret": "anything"})
    assert res.status_code == 401
