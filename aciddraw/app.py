#!/usr/bin/env python3
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# aciddraw/app.py

import functools
import json
import os
import queue
import threading
import time

from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import arcade
import canvas
import duel
import hub
import live
import words
from auth import ARCADE_ADMINS, USERNAME_RE, arcade_account, arcade_mirror, arcade_poll, arcade_presence, attempt, blocked, captcha_create, captcha_verify, create_session, delete_session, request_token, request_user, token_user
from storage import BOARDS, DB_LOCK, START_COINS, db_connect, init_db, leaderboard, packs_of, profile, prune_sessions, signup_allowed, spend


app = Flask(__name__)

CACHE_VERSION = int(time.time())
PROXIES       = int(os.environ.get('DRAW_PROXIES', '0')) # reverse proxies in front of the app, so the real client IP is used
ADMINS        = {n.strip().lower() for n in os.environ.get('DRAW_ADMINS', '').split(',') if n.strip()}
PUBLIC_URL    = os.environ.get('DRAW_PUBLIC_URL', '').rstrip('/') # main address for crawlers and share cards, e.g. https://draw.acid.vegas
PING_EVERY    = 15   # seconds between keep-alive comments on an idle event stream
DB_EVERY      = 15   # seconds between refreshing database backed parts of an idle event stream
IP_STREAMS    = 20   # event streams one IP address can have open at once
JOIN_FAILS    = {}   # IP address -> times of recent joins with a wrong room code
JOIN_FAIL_MAX = 10   # wrong room codes one IP address may try per minute, so private rooms can not be found by guessing

if PROXIES:
	app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXIES, x_proto=PROXIES, x_host=PROXIES)


def admin_required(fn):
	'''Only let admins through.'''

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		user_id, username = request_user()
		if not user_id or not is_admin(user_id, username):
			return api_error('admins only', 403)
		return fn(user_id, *args, **kwargs)
	return wrapper


def api_error(message: str, status: int = 400) -> tuple:
	'''Return a JSON error response.'''

	return jsonify({'error': message}), status


@app.errorhandler(arcade.ArcadeDown)
def arcade_down(_: Exception) -> tuple:
	'''Fail hard when acid-arcade-db can not be reached instead of treating everyone as a guest.'''

	return api_error('account server is down', 503)


def arcade_reply(res: dict) -> Response:
	'''Answer a sign in through acid-arcade-db like a local login, and share it with the other sites on this domain.'''

	arcade_mirror(res['user_id'], res['username'])
	resp = jsonify({'token': res['token'], 'username': res['username']})
	arcade.set_cookie(resp, request.host, res['token'])
	return resp


def game_reply(game_id: int, user_id: int, error: str = None, extra: dict = None) -> tuple:
	'''Answer a game action with the new game view, or the error, and wake both players.'''

	if error:
		return api_error(error)
	hub.notify(users=duel.players(game_id))
	return jsonify({'game': duel.view(user_id, game_id), **(extra or {})})


def guest_name(user_id: int, username: str, name: str) -> tuple:
	'''Return (name to use in a live room, error). Signed in players always use their username.'''

	if user_id:
		return username, None
	name = (name or '').strip()
	if not USERNAME_RE.match(name):
		return None, 'pick a name: 3-16 letters, numbers or _'
	if arcade.enabled() and arcade_account(name):
		return None, 'that name belongs to an account, sign in or pick another'
	with db_connect() as c:
		if c.execute('SELECT 1 FROM users WHERE username=?', (name,)).fetchone():
			return None, 'that name belongs to an account, sign in or pick another'
	return name, None


def is_admin(user_id: int, username: str) -> bool:
	'''Return True if the player can manage the word list: named in DRAW_ADMINS, or an acid-arcade-db admin.'''

	return bool(username) and (username.lower() in ADMINS or user_id in ARCADE_ADMINS)


def login_required(fn):
	'''Only let signed in players through, passing their user id and name.'''

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		user_id, username = request_user()
		if not user_id:
			return api_error('sign in to play', 401)
		return fn(user_id, username, *args, **kwargs)
	return wrapper


def me_view(user_id: int, username: str, games: list = None) -> dict:
	'''Return the signed in player's coins and stats, plus how many games are waiting on them.'''

	out = profile(user_id, is_admin(user_id, username))
	if out:
		out['turns'] = sum(1 for g in (games if games is not None else duel.list_games(user_id)) if g['step'] in ('guess', 'draw'))
	return out


