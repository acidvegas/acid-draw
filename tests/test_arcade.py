#!/usr/bin/env python3
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_arcade.py

import os
import sys
import tempfile
import time
import unittest
import unittest.mock

from werkzeug.middleware.proxy_fix import ProxyFix

if 'DRAW_DATA_DIR' not in os.environ:
	os.environ['DRAW_DATA_DIR'] = tempfile.mkdtemp()
os.environ.setdefault('DRAW_ADMINS', 'admin')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aciddraw'))

import app as draw_app
import arcade
import auth
import storage
import words


STROKE   = [{'k': 'p', 'c': '#111111', 's': 8, 't': 0, 'p': [v for i in range(40) for v in (10 + i, 10 + i, i)]}] # 40 points, enough ink to pay
SESSIONS = {
	'tok-zed'  : {'user_id': 9001, 'username': 'zed',   'admin': False},
	'tok-yan'  : {'user_id': 9002, 'username': 'yan',   'admin': False},
	'tok-boss' : {'user_id': 9003, 'username': 'boss',  'admin': True},
	'tok-admin': {'user_id': 9004, 'username': 'admin', 'admin': False},
	'tok-ava'  : {'user_id': 9005, 'username': 'ava',   'admin': False},
	'tok-ben'  : {'user_id': 9006, 'username': 'ben',   'admin': False}
}
ACCOUNTS = {s['username']: s['user_id'] for s in SESSIONS.values()} | {'quinn': 9020} # quinn has never visited acid-draw


draw_app.duel.GUESS_EVERY = 0 # tests guess back to back


class StopPoll(Exception):
	'''Raised by the patched sleep to end the poll loop after one pass.'''


class SyncThread:
	'''Stand-in for threading.Thread that runs the target right away.'''

	def __init__(self, target, args=(), daemon=None):
		self.target = target
		self.args   = args

	def start(self):
		self.target(*self.args)


