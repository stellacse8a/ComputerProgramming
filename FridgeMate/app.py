import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

LOCATIONS = ('냉장실 위 칸', '냉장실 아래 칸', '냉동실')
CATEGORIES = ('채소', '과일', '유제품', '육류', '수산물', '반찬', '기타')
REASONS = ('소비기한 경과', '변질', '보관 실수', '기타')
ICONS = {'채소':'🥬', '과일':'🍎', '유제품':'🥛', '육류':'🥩', '수산물':'🐟', '반찬':'🍱', '기타':'🥚'}


def today():
    return date.today()


def deadline(expiration):
    days = (date.fromisoformat(expiration) - today()).days
    return days, 'expired' if days < 0 else 'red' if days <= 2 else 'yellow' if days <= 6 else 'green'


def db():
    if 'db' not in g:
        g.db = sqlite3.connect(g.app_database, timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db


def create_app(database=None, seed=True):
    app = Flask(__name__)
    app.config.update(DATABASE=str(database or Path(__file__).parent / 'instance' / 'fridgemate.sqlite3'), SECRET_KEY=os.environ.get('FRIDGEMATE_SECRET', secrets.token_hex(32)))
    Path(app.config['DATABASE']).parent.mkdir(parents=True, exist_ok=True)

    @app.before_request
    def prepare():
        g.app_database = app.config['DATABASE']
        session.setdefault('csrf', secrets.token_hex(24))
        if request.method == 'POST' and request.form.get('csrf') != session['csrf']:
            abort(400, '잘못된 요청입니다. 페이지를 새로고침해주세요.')

    @app.teardown_appcontext
    def close(error):
        conn = g.pop('db', None)
        if conn is not None:
            conn.close()

    with app.app_context():
        g.app_database = app.config['DATABASE']
        conn = db()
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS foods (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity >= 0), storage_location TEXT NOT NULL,
            expiration_date TEXT NOT NULL, opened INTEGER NOT NULL DEFAULT 0 CHECK(opened IN (0,1)),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY, food_id INTEGER REFERENCES foods(id), food_name TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('consumed','discarded')), quantity INTEGER NOT NULL CHECK(quantity > 0),
            reason TEXT, timestamp TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notifications (
            food_id INTEGER PRIMARY KEY REFERENCES foods(id), is_read INTEGER NOT NULL DEFAULT 0,
            processed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        ''')
        if seed and not conn.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
            now = datetime.now().isoformat(timespec='seconds')
            for name, cat, qty, loc, days, opened in [('우유','유제품',2,LOCATIONS[0],2,1),('딸기','과일',1,LOCATIONS[0],1,0),('시금치','채소',2,LOCATIONS[1],4,0),('닭가슴살','육류',3,LOCATIONS[2],15,0),('달걀','기타',6,LOCATIONS[1],8,0),('두부','반찬',1,LOCATIONS[0],-1,0)]:
                cur = conn.execute('INSERT INTO foods(name,category,quantity,storage_location,expiration_date,opened,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)', (name,cat,qty,loc,(today()+timedelta(days=days)).isoformat(),opened,now,now))
                conn.execute('INSERT INTO notifications(food_id) VALUES(?)', (cur.lastrowid,))
            conn.execute("INSERT INTO metadata VALUES('seeded','1')")
        conn.commit()

    def foods():
        rows = db().execute('SELECT f.*, n.is_read, n.processed FROM foods f JOIN notifications n ON n.food_id=f.id WHERE quantity>0 ORDER BY expiration_date,id').fetchall()
        return [dict(row, days=deadline(row['expiration_date'])[0], status=deadline(row['expiration_date'])[1]) for row in rows]

    def food(food_id):
        row = db().execute('SELECT * FROM foods WHERE id=? AND quantity>0',(food_id,)).fetchone()
        if row is None:
            abort(404)
        return dict(row, days=deadline(row['expiration_date'])[0], status=deadline(row['expiration_date'])[1])

    @app.context_processor
    def shared():
        return dict(locations=LOCATIONS, categories=CATEGORIES, reasons=REASONS, icons=ICONS, today=today(), unread=db().execute('SELECT count(*) FROM foods f JOIN notifications n ON f.id=n.food_id WHERE f.quantity>0 AND f.expiration_date<=? AND n.is_read=0 AND n.processed=0',((today()+timedelta(days=6)).isoformat(),)).fetchone()[0])

    @app.get('/')
    def home():
        items = foods()
        return render_template('home.html', tab='home', items=items, urgent=[f for f in items if f['days']<=2], week=[f for f in items if 0<=f['days']<=6], total=sum(f['quantity'] for f in items))

    @app.get('/fridge')
    def fridge():
        location = request.args.get('location','냉장실')
        if location not in ('냉장실','냉동실','전체'):
            abort(400)
        items = [f for f in foods() if location=='전체' or f['storage_location'].startswith(location)]
        return render_template('fridge.html', tab='fridge', items=items, location=location, total=sum(f['quantity'] for f in items), urgent_count=sum(f['quantity'] for f in items if f['days']<=2), consumed_week=db().execute("SELECT COALESCE(SUM(quantity),0) FROM history WHERE action='consumed' AND timestamp>=?", ((today()-timedelta(days=today().weekday())).isoformat(),)).fetchone()[0])

    @app.route('/add', methods=['GET','POST'])
    def add():
        if request.method == 'POST':
            try:
                name = request.form.get('name','').strip()
                qty = int(request.form.get('quantity',''))
                expiry = request.form.get('expiration_date','')
                parsed = date.fromisoformat(expiry)
                category = request.form.get('category')
                location = request.form.get('storage_location')
                if not name or len(name)>80 or not 1<=qty<=9999 or parsed.isoformat()!=expiry or category not in CATEGORIES or location not in LOCATIONS:
                    raise ValueError
            except (ValueError, TypeError):
                return render_template('add.html', tab='add', error='이름, 수량(1~9999), 날짜와 보관 위치를 확인해주세요.'), 400
            now = datetime.now().isoformat(timespec='seconds')
            with db():
                cur = db().execute('INSERT INTO foods(name,category,quantity,storage_location,expiration_date,opened,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(name,category,qty,location,expiry,int(request.form.get('opened')=='on'),now,now))
                db().execute('INSERT INTO notifications(food_id) VALUES(?)',(cur.lastrowid,))
            flash('식품을 냉장고에 담았어요.')
            return redirect(url_for('fridge'))
        return render_template('add.html', tab='add')

    @app.get('/food/<int:food_id>')
    def detail(food_id):
        return render_template('detail.html', tab='fridge', food=food(food_id))

    @app.post('/food/<int:food_id>/update')
    def update(food_id):
        food(food_id)
        loc = request.form.get('storage_location')
        if loc not in LOCATIONS:
            abort(400)
        with db():
            db().execute('UPDATE foods SET storage_location=?,opened=?,updated_at=? WHERE id=?',(loc,int(request.form.get('opened')=='on'),datetime.now().isoformat(timespec='seconds'),food_id))
        flash('보관 정보를 변경했어요.')
        return redirect(url_for('detail',food_id=food_id))

    @app.post('/food/<int:food_id>/process')
    def process(food_id):
        action = request.form.get('action')
        reason = request.form.get('reason','')
        try:
            qty = int(request.form.get('quantity',''))
            if qty<=0 or action not in ('consumed','discarded') or (action=='discarded' and reason not in REASONS):
                raise ValueError
        except (ValueError,TypeError):
            abort(400, '처리할 수량과 이유를 확인해주세요.')
        conn = db()
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            row = food(food_id)
            if qty>row['quantity']:
                abort(400,'현재 수량보다 많이 처리할 수 없어요.')
            now = datetime.now().isoformat(timespec='seconds')
            conn.execute('UPDATE foods SET quantity=quantity-?,updated_at=? WHERE id=?',(qty,now,food_id))
            conn.execute('INSERT INTO history(food_id,food_name,action,quantity,reason,timestamp) VALUES(?,?,?,?,?,?)',(food_id,row['name'],action,qty,reason if action=='discarded' else None,now))
            conn.execute('UPDATE notifications SET is_read=1,processed=? WHERE food_id=?',(int(qty==row['quantity']),food_id))
        flash(f"{row['name']} {qty}개를 {'소비' if action=='consumed' else '폐기'}했어요.")
        return redirect(url_for('notifications'))

    @app.get('/notifications')
    def notifications():
        return render_template('notifications.html',tab='notifications', items=[f for f in foods() if f['days']<=6], history=db().execute('SELECT * FROM history ORDER BY id DESC').fetchall())

    @app.post('/notifications/<int:food_id>/read')
    def read_notification(food_id):
        food(food_id)
        with db():
            db().execute('UPDATE notifications SET is_read=1 WHERE food_id=?',(food_id,))
        return redirect(url_for('notifications'))

    @app.post('/notifications/read-all')
    def read_all():
        with db():
            db().execute('UPDATE notifications SET is_read=1 WHERE food_id IN (SELECT id FROM foods WHERE quantity>0 AND expiration_date<=?)',((today()+timedelta(days=6)).isoformat(),))
        return redirect(url_for('notifications'))

    @app.get('/search')
    def search():
        q = request.args.get('q','').strip()
        return render_template('search.html',tab='fridge',q=q,items=[f for f in foods() if q.casefold() in f['name'].casefold()] if q else [])

    return app

if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=int(os.environ.get('PORT',5000)), debug=False)
