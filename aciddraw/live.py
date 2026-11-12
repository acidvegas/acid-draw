#!/usr/bin/env python3
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# aciddraw/live.py
#
# Live rooms: everyone takes a turn drawing while the rest race to type the word.

import collections
import random
import secrets
import time

import canvas
import words
from storage import DB_LOCK, add_coins, arcade_push, db_connect, packs_of, stat


CHOOSE_TIME  = 15             # seconds the drawer gets to pick a word
REVEAL_TIME  = 6              # seconds the word is shown between turns
DONE_TIME    = 20             # seconds the final scores stay up before the room goes back to waiting
AWAY_AFTER   = 30             # seconds without the room open before a player is dropped
EMPTY_AFTER  = 60             # seconds an empty room stays around
ROOMS_MAX    = 200
ROUND_CHOICE = (1, 2, 3, 4, 5, 6)
TIME_CHOICE  = (60, 80, 100, 120)
SIZE_CHOICE  = range(2, 11)
CHAT_KEEP    = 150
CHAT_MAX     = 120
RATE_SPAN    = 5.0            # seconds
RATE_MAX     = 5              # messages per span
GUESS_BASE   = 100            # points for any correct guess...
GUESS_SPEED  = 400            # ...plus up to this much more the faster it comes
DRAWER_EACH  = 50             # points the drawer gets for every player who guesses