class TestArcade(unittest.TestCase):
	'''Arcade mode with the acid-arcade-db client mocked out.'''

	def setUp(self):
		self.client  = draw_app.app.test_client()
		self.patches = [
			unittest.mock.patch.object(arcade, 'enabled', return_value=True),
			unittest.mock.patch.object(arcade, 'ARCADE_PUBLIC', 'https://arcade.{domain}'),
			unittest.mock.patch.object(arcade, 'session', side_effect=lambda t: SESSIONS.get(t)),
			unittest.mock.patch.object(arcade, 'user', side_effect=lambda n: next(({'user_id': i, 'username': k} for k, i in ACCOUNTS.items() if k.lower() == n.lower()), None))
		]
		for p in self.patches:
			p.start()

	def tearDown(self):
		for p in self.patches:
			p.stop()

	def call(self, token: str, path: str, body: dict = None):
		'''Call the API with a session token and return (status, json).'''

		headers = {'X-Auth-Token': token} if token else {}
		res     = self.client.post(path, json=body, headers=headers) if body is not None else self.client.get(path, headers=headers)
		return res.status_code, res.get_json()

	def poll_once(self, reply: dict):
		'''Run one pass of the changes poller with a canned reply.'''

		clock = unittest.mock.Mock(wraps=time)
		clock.sleep.side_effect = StopPoll
		with unittest.mock.patch.object(arcade, 'changes', return_value=reply), unittest.mock.patch.object(auth, 'time', clock):
			with self.assertRaises(StopPoll):
				auth.arcade_poll()

	def test_admins(self):
		'''Arcade admins and DRAW_ADMINS names both get the admin page.'''

		self.assertEqual(self.call('tok-zed', '/admin/words')[0], 403)
		self.assertEqual(self.call('tok-boss', '/admin/words')[0], 200)
		self.assertEqual(self.call('tok-admin', '/admin/words')[0], 200)
		_, me = self.call('tok-boss', '/auth/me')
		self.assertTrue(me['user']['admin'])

	def test_auth_proxy(self):
		'''Captcha, signup, login and logout go to acid-arcade-db and keep the reply shapes.'''

		with unittest.mock.patch.object(arcade, 'captcha', return_value={'captcha_id': 'c1', 'svg': '<svg/>'}):
			self.assertEqual(self.call(None, '/auth/captcha')[1]['captcha_id'], 'c1')
		with unittest.mock.patch.object(arcade, 'signup', return_value={'token': 'tok-new', 'user_id': 9010, 'username': 'newbie'}) as signup:
			st, res = self.call(None, '/auth/signup', {'username': 'newbie', 'password': 'password123', 'captcha_id': 'c1', 'captcha': 'ABCDE'})
		self.assertEqual((st, res), (200, {'token': 'tok-new', 'username': 'newbie'}))
		signup.assert_called_once_with('newbie', 'password123', 'c1', 'ABCDE', '127.0.0.1')
		with storage.db_connect() as c:
			row = c.execute('SELECT username, password_hash, coins FROM users WHERE id=9010').fetchone()
		self.assertEqual(tuple(row), ('newbie', '', storage.START_COINS))
		with unittest.mock.patch.object(arcade, 'login', side_effect=arcade.ArcadeError('wrong username or password', 401)):
			st, res = self.call(None, '/auth/login', {'username': 'zed', 'password': 'nope'})
		self.assertEqual((st, res['error']), (401, 'wrong username or password'))
		with unittest.mock.patch.object(arcade, 'logout') as logout:
			self.call('tok-zed', '/auth/logout', {})
		logout.assert_called_once_with('tok-zed')

	def test_client_ip_and_domain(self):
		'''Behind a trusted proxy (DRAW_PROXIES=1) logins and signups pass the player's address, a 429 comes back as is, and the arcade URL follows the request host.'''

		fwd = {'X-Forwarded-For': '203.0.113.7'}
		with unittest.mock.patch.object(draw_app.app, 'wsgi_app', ProxyFix(draw_app.app.wsgi_app, x_for=1)):
			with unittest.mock.patch.object(arcade, 'login', return_value={'token': 'tok-zed', 'user_id': 9001, 'username': 'zed'}) as login:
				self.client.post('/auth/login', json={'username': 'zed', 'password': 'password123'}, headers=fwd)
			login.assert_called_once_with('zed', 'password123', '203.0.113.7')
			with unittest.mock.patch.object(arcade, 'signup', return_value={'token': 'tok-zed', 'user_id': 9001, 'username': 'zed'}) as signup:
				self.client.post('/auth/signup', json={'username': 'zed', 'password': 'password123', 'captcha_id': 'c1', 'captcha': 'ABCDE'}, headers=fwd)
			signup.assert_called_once_with('zed', 'password123', 'c1', 'ABCDE', '203.0.113.7')
			with unittest.mock.patch.object(arcade, 'login', side_effect=arcade.ArcadeError('too many failed logins, try again later', 429)):
				res = self.client.post('/auth/login', json={'username': 'zed', 'password': 'nope'}, headers=fwd)
			self.assertEqual((res.status_code, res.get_json()), (429, {'error': 'too many failed logins, try again later'}))
		for host, url in (('draw.supernets.org', 'https://arcade.supernets.org'), ('draw.acid.vegas', 'https://arcade.acid.vegas')):
			self.assertEqual(self.client.get('/auth/me', base_url=f'https://{host}').get_json()['arcade_public_url'], url)

	def test_account_lookup(self):
		'''An account that never visited can be challenged by name and its name is reserved from guests, with a mirror row made on the way.'''

		with storage.db_connect() as c:
			self.assertIsNone(c.execute('SELECT 1 FROM users WHERE id=9020').fetchone())
		st, res = self.call(None, '/live/create', {'name': 'Quinn'})
		self.assertEqual((st, res['error']), (400, 'that name belongs to an account, sign in or pick another'))
		with storage.db_connect() as c:
			self.assertEqual(tuple(c.execute('SELECT username, coins FROM users WHERE id=9020').fetchone()), ('quinn', storage.START_COINS))
			c.execute('DELETE FROM users WHERE id=9020')
		st, g = self.call('tok-zed', '/games/new', {'mode': 'user', 'opponent': 'quinn'})
		self.assertEqual(st, 200)
		_, view = self.call('tok-zed', f'/games/{g["id"]}')
		self.assertEqual((view['opponent'], view['opponent_id']), ('quinn', 9020))
		st, res = self.call('tok-zed', '/games/new', {'mode': 'user', 'opponent': 'nobody_here'})
		self.assertEqual((st, res['error']), (400, 'no player by that name'))

	def test_presence(self):
		'''Presence reports the signed in players with an event stream open, and an empty list when there are none.'''

		clock = unittest.mock.Mock(wraps=time)
		clock.sleep.side_effect = StopPoll
		subs  = {'q1': {'user': 9006, 'room': None, 'game': None}, 'q2': {'user': None, 'room': 'ABCD', 'game': None}, 'q3': {'user': 9005, 'room': None, 'game': 3}, 'q4': {'user': 9005, 'room': None, 'game': None}}
		for current, expect in ((subs, [9005, 9006]), ({}, [])):
			with unittest.mock.patch.dict(draw_app.hub.SUBS, current, clear=True), unittest.mock.patch.object(auth, 'time', clock), unittest.mock.patch.object(arcade, 'presence') as presence:
				with self.assertRaises(StopPoll):
					auth.arcade_presence()
			presence.assert_called_once_with(expect)

	def test_sso(self):
		'''Sign ins set the shared cookie, /auth/sso swaps it for a session, logout clears it, and /auth/me starts sharing an older sign in.'''

		def cookie(res) -> str:
			return next((h for h in res.headers.getlist('Set-Cookie') if h.startswith(arcade.COOKIE + '=')), None)

		with unittest.mock.patch.object(arcade, 'login', return_value={'token': 'tok-zed', 'user_id': 9001, 'username': 'zed'}):
			res = draw_app.app.test_client().post('/auth/login', json={'username': 'zed', 'password': 'password123'})
		self.assertEqual(res.get_json(), {'token': 'tok-zed', 'username': 'zed'})
		self.assertTrue(cookie(res).startswith(f'{arcade.COOKIE}=tok-zed;'))
		self.assertIn('HttpOnly', cookie(res))
		res = draw_app.app.test_client().get('/auth/me', headers={'X-Auth-Token': 'tok-zed'})
		self.assertTrue(cookie(res).startswith(f'{arcade.COOKIE}=tok-zed;'))
		client = draw_app.app.test_client()
		client.set_cookie(arcade.COOKIE, 'shared')
		self.assertIsNone(cookie(client.get('/auth/me', headers={'X-Auth-Token': 'tok-zed'})))
		self.assertIsNone(cookie(draw_app.app.test_client().get('/auth/me')))
		with unittest.mock.patch.object(arcade, 'sso', return_value={'token': 'tok-yan', 'user_id': 9002, 'username': 'yan'}) as sso:
			res = client.get('/auth/sso')
		sso.assert_called_once_with('shared')
		self.assertEqual((res.status_code, res.get_json()), (200, {'token': 'tok-yan', 'username': 'yan'}))
		res = draw_app.app.test_client().get('/auth/sso')
		self.assertEqual(res.status_code, 401)
		self.assertIn('Max-Age=0', cookie(res))
		with unittest.mock.patch.object(arcade, 'logout'):
			res = client.post('/auth/logout', json={}, headers={'X-Auth-Token': 'tok-yan'})
		self.assertIn('Max-Age=0', cookie(res))

	def test_down(self):
		'''An unreachable account server is a 503, not a guest.'''

		with unittest.mock.patch.object(arcade, 'session', side_effect=arcade.ArcadeDown('refused')):
			st, res = self.call('tok-zed', '/auth/me')
		self.assertEqual((st, res), (503, {'error': 'account server is down'}))

	def test_game_stats_rename_delete(self):
		'''A guess pushes stats for both players, a rename shows everywhere, and a deletion wipes the account's rows.'''

		self.call('tok-yan', '/auth/me')
		_, g    = self.call('tok-zed', '/games/new', {'mode': 'user', 'opponent': 'yan'})
		gid     = g['id']
		_, view = self.call('tok-zed', f'/games/{gid}')
		self.assertEqual(view['opponent_id'], 9002)
		self.call('tok-zed', f'/games/{gid}/draw', {'choice': 0, 'ops': STROKE})
		self.call('tok-yan', f'/games/{gid}/chat', {'text': 'hi'})
		with unittest.mock.patch.object(storage.threading, 'Thread', SyncThread), unittest.mock.patch.object(arcade, 'push_stats') as push:
			_, res = self.call('tok-yan', f'/games/{gid}/guess', {'guess': view['choices'][0]['word']})
		self.assertTrue(res['correct'])
		pushed = {call.args[0]: call.args for call in push.call_args_list}
		self.assertEqual(set(pushed), {9001, 9002})
		self.assertEqual(pushed[9002][1], res['coins'])
		self.assertEqual(pushed[9002][2], {'Coins earned': res['coins'], 'Coins': storage.START_COINS + res['coins'], 'Best streak': 1, 'Drawings': 0, 'Games': 1})
		self.assertEqual(pushed[9001][2]['Drawings'], 1)
		_, me = self.call('tok-zed', '/auth/me')
		self.assertEqual((me['user']['id'], me['arcade_public_url']), (9001, 'https://arcade.localhost'))
		self.poll_once({'now': 1, 'changed': [{'user_id': 9002, 'username': 'yanny'}, {'user_id': 9999, 'username': 'stranger'}], 'deleted': []})
		with unittest.mock.patch.dict(SESSIONS, {'tok-yan': {'user_id': 9002, 'username': 'yanny', 'admin': False}}):
			_, view = self.call('tok-zed', f'/games/{gid}')
			self.assertEqual(view['opponent'], 'yanny')
			board = self.call(None, '/leaderboard?board=coins')[1]
			self.assertIn({'user_id': 9002, 'name': 'yanny', 'value': res['coins'], 'extra': None}, board)
			chat = draw_app.duel.chat_since(gid, 0)
			self.assertEqual((chat[0]['name'], chat[0]['user_id']), ('yanny', 9002))
			with storage.db_connect() as c:
				self.assertIsNone(c.execute('SELECT 1 FROM users WHERE id=9999').fetchone())
			self.poll_once({'now': 2, 'changed': [], 'deleted': [9002]})
		with storage.db_connect() as c:
			self.assertIsNone(c.execute('SELECT 1 FROM users WHERE id=9002').fetchone())
			self.assertIsNone(c.execute('SELECT 1 FROM games WHERE id=?', (gid,)).fetchone())
			self.assertIsNone(c.execute('SELECT 1 FROM rounds WHERE game_id=?', (gid,)).fetchone())
			self.assertIsNone(c.execute('SELECT 1 FROM game_chat WHERE game_id=?', (gid,)).fetchone())
			self.assertIsNone(c.execute('SELECT 1 FROM plays WHERE user_id=9002').fetchone())

	def test_feed_live_win(self):
		'''Winning a live game with 3 or more players goes on the feed once, guests and smaller games never.'''

		room = draw_app.live.Room('FEED', 'feed room', False, 1, 80, 8)
		for uid, name, score in ((9005, 'ava', 900), (9006, 'ben', 400), (None, 'guestie', 100)):
			pid, _ = room.join(uid, name)
			room.players[pid]['score'] = score
		with unittest.mock.patch.object(storage.threading, 'Thread', SyncThread), unittest.mock.patch.object(arcade, 'push_stats'), unittest.mock.patch.object(arcade, 'event') as event:
			room.finish()
			event.assert_called_once_with(9005, 'win', 'won a live room with 900 points')
			event.reset_mock()
			room.remove(next(k for k, p in room.players.items() if p['name'] == 'ben'))
			room.finish()
			event.assert_not_called()
			room.join(9006, 'ben')
			for p in room.players.values():
				p['score'] = 700 if p['user_id'] is None else 0
			room.finish()
			event.assert_not_called()

	def test_feed_streak(self):
		'''A 1v1 streak goes on the feed for both players when it reaches 5, and not before.'''

		self.call('tok-ben', '/auth/me')
		_, g    = self.call('tok-ava', '/games/new', {'mode': 'user', 'opponent': 'ben'})
		players = ['tok-ava', 'tok-ben']
		with unittest.mock.patch.object(storage.threading, 'Thread', SyncThread), unittest.mock.patch.object(arcade, 'push_stats'), unittest.mock.patch.object(arcade, 'event') as event:
			for n in range(1, 6):
				drawer, guesser = players[(n - 1) % 2], players[n % 2]
				_, view = self.call(drawer, f'/games/{g["id"]}')
				self.call(drawer, f'/games/{g["id"]}/draw', {'choice': 0, 'ops': STROKE})
				_, res  = self.call(guesser, f'/games/{g["id"]}/guess', {'guess': view['choices'][0]['word']})
				self.assertEqual(res['streak'], n)
				if n < 5:
					event.assert_not_called()
		self.assertEqual(sorted(c.args for c in event.call_args_list), [(9005, 'streak', 'is on a 5 guess streak'), (9006, 'streak', 'is on a 5 guess streak')])

	def test_live_room(self):
		'''Live room seats carry the user id for pictures, and a correct guess pushes stats.'''

		_, host = self.call('tok-zed', '/live/create', {})
		code    = host['code']
		_, join = self.call(None, '/live/join', {'code': code, 'name': 'guestie'})
		room    = draw_app.hub.ROOMS[code]
		players = {p['name']: p for p in room.view(host['pid'])['players']}
		self.assertEqual((players['zed']['user_id'], players['guestie']['user_id']), (9001, None))
		room.drawer = join['pid']
		room.choices = words.choose(storage.db_connect())
		room.state   = 'choosing'
		room.begin_draw(0)
		with unittest.mock.patch.object(storage.threading, 'Thread', SyncThread), unittest.mock.patch.object(arcade, 'push_stats') as push:
			self.call(None, f'/live/{code}/say', {'pid': host['pid'], 'text': room.word['word']})
		self.assertIn(9001, [call.args[0] for call in push.call_args_list])



if __name__ == '__main__':
	unittest.main()
