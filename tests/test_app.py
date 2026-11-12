#!/usr/bin/env python3
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_app.py

import os
import sys
import tempfile
import time
import unittest

os.environ['DRAW_DATA_DIR'] = tempfile.mkdtemp()
os.environ['DRAW_ADMINS']   = 'admin'
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'aciddraw'))

import app as draw_app
import auth
import hub
import live
import words


STROKE = [{'k': 'p', 'c': '#111111', 's': 8, 't': 0, 'p': [10, 10, 0, 50, 50, 16, 90, 20, 16]}]


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
