#!/usr/bin/env python3
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_app.py

import json
import os
import re
import sys
import tempfile
import time
import unittest
import unittest.mock

os.environ['DRAW_DATA_DIR'] = tempfile.mkdtemp()
os.environ['DRAW_ADMINS']   = 'admin'
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aciddraw'))

import app as draw_app
import arcade
import auth
import hub
import live
import storage
import words


STROKE = [{'k': 'p', 'c': '#111111', 's': 8, 't': 0, 'p': [v for i in range(40) for v in (10 + i, 10 + i, i)]}] # 40 points, enough ink to pay


draw_app.duel.GUESS_EVERY = 0 # tests guess back to back; test_guess_rate turns it on


class TestApp(unittest.TestCase):
	'''HTTP tests for accounts, async games, words and live rooms.'''

	@classmethod
	def setUpClass(cls):
		cls.client = draw_app.app.test_client()
		cls.tokens = {}
		for i, name in enumerate(('alice', 'bob', 'carol', 'admin')):
			cap = cls.client.get('/auth/captcha').get_json()
			ans = auth.CAPTCHAS[cap['captcha_id']][0]
			res = cls.client.post('/auth/signup', json={'username': name, 'password': 'password123', 'captcha_id': cap['captcha_id'], 'captcha': ans}, environ_base={'REMOTE_ADDR': f'10.0.0.{i}'})
			cls.tokens[name] = res.get_json()['token']

	def call(self, who: str, path: str, body: dict = None):
		'''Call the API as a player and return (status, json).'''

		headers = {'X-Auth-Token': self.tokens[who]} if who else {}
		res     = self.client.post(path, json=body, headers=headers) if body is not None else self.client.get(path, headers=headers)
		return res.status_code, res.get_json()

	def test_async_game(self):
		'''Alice challenges bob, draws, bob guesses with the right word and both get coins.'''

		_, g    = self.call('alice', '/games/new', {'mode': 'user', 'opponent': 'bob'})
		gid     = g['id']
		_, view = self.call('alice', f'/games/{gid}')
		self.assertEqual(view['step'], 'draw')
		self.assertEqual(len(view['choices']), 3)
		self.assertNotIn('ops', view)
		st, bad = self.call('alice', f'/games/{gid}/draw', {'choice': 0, 'ops': [{'k': 'p', 'c': '#39ff14', 's': 8, 't': 0, 'p': [1, 1, 0]}]})
		self.assertEqual(st, 400)
		self.assertIn('color', bad['error'])
		_, res  = self.call('alice', f'/games/{gid}/draw', {'choice': 2, 'ops': STROKE})
		self.assertEqual(res['game']['step'], 'wait')
		_, bv   = self.call('bob', f'/games/{gid}')
		self.assertEqual(bv['step'], 'guess')
		self.assertNotIn('word', bv['round'])
		word    = view['choices'][2]['word']
		tiles   = list(bv['round']['tiles'])
		for ch in words.letters(word):
			tiles.remove(ch)
		_, ops  = self.call('bob', f'/games/{gid}/round/{bv["round"]["id"]}')
		self.assertIsNone(ops['word'])
		self.assertEqual(ops['ops'], STROKE)
		_, wrong = self.call('bob', f'/games/{gid}/guess', {'guess': 'zzzzzz'})
		self.assertFalse(wrong['correct'])
		self.assertEqual(wrong['game']['round']['misses'], ['zzzzzz'])
		_, av    = self.call('alice', f'/games/{gid}')
		self.assertEqual(av['history'][0]['misses'], ['zzzzzz'])
		_, right = self.call('bob', f'/games/{gid}/guess', {'guess': word.upper()})
		self.assertTrue(right['correct'])
		self.assertEqual(right['coins'], 3)
		self.assertEqual(right['game']['step'], 'draw')
		self.assertEqual(right['game']['streak'], 1)
		_, me = self.call('bob', '/auth/me')
		self.assertEqual(me['user']['coins'], 53)

	def test_captcha(self):
		'''Captchas are drawn as paths with no text, the store is capped, and each address gets 30 a minute.'''

		svg = auth.captcha_render_svg('ABCDEFGHJKLMNPQRSTUVWXYZ23456789')
		self.assertNotIn('<text', svg)
		self.assertIn('<path', svg)
		with unittest.mock.patch.object(auth, 'CAPTCHA_MAX', 3):
			ids = [auth.captcha_create()['captcha_id'] for _ in range(5)]
			self.assertLessEqual(len(auth.CAPTCHAS), 3)
			self.assertIn(ids[-1], auth.CAPTCHAS)
			self.assertNotIn(ids[0], auth.CAPTCHAS)
		ip = {'REMOTE_ADDR': '10.9.0.1'}
		for _ in range(30):
			self.assertEqual(self.client.get('/auth/captcha', environ_base=ip).status_code, 200)
		self.assertEqual(self.client.get('/auth/captcha', environ_base=ip).status_code, 429)
		self.assertEqual(self.client.get('/auth/captcha', environ_base={'REMOTE_ADDR': '10.9.0.2'}).status_code, 200)

	def test_hints_and_pass(self):
		'''Hints cost coins, decoys can be removed once, and passing resets the streak.'''

		_, g   = self.call('carol', '/games/new', {'mode': 'user', 'opponent': 'alice'})
		gid    = g['id']
		self.call('carol', f'/games/{gid}')
		self.call('carol', f'/games/{gid}/draw', {'choice': 0, 'ops': STROKE})
		_, av  = self.call('alice', f'/games/{gid}')
		n      = sum(av['round']['pattern'])
		_, res = self.call('alice', f'/games/{gid}/hint', {'kind': 'decoys'})
		self.assertEqual(len(res['game']['round']['tiles']), n)
		st, _  = self.call('alice', f'/games/{gid}/hint', {'kind': 'decoys'})
		self.assertEqual(st, 400)
		_, res = self.call('alice', f'/games/{gid}/hint', {'kind': 'reveal'})
		self.assertEqual(len(res['game']['round']['revealed']), 1)
		self.assertEqual(len(res['game']['round']['tiles']), n - 1)
		_, res = self.call('alice', f'/games/{gid}/pass', {})
		self.assertTrue(res['passed'])
		self.assertEqual(res['game']['streak'], 0)
		self.assertEqual(res['game']['step'], 'draw')

	def test_invite_and_random(self):
		'''Invite links seat whoever opens them, random games pair players after the first drawing.'''

		_, g  = self.call('admin', '/games/new', {'mode': 'invite'})
		_, v  = self.call('admin', f'/games/{g["id"]}')
		self.assertTrue(v['invite'])
		_, j  = self.call('carol', '/games/join', {'code': v['invite']})
		self.assertEqual(j['id'], g['id'])
		_, r1 = self.call('bob', '/games/new', {'mode': 'random'})
		self.call('bob', f'/games/{r1["id"]}')
		self.call('bob', f'/games/{r1["id"]}/draw', {'choice': 1, 'ops': STROKE})
		_, r2 = self.call('carol', '/games/new', {'mode': 'random'})
		self.assertEqual(r1['id'], r2['id'])
		_, cv = self.call('carol', f'/games/{r2["id"]}')
		self.assertEqual(cv['step'], 'guess')

	def test_join_guessing(self):
		'''An IP that tries too many wrong room codes is told to wait, other IPs are not.'''

		ip = {'REMOTE_ADDR': '10.8.0.1'}
		for _ in range(draw_app.JOIN_FAIL_MAX):
			self.assertEqual(self.client.post('/live/join', json={'code': 'ZZZZ', 'name': 'guesser'}, environ_base=ip).status_code, 404)
		self.assertEqual(self.client.post('/live/join', json={'code': 'ZZZZ', 'name': 'guesser'}, environ_base=ip).status_code, 429)
		self.assertEqual(self.client.post('/live/join', json={'code': 'ZZZZ', 'name': 'guesser'}, environ_base={'REMOTE_ADDR': '10.8.0.2'}).status_code, 404)

	def test_ip_rooms(self):
		'''One address can have 5 live rooms open, a 6th is refused while another address still gets one.'''

		def create(ip: str):
			res = self.client.post('/live/create', json={'name': 'roomer'}, environ_base={'REMOTE_ADDR': ip})
			return res.status_code, res.get_json()

		codes = [create('10.1.0.1')[1]['code'] for _ in range(live.IP_ROOMS_MAX)]
		st, res = create('10.1.0.1')
		self.assertEqual(st, 429)
		self.assertIn('5 open rooms', res['error'])
		st, res = create('10.1.0.2')
		self.assertEqual(st, 200)
		codes.append(res['code'])
		with hub.LOCK:
			for code in codes:
				hub.ROOMS.pop(code, None)
		self.assertEqual(create('10.1.0.1')[0], 200) # a reaped room frees its slot
		with hub.LOCK:
			for code in [c for c, r in hub.ROOMS.items() if r.ip == '10.1.0.1']:
				del hub.ROOMS[code]

	def test_ip_streams(self):
		'''One address can have 20 event streams open, a 21st is refused, and closing one frees its slot.'''

		def stream(ip: str):
			return self.client.get('/events', buffered=False, environ_base={'REMOTE_ADDR': ip})

		open_ = [stream('10.2.0.1') for _ in range(draw_app.IP_STREAMS)]
		self.assertTrue(all(r.status_code == 200 for r in open_))
		self.assertEqual(stream('10.2.0.1').status_code, 429)
		other = stream('10.2.0.2')
		self.assertEqual(other.status_code, 200)
		open_.pop().close()
		open_.append(stream('10.2.0.1'))
		self.assertEqual(open_[-1].status_code, 200)
		for r in open_ + [other]:
			r.close()
		self.assertFalse(any(info['ip'] in ('10.2.0.1', '10.2.0.2') for info in hub.SUBS.values()))

	def test_login_limits(self):
		'''After 10 failed logins an account is refused even with the right password, other accounts are not.'''

		self.addCleanup(auth.ATTEMPTS.clear)
		ip = {'REMOTE_ADDR': '10.9.1.1'}
		for _ in range(10):
			self.assertEqual(self.client.post('/auth/login', json={'username': 'carol', 'password': 'wrongpass1'}, environ_base=ip).status_code, 401)
		res = self.client.post('/auth/login', json={'username': 'carol', 'password': 'password123'}, environ_base=ip)
		self.assertEqual(res.status_code, 429)
		self.assertIn('error', res.get_json())
		self.assertEqual(self.client.post('/auth/login', json={'username': 'CAROL', 'password': 'password123'}, environ_base={'REMOTE_ADDR': '10.9.1.2'}).status_code, 429)
		self.assertEqual(self.client.post('/auth/login', json={'username': 'alice', 'password': 'password123'}, environ_base=ip).status_code, 200)

	def test_live_room(self):
		'''A guest and a player share a room: the word stays hidden from the guesser until they type it.'''

		st, bad = self.call(None, '/live/create', {'name': 'bob'})
		self.assertEqual(st, 400)
		_, host = self.call(None, '/live/create', {'name': 'guesty'})
		code    = host['code']
		_, join = self.call('alice', '/live/join', {'code': code})
		room    = hub.ROOMS[code]
		_, ok   = self.call(None, f'/live/{code}/start', {'pid': host['pid']})
		self.assertTrue(ok['ok'])
		drawer  = room.drawer
		guesser = join['pid'] if drawer == host['pid'] else host['pid']
		self.call(None, f'/live/{code}/choose', {'pid': drawer, 'choice': 0})
		self.assertEqual(room.state, 'drawing')
		self.assertIsNone(room.view(guesser)['word'])
		self.assertEqual(room.view(drawer)['word'], room.word['word'])
		st, _   = self.call(None, f'/live/{code}/draw', {'pid': guesser, 'ops': STROKE})
		self.assertEqual(st, 400)
		self.call(None, f'/live/{code}/draw', {'pid': drawer, 'ops': STROKE})
		self.assertEqual(len(room.ops), 1)
		self.call(None, f'/live/{code}/say', {'pid': guesser, 'text': 'qqqq'})
		self.assertEqual(room.messages[-1]['kind'], 'miss')
		self.call(None, f'/live/{code}/say', {'pid': guesser, 'text': room.word['word']})
		self.assertTrue(any(m['kind'] == 'good' and m['name'] for m in room.messages))
		self.assertEqual(room.state, 'reveal')
		self.assertGreater(room.players[guesser]['score'], live.GUESS_BASE)

	def test_live_coins(self):
		'''Live turns pay only for a real drawing between two signed in players, and only up to the daily cap.'''

		def coins(name: str) -> int:
			with storage.db_connect() as c:
				return c.execute('SELECT coins_earned FROM users WHERE username=?', (name,)).fetchone()[0]

		def turn(drawer: str, guesser: str, ops: list) -> tuple:
			room = live.Room('PAY' + str(len(ops)), 'pay room', False, 1, 80, 8)
			with storage.db_connect() as c:
				ids = {n: (c.execute('SELECT id FROM users WHERE username=?', (n,)).fetchone() or [None])[0] for n in (drawer, guesser)}
			a, _ = room.join(ids[drawer], drawer)
			b, _ = room.join(ids[guesser], guesser)
			room.start(a)
			pid  = room.drawer
			room.choose(pid, 0)
			if ops:
				self.assertIsNone(room.draw(pid, ops))
			other = b if pid == a else a
			before = (coins('bob'), coins('carol'))
			room.say(other, room.word['word'])
			return (coins('bob') - before[0], coins('carol') - before[1])

		long_stroke = STROKE
		self.assertEqual(turn('bob', 'carol', [{'k': 'p', 'c': '#111111', 's': 8, 't': 0, 'p': [10, 10, 0, 50, 50, 16, 90, 20, 16]}]), (0, 0)) # a 3 point scribble pays nobody
		self.assertEqual(turn('bob', 'guesty', long_stroke), (0, 0)) # a guest guessing pays nobody
		self.assertTrue(all(turn('bob', 'carol', long_stroke))) # a real drawing between two accounts pays both
		day = time.strftime('%Y-%m-%d', time.gmtime())
		with storage.db_connect() as c:
			c.execute('UPDATE daily_coins SET coins=? WHERE day=?', (storage.DAY_CAP, day))
		self.assertEqual(turn('bob', 'carol', long_stroke), (0, 0)) # nothing past the daily cap


	def test_guess_rate(self):
		'''A duel guess right after another one is refused, and a tiny drawing can not be sent.'''

		_, g = self.call('admin', '/games/new', {'mode': 'user', 'opponent': 'carol'})
		self.call('admin', f'/games/{g["id"]}')
		st, res = self.call('admin', f'/games/{g["id"]}/draw', {'choice': 0, 'ops': [{'k': 'p', 'c': '#111111', 's': 8, 't': 0, 'p': [10, 10, 0, 50, 50, 16]}]})
		self.assertEqual(res.get('error'), 'draw a bit more first')
		self.call('admin', f'/games/{g["id"]}/draw', {'choice': 0, 'ops': [{'k': 'l', 'c': '#111111', 's': 8, 't': 0, 'p': [0, 0, 99, 99]}] * 3}) # three lines are enough
		with unittest.mock.patch.object(draw_app.duel, 'GUESS_EVERY', 1.0):
			self.assertNotIn('error', self.call('carol', f'/games/{g["id"]}/guess', {'guess': 'qqqq'})[1])
			self.assertEqual(self.call('carol', f'/games/{g["id"]}/guess', {'guess': 'qqqq'})[1].get('error'), 'slow down')


	def test_seo(self):
		'''Canonical, og:url, JSON-LD, robots.txt and the sitemap use DRAW_PUBLIC_URL when set and the request host when not.'''

		for public, root in (('', 'http://draw.example.org/'), ('https://draw.acid.vegas', 'https://draw.acid.vegas/')):
			with unittest.mock.patch.object(draw_app, 'PUBLIC_URL', public):
				page = self.client.get('/', base_url='http://draw.example.org').get_data(as_text=True)
				self.assertIn(f'<link rel="canonical" href="{root}">', page)
				self.assertIn(f'<meta property="og:url" content="{root}">', page)
				self.assertIn(f'<meta property="og:image" content="{root}static/og.png">', page)
				ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page).group(1))
				self.assertEqual((ld['url'], ld['image'], ld['@type']), (root, root + 'static/og.png', ['VideoGame', 'WebApplication']))
				self.assertIn(f'Sitemap: {root}sitemap.xml', self.client.get('/robots.txt', base_url='http://draw.example.org').get_data(as_text=True))
				self.assertIn(f'<loc>{root}</loc>', self.client.get('/sitemap.xml', base_url='http://draw.example.org').get_data(as_text=True))

	def test_standalone_no_feed(self):
		'''Without acid-arcade-db a streak or live win sends nothing to the feed.'''

		with unittest.mock.patch.object(arcade, 'event') as event, unittest.mock.patch.object(arcade, 'push_stats') as push:
			storage.arcade_event(1, 'streak', 'is on a 5 guess streak')
			room = live.Room('SOLO', 'solo room', False, 1, 80, 8)
			for name in ('alice', 'bob', 'carol'):
				pid, _ = room.join(1 if name == 'alice' else None, name)
				room.players[pid]['score'] = 500 if name == 'alice' else 0
			room.finish()
		event.assert_not_called()
		push.assert_not_called()

	def test_standalone_no_sso(self):
		'''Without acid-arcade-db no shared sign-in cookie is ever set and /auth/sso does not exist.'''

		client = draw_app.app.test_client()
		cap    = client.get('/auth/captcha').get_json()
		res    = client.post('/auth/signup', json={'username': 'dave', 'password': 'password123', 'captcha_id': cap['captcha_id'], 'captcha': auth.CAPTCHAS[cap['captcha_id']][0]}, environ_base={'REMOTE_ADDR': '10.0.1.1'})
		hdrs   = {'X-Auth-Token': res.get_json()['token']}
		for res in (res, client.post('/auth/login', json={'username': 'dave', 'password': 'password123'}), client.get('/auth/me', headers=hdrs), client.post('/auth/logout', json={}, headers=hdrs)):
			self.assertEqual(res.status_code, 200)
			self.assertFalse(res.headers.getlist('Set-Cookie'))
		self.assertEqual(client.get('/auth/sso').status_code, 404)

	def test_words(self):
		'''Submitted words need votes from players who played them, and admins can manage them.'''

		_, mine = self.call('alice', '/words/submit', {'text': '  Space   Hamster ', 'tier': 2})
		self.assertEqual(mine[0]['text'], 'space hamster')
		self.assertEqual(mine[0]['status'], 'pending')
		st, _   = self.call('alice', '/words/submit', {'text': 'space hamster', 'tier': 2})
		self.assertEqual(st, 400)
		st, _   = self.call('alice', '/words/submit', {'text': 'b4d!', 'tier': 1})
		self.assertEqual(st, 400)
		st, res = self.call('bob', '/words/vote', {'word': mine[0]['id'], 'vote': 1})
		self.assertIn('play', res['error'])
		st, _   = self.call('bob', '/admin/words')
		self.assertEqual(st, 403)
		_, adm  = self.call('admin', '/admin/words?status=pending')
		self.assertTrue(any(w['text'] == 'space hamster' for w in adm['words']))
		_, ok   = self.call('admin', f'/admin/words/{mine[0]["id"]}', {'action': 'approve'})
		self.assertTrue(ok['ok'])

	def test_word_helpers(self):
		'''Close guesses, letter matching and tiles.'''

		self.assertTrue(words.close('elephant', 'elephnt'))
		self.assertTrue(words.close('elephant', 'elehpant'))
		self.assertFalse(words.close('elephant', 'giraffe'))
		self.assertTrue(words.check('hot dog', 'HOTDOG'))
		t = words.tiles('hot dog')
		self.assertEqual(len(t), 12)
		for ch in 'HOTDOG':
			self.assertIn(ch, t)



if __name__ == '__main__':
	unittest.main()
