from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from pulsecheck.api import create_app
from pulsecheck.models import MonitorInput, ProbeResult
from pulsecheck.store import Store


def test_analytics_filters_window_and_excludes_failures_from_latency(tmp_path):
    store=Store(tmp_path/'metrics.db');store.initialize()
    monitor=store.add(MonitorInput(name='API',url='http://localhost'))
    now=datetime.now(timezone.utc)
    for minutes,ok,latency in [(120,True,999),(30,True,10),(20,False,500),(10,True,30),(-10,True,999)]:
        store.record(ProbeResult(monitor['id'],(now-timedelta(minutes=minutes)).isoformat(),ok,latency,200 if ok else 503,1))
    report=store.analytics(1,now.isoformat());m=report['monitors'][0]
    assert m['checks']==3 and m['failures']==1
    assert m['average_ms']==20 and m['p95_ms']==30
    assert m['success_rate']==66.67
    assert sum(b['samples'] for b in m['buckets'])==3
    assert len(m['buckets'])==24


def test_empty_analytics_never_claims_success_or_zero_latency(tmp_path):
    store=Store(tmp_path/'empty.db');store.initialize();store.add(MonitorInput(name='API',url='http://localhost'))
    m=store.analytics()['monitors'][0]
    assert m['checks']==0
    assert m['success_rate'] is None and m['average_ms'] is None and m['p95_ms'] is None
    assert all(b['avg_ms'] is None and b['samples']==0 for b in m['buckets'])


def test_retention_keeps_open_incidents_and_recent_history(tmp_path):
    store=Store(tmp_path/'retention.db');store.initialize();m=store.add(MonitorInput(name='API',url='http://localhost'))
    old=(datetime.now(timezone.utc)-timedelta(days=10)).isoformat()
    recent=datetime.now(timezone.utc).isoformat()
    store.record(ProbeResult(m['id'],old,False,0,503,1),failure_threshold=1)
    store.prune(7)
    assert not store.history(m['id'])
    assert len(store.incidents())==1 and store.incidents()[0]['resolved_at'] is None
    store.record(ProbeResult(m['id'],recent,True,20,200,1),recovery_threshold=1)
    assert store.history(m['id'])[0]['latency_ms']==20
    assert store.incidents()[0]['resolved_at'] is not None


def test_detail_and_window_api_validation(tmp_path):
    app=create_app(db_path=tmp_path/'api.db',demo=True,scheduler=False,transport=httpx.MockTransport(lambda request:httpx.Response(200)))
    with TestClient(app) as c:
        for value in [0,169,'bad']:
            assert c.get('/api/analytics',params={'hours':value}).status_code==422
        assert c.get('/api/monitors/999').status_code==404
        assert c.post('/api/checks/run').status_code==200
        detail=c.get('/api/monitors/1').json()
        assert detail['monitor']['name']=='Application API'
        assert len(detail['checks'])==1
        assert c.get('/api/analytics?hours=6').json()['window_hours']==6