def payload() -> dict:
	'''Return the JSON body of the request.'''

	return request.get_json(silent=True) or {}


def public_root() -> str:
	'''Return the site root for canonical and share URLs: DRAW_PUBLIC_URL when set, otherwise the address the request came in on.'''

	return PUBLIC_URL + '/' if PUBLIC_URL else request.url_root


def room_action(fn):
	'''Find the live room and the player's seat named in the request, run the action under the lock and wake the room.'''

	@functools.wraps(fn)
	def wrapper(code: str):
		data = payload()
		with hub.LOCK:
			room = hub.ROOMS.get(code.upper())
			pid  = data.get('pid') or ''
			if not room or pid not in room.players:
				return api_error('you are not in that room', 404)
			error = fn(room, pid, data)
			if error:
				return api_error(error)
			hub.notify(room=room.code)
			return jsonify({'ok': True})
	return wrapper


def rooms_list() -> list:
	'''Return the public live rooms, ones still waiting first.'''

	rooms = [r.listing() for r in hub.ROOMS.values() if r.public and r.players]
	rooms.sort(key=lambda r: (r['state'] != 'lobby', -r['players']))
	return rooms[:50]


def setup():
	'''Open the database, load the built-in words and start the room clock.'''

	init_db()
	prune_sessions()
	words.load_builtin()
	hub.start(duel.expire)
	if arcade.enabled():
		threading.Thread(target=arcade_poll, daemon=True).start()
		threading.Thread(target=arcade_presence, daemon=True).start()


@app.route('/')
def index():
	'''Serve the page.'''

	url = public_root()
	ld  = {
		'@context'            : 'https://schema.org',
		'@type'               : ['VideoGame', 'WebApplication'],
		'name'                : 'acid draw',
		'url'                 : url,
		'description'         : 'Draw a word, your friend guesses it. Turn based 1v1 games with streaks and coins, plus live rooms where everyone races to guess. Free and open source.',
		'image'               : url + 'static/og.png',
		'applicationCategory' : 'GameApplication',
		'operatingSystem'     : 'Any',
		'genre'               : ['Drawing', 'Party', 'Word'],
		'gamePlatform'        : 'Web browser',
		'playMode'            : ['MultiPlayer'],
		'isAccessibleForFree' : True,
		'offers'              : {'@type': 'Offer', 'price': '0', 'priceCurrency': 'USD'},
		'author'              : {'@type': 'Person', 'name': 'acidvegas', 'url': 'https://github.com/acidvegas'}
	}
	return render_template('index.html', cache_v=CACHE_VERSION, url=url, ld=json.dumps(ld).replace('</', '<\\/'))


@app.route('/admin/words')
@admin_required
def admin_words(_: int):
	'''List words for the admin page.'''

	return jsonify(words.admin_list(request.args.get('status') or 'pending', request.args.get('q') or '', request.args.get('offset', 0, type=int)))


@app.route('/admin/words/<int:word_id>', methods=['POST'])
@admin_required
def admin_word(_: int, word_id: int):
	'''Approve, reject, ban, retier or delete a word.'''

	data  = payload()
	error = words.admin_set(word_id, data.get('action'), data.get('tier'))
	return api_error(error) if error else jsonify({'ok': True})


@app.route('/admin/words/add', methods=['POST'])
@admin_required
def admin_word_add(user_id: int):
	'''Add a word straight into the main pool.'''

	data  = payload()
	error = words.admin_add(data.get('text'), data.get('tier'), user_id)
	return api_error(error) if error else jsonify({'ok': True})


@app.route('/auth/captcha')
def auth_captcha():
	'''Return a new captcha.'''

	if arcade.enabled():
		return jsonify(arcade.captcha())
	if blocked('captcha', request.remote_addr):
		return api_error('too many captchas, wait a minute', 429)
	attempt('captcha', request.remote_addr)
	return jsonify(captcha_create())


