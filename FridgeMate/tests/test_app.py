from datetime import date, timedelta
import sqlite3
import pytest
import app as module


@pytest.fixture
def env(tmp_path):
    app = module.create_app(tmp_path/'test.sqlite3', seed=False)
    app.config['TESTING'] = True
    client = app.test_client()
    client.get('/')
    return app, client


def post(client, path, data):
    with client.session_transaction() as session:
        token = session['csrf']
    return client.post(path, data=dict(data, csrf=token))


def add(client, **overrides):
    data = dict(name='우유', category='유제품', quantity='3', storage_location='냉장실 위 칸', expiration_date=(date.today()+timedelta(days=2)).isoformat())
    data.update(overrides)
    return post(client, '/add', data)


def rows(app, sql):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        return conn.execute(sql).fetchall()


@pytest.mark.parametrize('days,status', [(-1,'expired'),(0,'red'),(1,'red'),(2,'red'),(3,'yellow'),(6,'yellow'),(7,'green')])
def test_deadline(monkeypatch, days, status):
    monkeypatch.setattr(module, 'today', lambda: date(2026,10,25))
    assert module.deadline((date(2026,10,25)+timedelta(days=days)).isoformat()) == (days,status)


@pytest.mark.parametrize('qty', ['0','-1','1.5','abc','10000',''])
def test_invalid_quantity(env, qty):
    app,c = env
    assert add(c,quantity=qty).status_code == 400
    assert not rows(app,'SELECT * FROM foods')


def test_validation(env):
    app,c = env
    for fields in [dict(name=' '),dict(expiration_date='2026-02-30'),dict(storage_location='침실'),dict(category='없는 분류')]:
        assert add(c,**fields).status_code == 400
    assert c.post('/add',data={}).status_code == 400


def test_end_to_end_and_persistence(env):
    app,c = env
    assert add(c).status_code == 302
    for path in ['/', '/fridge', '/food/1', '/notifications', '/add', '/search?q=우유']:
        response = c.get(path)
        assert response.status_code == 200
        assert '우유' in response.get_data(as_text=True) or path=='/add'
    assert post(c,'/food/1/process',dict(action='consumed',quantity=4)).status_code == 400
    assert post(c,'/food/1/process',dict(action='consumed',quantity=-1)).status_code == 400
    assert post(c,'/food/1/process',dict(action='consumed',quantity=1)).status_code == 302
    assert rows(app,'SELECT quantity FROM foods') == [(2,)]
    assert rows(app,'SELECT is_read,processed FROM notifications') == [(1,0)]
    assert post(c,'/food/1/update',dict(storage_location='냉동실',opened='on')).status_code == 302
    assert rows(app,'SELECT opened,storage_location FROM foods') == [(1,'냉동실')]
    fresh = module.create_app(app.config['DATABASE'],seed=False).test_client()
    assert '우유' in fresh.get('/fridge?location=냉동실').get_data(as_text=True)
    assert post(c,'/food/1/process',dict(action='discarded',quantity=2,reason='변질')).status_code == 302
    assert rows(app,'SELECT quantity FROM foods') == [(0,)]
    assert rows(app,'SELECT processed FROM notifications') == [(1,)]
    assert rows(app,'SELECT action,quantity,reason FROM history') == [('consumed',1,None),('discarded',2,'변질')]
    assert c.get('/food/1').status_code == 404
    assert '검색 결과 · 0건' in c.get('/search?q=우유').get_data(as_text=True)
    assert '변질' in c.get('/notifications').get_data(as_text=True)


def test_filter_sort_search_and_read(env):
    app,c = env
    add(c,name='나중식품',expiration_date='2099-01-01')
    add(c,name='먼저식품',expiration_date='2020-01-01')
    add(c,name='냉동식품',storage_location='냉동실')
    html = c.get('/fridge').get_data(as_text=True)
    assert html.index('먼저식품') < html.index('나중식품')
    assert '냉동식품' not in html
    assert '냉동식품' in c.get('/fridge?location=냉동실').get_data(as_text=True)
    assert '먼저식품' in c.get('/search?q=먼저').get_data(as_text=True)
    assert '이 식품 등록하기' in c.get('/search?q=없는식품').get_data(as_text=True)
    assert post(c,'/notifications/2/read',{}).status_code == 302
    assert rows(app,'SELECT is_read FROM notifications WHERE food_id=2') == [(1,)]
    assert post(c,'/food/2/process',dict(action='discarded',quantity=3,reason='소비기한 경과')).status_code == 302


def test_seed_only_once(tmp_path):
    path = tmp_path/'demo.sqlite3'
    app = module.create_app(path)
    assert len(rows(app,'SELECT * FROM foods')) == 6
    module.create_app(path)
    assert len(rows(app,'SELECT * FROM foods')) == 6


def test_reference_controls(env):
    app,c = env
    add(c)
    assert post(c,'/notifications/read-all',{}).status_code == 302
    assert rows(app,'SELECT is_read FROM notifications') == [(1,)]
    html=c.get('/add').get_data(as_text=True)
    assert 'type="radio" name="storage_location"' in html
    assert '온도 미연동' in c.get('/fridge').get_data(as_text=True)
    assert '정상 작동' not in c.get('/').get_data(as_text=True)