class Room:
	'''A live room.'''

	def __init__(self, code: str, name: str, public: bool, rounds: int, seconds: int, size: int):
		self.code        = code
		self.name        = name
		self.public      = public
		self.rounds      = rounds
		self.seconds     = seconds
		self.size        = size
		self.players     = {}   # pid -> player
		self.order       = []   # pids in seat order
		self.host        = None
		self.state       = 'lobby' # lobby, choosing, drawing, reveal or done
		self.round       = 0
		self.queue       = []   # pids still to draw this round
		self.drawer      = None
		self.choices     = []
		self.word        = None
		self.turn        = 0    # goes up every time the canvas is wiped, so watchers know to clear it
		self.ops         = []
		self.points      = 0
		self.started     = 0
		self.deadline    = 0
		self.revealed    = []   # letter positions shown to guessers
		self.reveal      = None # what happened in the turn that just ended
		self.results     = None # final scores of the last game
		self.messages    = collections.deque(maxlen=CHAT_KEEP)
		self.next_msg    = 0
		self.next_id     = 0
		self.banned      = set()
		self.empty_since = time.time()

	def add_msg(self, text: str, kind: str = 'system', name: str = None, to: set = None):
		'''Add a chat line.

		:param kind: chat, system, good (someone guessed), miss (a wrong guess), close or secret
		:param to: pids allowed to see it, or None for everyone
		'''

		self.next_msg += 1
		self.messages.append({'id': self.next_msg, 'name': name, 'text': text, 'kind': kind, 'to': to})

	def begin_draw(self, index: int):
		'''Start the drawing part of a turn with the chosen word.'''

		self.word     = self.choices[index]
		self.state    = 'drawing'
		self.turn    += 1
		self.ops      = []
		self.points   = 0
		self.started  = time.time()
		self.deadline = self.started + self.seconds
		self.revealed = []
		self.reveal   = None
		for p in self.players.values():
			p['guessed'] = False
			p['gain']    = 0
		self.add_msg(f'{self.players[self.drawer]["name"]} is drawing')

	def choose(self, pid: str, index: int) -> str:
		'''The drawer picks one of the three words. Returns an error or None.'''

		if self.state != 'choosing' or pid != self.drawer:
			return 'it is not your turn to pick'
		if not isinstance(index, int) or not 0 <= index < len(self.choices):
			return 'pick a word'
		self.begin_draw(index)

	def draw(self, pid: str, ops: list) -> str:
		'''Add strokes from the drawer. Returns an error or None.'''

		if self.state != 'drawing' or pid != self.drawer:
			return 'it is not your turn to draw'
		ops, points, error = canvas.validate(ops, canvas.colors_for(self.players[pid]['packs']), len(self.ops), self.points)
		if error:
			return error
		self.ops    += ops
		self.points += points

	def end_turn(self):
		'''Show the word, pay the drawer and save the stats of the turn.'''

		guessers     = [p for pid, p in self.players.items() if pid != self.drawer and p['guessed']]
		drawer       = self.players.get(self.drawer)
		self.state    = 'reveal'
		self.deadline = time.time() + REVEAL_TIME
		self.reveal   = {'word': self.word['word'], 'drawer': drawer['name'] if drawer else None, 'gains': {p['id']: p['gain'] for p in self.players.values() if p['gain']}, 'word_id': self.word['id']}
		self.add_msg(f'The word was {self.word["word"].upper()}')
		with DB_LOCK, db_connect() as c:
			if drawer and drawer['user_id']:
				stat(c, drawer['user_id'], drawn=1, drawn_guessed=1 if guessers else 0)
				if guessers:
					add_coins(c, drawer['user_id'], self.word['tier'])
				arcade_push(c, drawer['user_id'])
			words.played(c, self.word['id'], [p['user_id'] for p in self.players.values()])

	def finish(self):
		'''End the game and save who won.'''

		self.state    = 'done'
		self.deadline = time.time() + DONE_TIME
		ranked        = sorted(self.players.values(), key=lambda p: -p['score'])
		top           = ranked[0]['score'] if ranked else 0
		self.results  = [{'name': p['name'], 'user_id': p['user_id'], 'score': p['score'], 'winner': top > 0 and p['score'] == top} for p in ranked]
		self.add_msg(f'Game over! {" and ".join(r["name"] for r in self.results if r["winner"]) or "Nobody"} won')
		with DB_LOCK, db_connect() as c:
			for p in ranked:
				if p['user_id']:
					stat(c, p['user_id'], live_games=1, live_wins=1 if top > 0 and p['score'] == top and len(ranked) > 1 else 0)
					arcade_push(c, p['user_id'])

	def gone(self) -> bool:
		'''Return True once the room has been empty long enough to close.'''

		return not self.players and time.time() - self.empty_since > EMPTY_AFTER

	def join(self, user_id: int, name: str) -> tuple:
		'''Seat a player. A signed in player who is already here gets their seat back. Returns (pid, error).'''

		for pid, p in self.players.items():
			if user_id and p['user_id'] == user_id:
				p['seen'] = time.time()
				return pid, None
		if name.lower() in self.banned:
			return None, 'you were kicked from this room'
		if any(p['name'].lower() == name.lower() for p in self.players.values()):
			return None, 'someone in the room already has that name'
		if len(self.players) >= self.size:
			return None, 'that room is full'
		with db_connect() as c:
			packs = packs_of(c, user_id)
		pid           = secrets.token_urlsafe(16)
		self.next_id += 1
		self.players[pid] = {'id': self.next_id, 'name': name, 'user_id': user_id, 'packs': packs, 'score': 0, 'gain': 0, 'guessed': False, 'seen': time.time(), 'times': []}
		self.order.append(pid)
		self.host        = self.host or pid
		self.empty_since = None
		self.add_msg(f'{name} joined')
		return pid, None

	def kick(self, pid: str, target: int) -> str:
		'''The host removes a player, who can not come back. Returns an error or None.'''

		if pid != self.host:
			return 'only the host can kick'
		victim = next((k for k, p in self.players.items() if p['id'] == target), None)
		if not victim or victim == pid:
			return 'no such player'
		self.banned.add(self.players[victim]['name'].lower())
		self.remove(victim, 'was kicked')

	def listing(self) -> dict:
		'''Return the room as shown in the public room list.'''

		host = self.players.get(self.host)
		return {'code': self.code, 'name': self.name, 'players': len(self.players), 'size': self.size, 'state': self.state, 'round': self.round, 'rounds': self.rounds, 'host': host['name'] if host else None}

	def mask(self) -> str:
		'''Return the word with every letter not revealed yet as an underscore.'''

		out, i = [], 0
		for ch in self.word['word']:
			if ch == ' ':
				out.append(' ')
				continue
			out.append(ch if i in self.revealed else '_')
			i += 1
		return ''.join(out)

	def next_turn(self):
		'''Hand the pen to the next player, starting a new round or ending the game when everyone has drawn.'''

		self.queue = [pid for pid in self.queue if pid in self.players]
		if not self.queue:
			self.round += 1
			if self.round > self.rounds:
				return self.finish()
			self.queue = list(self.order)
		self.drawer   = self.queue.pop(0)
		with DB_LOCK, db_connect() as c:
			self.choices = words.choose(c, self.players[self.drawer]['user_id'])
		self.state    = 'choosing'
		self.deadline = time.time() + CHOOSE_TIME
		self.turn    += 1
		self.ops      = []
		self.points   = 0
		self.word     = None

	def remove(self, pid: str, how: str = 'left'):
		'''Take a player out of the room.'''

		p = self.players.pop(pid, None)
		if not p:
			return
		self.order = [x for x in self.order if x != pid]
		self.queue = [x for x in self.queue if x != pid]
		if self.host == pid:
			self.host = self.order[0] if self.order else None
		self.add_msg(f'{p["name"]} {how}')
		if not self.players:
			self.empty_since = time.time()
			self.state       = 'lobby'
			return
		if self.state in ('choosing', 'drawing', 'reveal') and len(self.players) < 2:
			self.state = 'lobby'
			self.add_msg('Not enough players left, back to the lobby')
		elif pid == self.drawer and self.state == 'choosing':
			self.next_turn()
		elif self.state == 'drawing' and (pid == self.drawer or all(q['guessed'] for k, q in self.players.items() if k != self.drawer)):
			self.end_turn()

	def say(self, pid: str, text: str) -> str:
		'''Post in the room. While someone draws, chat is also guessing. Returns an error or None.'''

		p    = self.players.get(pid)
		text = ' '.join((text or '').split())[:CHAT_MAX]
		if not p:
			return 'you are not in this room'
		if not text:
			return 'say something first'
		now  = time.time()
		p['times'] = [t for t in p['times'] if now - t < RATE_SPAN]
		if len(p['times']) >= RATE_MAX:
			return 'slow down a little'
		p['times'].append(now)
		if self.state != 'drawing':
			return self.add_msg(text, 'chat', p['name'])
		answer = words.letters(self.word['word'])
		if pid == self.drawer:
			if answer in words.letters(text):
				return 'no giving away the word'
			return self.add_msg(text, 'chat', p['name'])
		if p['guessed']:
			return self.add_msg(text, 'secret', p['name'], to={k for k, q in self.players.items() if q['guessed'] or k == self.drawer})
		if words.check(self.word['word'], text):
			left        = max(0, self.deadline - now)
			gain        = GUESS_BASE + round(GUESS_SPEED * left / self.seconds)
			p['guessed'] = True
			p['gain']    = gain
			p['score']  += gain
			drawer       = self.players[self.drawer]
			drawer['score'] += DRAWER_EACH
			drawer['gain']  += DRAWER_EACH
			self.add_msg(f'guessed the word! +{gain}', 'good', p['name'])
			if p['user_id']:
				with DB_LOCK, db_connect() as c:
					add_coins(c, p['user_id'], self.word['tier'])
					stat(c, p['user_id'], guessed=1)
					arcade_push(c, p['user_id'])
			if all(q['guessed'] for k, q in self.players.items() if k != self.drawer):
				self.end_turn()
			return
		self.add_msg(text, 'miss', p['name'])
		if words.close(self.word['word'], text):
			self.add_msg(f'"{text}" is close!', 'close', to={pid})

	def seen(self, pid: str):
		'''Mark a player as still having the room open.'''

		if pid in self.players:
			self.players[pid]['seen'] = time.time()

	def settings(self, pid: str, data: dict) -> str:
		'''The host changes the room settings between games. Returns an error or None.'''

		if pid != self.host:
			return 'only the host can change the settings'
		if self.state not in ('lobby', 'done'):
			return 'settings can only change between games'
		if data.get('rounds') in ROUND_CHOICE:
			self.rounds = data['rounds']
		if data.get('seconds') in TIME_CHOICE:
			self.seconds = data['seconds']
		if data.get('size') in SIZE_CHOICE:
			self.size = max(data['size'], len(self.players))
		if isinstance(data.get('public'), bool):
			self.public = data['public']

	def since(self, pid: str, last: int) -> list:
		'''Return the chat lines a player may see newer than an id.'''

		return [{k: v for k, v in m.items() if k != 'to'} for m in self.messages if m['id'] > last and (m['to'] is None or pid in m['to'])]

	def start(self, pid: str) -> str:
		'''The host starts a game. Returns an error or None.'''

		if pid != self.host:
			return 'only the host can start'
		if self.state not in ('lobby', 'done'):
			return 'the game is already going'
		if len(self.players) < 2:
			return 'you need at least 2 players'
		for p in self.players.values():
			p['score'] = 0
		self.round   = 1
		self.queue   = list(self.order)
		self.results = None
		self.add_msg('The game is starting!')
		self.next_turn()

	def tick(self) -> bool:
		'''Run the room clock. Returns True when anything changed.'''

		now     = time.time()
		changed = False
		for pid in [k for k, p in self.players.items() if now - p['seen'] > AWAY_AFTER]:
			self.remove(pid)
			changed = True
		if self.state == 'choosing' and now >= self.deadline:
			self.begin_draw(min(1, len(self.choices) - 1))
			changed = True
		elif self.state == 'drawing':
			n     = len(words.letters(self.word['word']))
			want  = 0 if n < 4 else 1 if n < 7 else 2
			due   = (now - self.started) / self.seconds
			shown = len(self.revealed)
			if shown < want and due >= (0.5, 0.75)[shown]:
				self.revealed.append(random.choice([i for i in range(n) if i not in self.revealed]))
				changed = True
			if now >= self.deadline:
				self.end_turn()
				changed = True
		elif self.state == 'reveal' and now >= self.deadline:
			self.next_turn()
			changed = True
		elif self.state == 'done' and now >= self.deadline:
			self.state = 'lobby'
			changed    = True
		return changed

	def view(self, pid: str) -> dict:
		'''Return the room as one player sees it. The word only goes to the drawer, players who guessed it, and everyone once the turn is over.'''

		me     = self.players.get(pid)
		drawer = self.players.get(self.drawer)
		knows  = me and (pid == self.drawer or me['guessed'])
		out    = {
			'code'     : self.code,
			'name'     : self.name,
			'public'   : self.public,
			'rounds'   : self.rounds,
			'seconds'  : self.seconds,
			'size'     : self.size,
			'state'    : self.state,
			'round'    : self.round,
			'deadline' : self.deadline,
			'turn'     : self.turn,
			'me'       : me['id'] if me else None,
			'host'     : pid == self.host,
			'packs'    : me['packs'] if me else [],
			'drawer'   : drawer['id'] if drawer and self.state in ('choosing', 'drawing', 'reveal') else None,
			'players'  : [{'id': p['id'], 'name': p['name'], 'user_id': p['user_id'], 'score': p['score'], 'guessed': p['guessed'] and self.state == 'drawing', 'host': k == self.host, 'guest': not p['user_id']} for k, p in ((k, self.players[k]) for k in self.order)],
			'results'  : self.results,
			'choices'  : [{'word': c['word'], 'tier': c['tier'], 'new': c['new']} for c in self.choices] if self.state == 'choosing' and pid == self.drawer else None
		}
		if self.state == 'drawing':
			out['word'] = self.word['word'] if knows else None
			out['mask'] = self.mask()
			out['tier'] = self.word['tier']
		if self.state == 'reveal' and self.reveal:
			out['reveal'] = {k: v for k, v in self.reveal.items() if k != 'word_id'}
			if me and me['user_id']:
				with db_connect() as c:
					out['reveal']['vote'] = words.word_info(c, self.reveal['word_id'], me['user_id'])
		return out