@app.route('/auth/login', methods=['POST'])
def auth_login():
	'''Log in with username and password.'''

	data = payload()
	if arcade.enabled():
		try:
			res = arcade.login(data.get('username') or '', data.get('password') or '', request.remote_addr)
		except arcade.ArcadeError as e:
			return api_error(str(e), e.status)
		return arcade_reply(res)
	name = (data.get('username') or '').lower()
	if blocked('account', name) or blocked('ip', request.remote_addr):
		return api_error('too many failed logins, try again in 15 minutes', 429)
	with db_connect() as c:
		row = c.execute('SELECT id, username, password_hash FROM users WHERE username=?', (data.get('username') or '',)).fetchone()
	if not row or not check_password_hash(row['password_hash'], data.get('password') or ''):
		attempt('account', name)
		attempt('ip', request.remote_addr)
		return api_error('wrong username or password', 401)
	return jsonify({'token': create_session(row['id']), 'username': row['username']})


@app.route('/auth/logout', methods=['POST'])
def auth_logout():
	'''Log out the current session.'''

	if arcade.enabled():
		arcade.logout(request_token())
		resp = jsonify({'ok': True})
		arcade.clear_cookie(resp, request.host)
		return resp
	delete_session(request_token())
	return jsonify({'ok': True})


@app.route('/auth/me')
def auth_me():
	'''Return the signed in player, if any, and the rules the page needs.'''

	user_id, username = request_user()
	resp = jsonify({
		'user'   : me_view(user_id, username) if user_id else None,
		'size'   : canvas.SIZE,
		'sizes'  : canvas.SIZES,
		'base'   : canvas.BASE,
		'packs'  : {k: {'name': n, 'price': p, 'colors': c} for k, (n, p, c) in canvas.PACKS.items()},
		'hints'  : duel.HINTS,
		'bomb'   : duel.BOMB_COST,
		'tiers'  : words.TIERS,
		'expire' : duel.EXPIRE_AFTER,
		'start'  : START_COINS,
		'live'   : {'rounds': live.ROUND_CHOICE, 'seconds': live.TIME_CHOICE, 'sizes': list(live.SIZE_CHOICE)},
		'letters': words.LETTERS_RANGE,
		'arcade_public_url': arcade.public_url(request.host) if arcade.enabled() else None
	})
	if arcade.enabled() and user_id and not request.cookies.get(arcade.COOKIE):
		arcade.set_cookie(resp, request.host, request_token()) # players signed in before sharing existed start sharing
	return resp


@app.route('/auth/signup', methods=['POST'])
def auth_signup():
	'''Create an account with the starting coins.'''

	data     = payload()
	username = (data.get('username') or '').strip()
	password = data.get('password') or ''
	if arcade.enabled():
		try:
			res = arcade.signup(username, password, data.get('captcha_id') or '', data.get('captcha') or '', request.remote_addr)
		except arcade.ArcadeError as e:
			return api_error(str(e), e.status)
		return arcade_reply(res)
	if not captcha_verify(data.get('captcha_id'), data.get('captcha')):
		return api_error('wrong captcha')
	if not USERNAME_RE.match(username):
		return api_error('username must be 3-16 letters, numbers or _')
	if len(password) < 8 or len(password) > 128:
		return api_error('password must be 8-128 characters')
	with DB_LOCK, db_connect() as c:
		if c.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
			return api_error('username taken')
		if not signup_allowed(c, request.remote_addr):
			return api_error('only one new account per connection a day')
		user_id = c.execute('INSERT INTO users(username, password_hash, created_at, signup_ip, coins) VALUES(?, ?, ?, ?, ?)', (username, generate_password_hash(password), int(time.time()), request.remote_addr, START_COINS)).lastrowid
	return jsonify({'token': create_session(user_id), 'username': username})


@app.route('/auth/sso')
def auth_sso():
	'''Swap the shared sign-in cookie set by any site on this domain for a session here.'''

	if not arcade.enabled():
		return api_error('not found', 404)
	res = arcade.sso(request.cookies.get(arcade.COOKIE))
	if not res:
		resp, status = api_error('not signed in', 401)
		arcade.clear_cookie(resp, request.host)
		return resp, status
	return arcade_reply(res)


@app.route('/events')
def events():
	'''Server-Sent Events: the player, their games, the public rooms, and the game or room they are looking at.'''

	user_id, username = token_user(request.args.get('t') or '')
	game_id  = request.args.get('game', type=int) if user_id else None
	code     = (request.args.get('room') or '').upper()
	pid      = request.args.get('pid') or ''
	q        = queue.Queue(maxsize=8)
	with hub.LOCK:
		if sum(1 for info in hub.SUBS.values() if info['ip'] == request.remote_addr) >= IP_STREAMS:
			return api_error(f'you already have {IP_STREAMS} streams open, close one first', 429)
		hub.SUBS[q] = {'user': user_id, 'room': code or None, 'game': game_id, 'ip': request.remote_addr}

	def send(name: str, data) -> str:
		return f'event: {name}\ndata: {json.dumps({"now": time.time(), "data": data}, separators=(",", ":"))}\n\n'

	def stream():
		last  = {}
		seen  = {'gchat': 0, 'rchat': 0}
		drawn = [None, 0] # [turn, ops sent] for the room canvas
		wrote = time.time()
		tag   = 'all'
		dbat  = 0
		try:
			yield ': ' + (' ' * 2048) + '\n\nretry: 3000\n\n'
			while True:
				out   = []
				parts = {}
				if user_id and (tag == 'all' or time.time() - dbat > DB_EVERY):
					dbat           = time.time()
					games          = duel.list_games(user_id)
					parts['me']    = me_view(user_id, username, games)
					parts['games'] = games
					if game_id:
						parts['game'] = duel.view(user_id, game_id)
						msgs          = duel.chat_since(game_id, seen['gchat'])
						if msgs:
							seen['gchat'] = msgs[-1]['id']
							out.append(send('gchat', msgs))
				with hub.LOCK:
					parts['rooms'] = rooms_list()
					room           = hub.ROOMS.get(code) if code else None
					if code:
						if room and pid in room.players:
							room.seen(pid)
							parts['room'] = room.view(pid)
							msgs          = room.since(pid, seen['rchat'])
							if msgs:
								seen['rchat'] = msgs[-1]['id']
								out.append(send('rchat', msgs))
							if drawn[0] != room.turn:
								drawn[:] = [room.turn, 0]
								out.append(send('ops', {'turn': room.turn, 'from': 0, 'ops': room.ops}))
								drawn[1] = len(room.ops)
							elif len(room.ops) > drawn[1]:
								out.append(send('ops', {'turn': room.turn, 'from': drawn[1], 'ops': room.ops[drawn[1]:]}))
								drawn[1] = len(room.ops)
						else:
							parts['room'] = None
				for name, data in parts.items():
					text = json.dumps(data, separators=(',', ':'))
					if last.get(name) != text:
						last[name] = text
						out.insert(0, send(name, data))
				if out:
					wrote = time.time()
					yield ''.join(out)
				elif time.time() - wrote > PING_EVERY:
					wrote = time.time()
					yield ': ping\n\n'
				try:
					tag = q.get(timeout=2)
				except queue.Empty:
					tag = 'tick'
		finally:
			with hub.LOCK:
				hub.SUBS.pop(q, None)

	def release():
		with hub.LOCK:
			hub.SUBS.pop(q, None)

	resp = Response(stream(), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache, no-transform, no-store', 'X-Accel-Buffering': 'no'})
	resp.call_on_close(release) # the generator's finally never runs if the client leaves before the first chunk
	return resp


@app.route('/favicon.ico')
def favicon():
	'''Serve the PNG icon for clients that only look for /favicon.ico.'''

	return send_from_directory(app.static_folder, 'icon-192.png', mimetype='image/png')


@app.route('/games')
@login_required
def games_list(user_id: int, _: str):
	'''List the player's games.'''

	return jsonify(duel.list_games(user_id))


@app.route('/games/<int:game_id>')
@login_required
def game_view(user_id: int, _: str, game_id: int):
	'''Return one game.'''

	g = duel.view(user_id, game_id)
	return jsonify(g) if g else api_error('no such game', 404)


@app.route('/games/<int:game_id>/bomb', methods=['POST'])
@login_required
def game_bomb(user_id: int, _: str, game_id: int):
	'''Swap the word choices for new ones.'''

	return game_reply(game_id, user_id, duel.bomb(user_id, game_id))


@app.route('/games/<int:game_id>/chat', methods=['POST'])
@login_required
def game_chat(user_id: int, _: str, game_id: int):
	'''Post in a game's chat.'''

	error = duel.chat_post(user_id, game_id, payload().get('text'))
	if error:
		return api_error(error)
	hub.notify(users=duel.players(game_id))
	return jsonify({'ok': True})


@app.route('/games/<int:game_id>/draw', methods=['POST'])
@login_required
def game_draw(user_id: int, _: str, game_id: int):
	'''Send a drawing.'''

	data = payload()
	return game_reply(game_id, user_id, duel.draw(user_id, game_id, data.get('choice'), data.get('ops')))


@app.route('/games/<int:game_id>/guess', methods=['POST'])
@login_required
def game_guess(user_id: int, _: str, game_id: int):
	'''Guess the word of a drawing.'''

	result = duel.guess(user_id, game_id, payload().get('guess'))
	return game_reply(game_id, user_id, result.pop('error', None), result)


@app.route('/games/<int:game_id>/hint', methods=['POST'])
@login_required
def game_hint(user_id: int, _: str, game_id: int):
	'''Buy a hint.'''

	result = duel.hint(user_id, game_id, payload().get('kind'))
	return game_reply(game_id, user_id, result.pop('error', None), result)


@app.route('/games/<int:game_id>/pass', methods=['POST'])
@login_required
def game_pass(user_id: int, _: str, game_id: int):
	'''Give up on a drawing.'''

	result = duel.pass_round(user_id, game_id)
	return game_reply(game_id, user_id, result.pop('error', None), result)


@app.route('/games/<int:game_id>/resign', methods=['POST'])
@login_required
def game_resign(user_id: int, _: str, game_id: int):
	'''Leave a game for good.'''

	return game_reply(game_id, user_id, duel.resign(user_id, game_id))


@app.route('/games/<int:game_id>/round/<int:round_id>')
@login_required
def game_round(user_id: int, _: str, game_id: int, round_id: int):
	'''Return a drawing for replay.'''

	r = duel.round_ops(user_id, game_id, round_id)
	return jsonify(r) if r else api_error('no such drawing', 404)


@app.route('/games/join', methods=['POST'])
@login_required
def game_join(user_id: int, _: str):
	'''Take the open seat from an invite link.'''

	game_id, error = duel.join(user_id, payload().get('code'))
	if error:
		return api_error(error)
	hub.notify(users=duel.players(game_id))
	return jsonify({'id': game_id})


@app.route('/games/new', methods=['POST'])
@login_required
def game_new(user_id: int, _: str):
	'''Start a game.'''

	data           = payload()
	opponent       = (data.get('opponent') or '').strip()
	if arcade.enabled() and data.get('mode') == 'user' and USERNAME_RE.match(opponent):
		arcade_account(opponent) # so an account that never visited here can be challenged
	game_id, error = duel.create(user_id, data.get('mode'), data.get('opponent'))
	if error:
		return api_error(error)
	hub.notify(users=duel.players(game_id))
	return jsonify({'id': game_id})


@app.route('/leaderboard')
def leaderboard_view():
	'''Return a leaderboard.'''

	board = request.args.get('board') or 'coins'
	return jsonify(leaderboard(board)) if board in BOARDS else api_error('unknown board')


@app.route('/live/<code>/choose', methods=['POST'])
@room_action
def live_choose(room: live.Room, pid: str, data: dict) -> str:
	'''Pick a word to draw.'''

	return room.choose(pid, data.get('choice'))


@app.route('/live/<code>/draw', methods=['POST'])
@room_action
def live_draw(room: live.Room, pid: str, data: dict) -> str:
	'''Add strokes to the drawing.'''

	return room.draw(pid, data.get('ops'))


@app.route('/live/<code>/kick', methods=['POST'])
@room_action
def live_kick(room: live.Room, pid: str, data: dict) -> str:
	'''Kick a player.'''

	return room.kick(pid, data.get('player'))


@app.route('/live/<code>/leave', methods=['POST'])
@room_action
def live_leave(room: live.Room, pid: str, _: dict) -> str:
	'''Leave the room.'''

	room.remove(pid)


@app.route('/live/<code>/say', methods=['POST'])
@room_action
def live_say(room: live.Room, pid: str, data: dict) -> str:
	'''Chat or guess.'''

	return room.say(pid, data.get('text'))


@app.route('/live/<code>/settings', methods=['POST'])
@room_action
def live_settings(room: live.Room, pid: str, data: dict) -> str:
	'''Change the room settings.'''

	return room.settings(pid, data)


@app.route('/live/<code>/start', methods=['POST'])
@room_action
def live_start(room: live.Room, pid: str, _: dict) -> str:
	'''Start the game.'''

	return room.start(pid)


@app.route('/live/create', methods=['POST'])
def live_create():
	'''Open a live room and seat its creator as host.'''

	data              = payload()
	user_id, username = request_user()
	name, error       = guest_name(user_id, username, data.get('name'))
	if error:
		return api_error(error)
	with hub.LOCK:
		if len(hub.ROOMS) >= live.ROOMS_MAX:
			return api_error('too many rooms are open, join one instead')
		if sum(1 for r in hub.ROOMS.values() if r.ip == request.remote_addr) >= live.IP_ROOMS_MAX:
			return api_error(f'you already have {live.IP_ROOMS_MAX} open rooms, finish or leave one first', 429)
		code = hub.new_code()
		room = live.Room(code, f'{name}\'s room', data.get('public') is not False, data.get('rounds') if data.get('rounds') in live.ROUND_CHOICE else 3, data.get('seconds') if data.get('seconds') in live.TIME_CHOICE else 80, data.get('size') if data.get('size') in live.SIZE_CHOICE else 8, request.remote_addr)
		pid, error = room.join(user_id, name)
		hub.ROOMS[code] = room
	hub.notify()
	return jsonify({'code': code, 'pid': pid})


@app.route('/live/join', methods=['POST'])
def live_join():
	'''Take a seat in a live room.'''

	data              = payload()
	user_id, username = request_user()
	name, error       = guest_name(user_id, username, data.get('name'))
	if error:
		return api_error(error)
	with hub.LOCK:
		now   = time.time()
		fails = [t for t in JOIN_FAILS.get(request.remote_addr, []) if t > now - 60]
		if len(fails) >= JOIN_FAIL_MAX:
			return api_error('too many wrong room codes, wait a minute', 429)
		room = hub.ROOMS.get((data.get('code') or '').strip().upper())
		if not room:
			for ip in [ip for ip, times in JOIN_FAILS.items() if times[-1] <= now - 60]:
				del JOIN_FAILS[ip]
			JOIN_FAILS[request.remote_addr] = fails + [now]
			return api_error('no room with that code', 404)
		pid, error = room.join(user_id, name)
		if error:
			return api_error(error)
		hub.notify(room=room.code)
	return jsonify({'code': room.code, 'pid': pid})


@app.route('/robots.txt')
def robots():
	'''Let crawlers in and point them at the sitemap.'''

	return Response(f'User-agent: *\nAllow: /\nSitemap: {public_root()}sitemap.xml\n', mimetype='text/plain')


@app.route('/shop/buy', methods=['POST'])
@login_required
def shop_buy(user_id: int, username: str):
	'''Buy a color pack.'''

	key = payload().get('pack')
	if key not in canvas.PACKS:
		return api_error('no such pack')
	with DB_LOCK, db_connect() as c:
		owned = packs_of(c, user_id)
		if key in owned:
			return api_error('you already own that pack')
		if not spend(c, user_id, canvas.PACKS[key][1]):
			return api_error(f'that pack costs {canvas.PACKS[key][1]} coins')
		c.execute('UPDATE users SET packs=? WHERE id=?', (','.join(owned + [key]), user_id))
	with hub.LOCK:
		for room in hub.ROOMS.values():
			for p in room.players.values():
				if p['user_id'] == user_id:
					p['packs'] = owned + [key]
	hub.notify(users={user_id})
	return jsonify(me_view(user_id, username))


@app.route('/sw.js')
def service_worker():
	'''Serve the service worker from the root so it covers the whole site. Android only shows notifications through one.'''

	return send_from_directory(app.static_folder, 'sw.js', mimetype='text/javascript', max_age=0)


@app.route('/sitemap.xml')
def sitemap():
	'''List the one indexable page. Games and rooms live behind the # in the URL.'''

	return Response(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{public_root()}</loc></url></urlset>\n', mimetype='application/xml')


@app.route('/words/mine')
@login_required
def words_mine(user_id: int, _: str):
	'''List the words the player submitted.'''

	return jsonify(words.mine(user_id))


@app.route('/words/submit', methods=['POST'])
@login_required
def words_submit(user_id: int, _: str):
	'''Submit a new word.'''

	data  = payload()
	error = words.submit(user_id, data.get('text'), data.get('tier'))
	return api_error(error) if error else jsonify(words.mine(user_id))


@app.route('/words/vote', methods=['POST'])
@login_required
def words_vote(user_id: int, _: str):
	'''Thumbs up or down a community word.'''

	data   = payload()
	result = words.vote(user_id, data.get('word'), data.get('vote'))
	return api_error(result['error']) if 'error' in result else jsonify(result)


setup()



if __name__ == '__main__':
	app.run(host='127.0.0.1', port=20010, threaded=True)
