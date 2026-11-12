#!/usr/bin/env python3
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# aciddraw/duel.py
#
# Async 1v1 games: one player draws, the other guesses whenever they get to it, then draws back.

import json
import random
import secrets
import sqlite3
import time

import canvas
import words
from storage import DB_LOCK, add_coins, arcade_push, db_connect, dumps, packs_of, spend, stat, username_of


EXPIRE_AFTER = 3 * 86400 # seconds without a move before a game ends and its streak is lost
ACTIVE_MAX   = 30        # games one player can have going at once
HISTORY_N    = 30        # rounds shown in a game's history
CHAT_KEEP    = 100       # chat messages shown per game
CHAT_MAX     = 300
RATE_SPAN    = 5.0       # seconds
RATE_MAX     = 4         # chat messages per span
HINTS        = {'reveal': 10, 'decoys': 15, 'skip': 20} # hint -> price in coins
BOMB_COST    = 25        # price to swap the three word choices for new ones
MISSES_KEEP  = 20        # wrong guesses kept per drawing
CHAT_TIMES   = {}        # user id -> recent chat post times


def bomb(user_id: int, game_id: int) -> str:
	'''Spend coins to swap the three word choices for new ones. Returns an error or None.'''

	with DB_LOCK, db_connect() as c:
		g = game_row(c, game_id, user_id)
		if not g or step(c, g, user_id) != 'draw':
			return 'it is not your turn to draw'
		if not spend(c, user_id, BOMB_COST):
			return f'a bomb costs {BOMB_COST} coins'
		old = {p['id'] for p in json.loads(g['choices'] or '[]')}
		c.execute('UPDATE games SET choices=? WHERE id=?', (dumps(words.choose(c, user_id, used_words(c, game_id) | old)), game_id))


def chat_post(user_id: int, game_id: int, text: str) -> str:
	'''Post in a game's chat. Returns an error or None.'''

	text = ' '.join((text or '').split())[:CHAT_MAX]
	if not text:
		return 'say something first'
	now   = time.time()
	times = [t for t in CHAT_TIMES.get(user_id, []) if now - t < RATE_SPAN]
	if len(times) >= RATE_MAX:
		return 'slow down a little'
	CHAT_TIMES[user_id] = times + [now]
	with DB_LOCK, db_connect() as c:
		if not game_row(c, game_id, user_id):
			return 'no such game'
		c.execute('INSERT INTO game_chat(game_id, user_id, text, at) VALUES(?, ?, ?, ?)', (game_id, user_id, text, int(now)))


def chat_since(game_id: int, last: int) -> list:
	'''Return a game's chat messages newer than an id, oldest first.'''

	with db_connect() as c:
		rows = c.execute('SELECT m.id, m.user_id, u.username AS name, m.text, m.at FROM game_chat m JOIN users u ON u.id = m.user_id WHERE m.game_id=? AND m.id > ? ORDER BY m.id DESC LIMIT ?', (game_id, last, CHAT_KEEP)).fetchall()
	return [dict(r) for r in reversed(rows)]


def create(user_id: int, mode: str, opponent: str = None) -> tuple:
	'''Start a game. Returns (game id, error).

	:param mode: random (pair with someone waiting, or wait for someone), user (challenge a player) or invite (anyone with the link)
	:param opponent: the username to challenge in user mode
	'''

	now = int(time.time())
	with DB_LOCK, db_connect() as c:
		if c.execute('SELECT COUNT(*) FROM games WHERE (a=? OR b=?) AND status!=\'over\'', (user_id, user_id)).fetchone()[0] >= ACTIVE_MAX:
			return None, f'you can have {ACTIVE_MAX} games going at once'
		if mode == 'random':
			mine = c.execute('SELECT id FROM games WHERE a=? AND status=\'open\' AND invite IS NULL', (user_id,)).fetchone()
			if mine:
				return mine['id'], None
			row = c.execute('''SELECT g.id FROM games g WHERE g.status='open' AND g.invite IS NULL AND g.a != ?
				AND EXISTS (SELECT 1 FROM rounds r WHERE r.game_id = g.id AND r.result = 'pending')
				AND g.a NOT IN (SELECT CASE WHEN a=? THEN b ELSE a END FROM games WHERE (a=? OR b=?) AND status='active')
				ORDER BY g.created_at LIMIT 1''', (user_id, user_id, user_id, user_id)).fetchone()
			if row:
				seat(c, row['id'], user_id)
				return row['id'], None
			return new_game(c, user_id, None, 'open', None, now), None
		if mode == 'invite':
			return new_game(c, user_id, None, 'open', secrets.token_urlsafe(6), now), None
		if mode == 'user':
			row = c.execute('SELECT id FROM users WHERE username=?', ((opponent or '').strip(),)).fetchone()
			if not row:
				return None, 'no player by that name'
			if row['id'] == user_id:
				return None, 'you can not play yourself'
			same = c.execute('SELECT id FROM games WHERE status=\'active\' AND ((a=? AND b=?) OR (a=? AND b=?))', (user_id, row['id'], row['id'], user_id)).fetchone()
			if same:
				return same['id'], None
			return new_game(c, user_id, row['id'], 'active', None, now), None
	return None, 'pick how to find an opponent'


def draw(user_id: int, game_id: int, choice: int, ops: list) -> str:
	'''Send a drawing of one of the three choices to the opponent. Returns an error or None.'''

	with DB_LOCK, db_connect() as c:
		g = game_row(c, game_id, user_id)
		if not g or step(c, g, user_id) != 'draw':
			return 'it is not your turn to draw'
		choices = json.loads(g['choices'] or '[]')
		if not isinstance(choice, int) or not 0 <= choice < len(choices):
			return 'pick a word to draw'
		ops, _, error = canvas.validate(ops, canvas.colors_for(packs_of(c, user_id)))
		if error:
			return error
		if not any(op['k'] in 'plrof' for op in ops):
			return 'draw something first'
		pick    = choices[choice]
		other   = g['b'] if g['a'] == user_id else g['a']
		now     = int(time.time())
		c.execute('INSERT INTO rounds(game_id, drawer, guesser, word_id, word, tier, ops, tiles, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)', (game_id, user_id, other, pick['id'], pick['word'], pick['tier'], dumps(ops), words.tiles(pick['word']), now))
		c.execute('UPDATE games SET turn=?, choices=NULL, updated_at=? WHERE id=?', (other, now, game_id))
		words.played(c, pick['id'], [user_id])


def ensure_choices(c: sqlite3.Connection, g: sqlite3.Row, user_id: int) -> list:
	'''Return the word choices for the player about to draw, picking them the first time so a refresh can not reroll them.'''

	if g['choices']:
		return json.loads(g['choices'])
	picks = words.choose(c, user_id, used_words(c, g['id']))
	c.execute('UPDATE games SET choices=? WHERE id=?', (dumps(picks), g['id']))
	return picks


def expire() -> set:
	'''End games nobody has moved in for too long. Returns the players involved.'''

	cutoff = int(time.time()) - EXPIRE_AFTER
	with DB_LOCK, db_connect() as c:
		rows = c.execute('SELECT id, a, b FROM games WHERE status!=\'over\' AND updated_at < ?', (cutoff,)).fetchall()
		for r in rows:
			c.execute('UPDATE games SET status=\'over\', turn=NULL, reason=\'expired after 3 days without a move\', updated_at=? WHERE id=?', (int(time.time()), r['id']))
	return {u for r in rows for u in (r['a'], r['b']) if u}


def game_row(c: sqlite3.Connection, game_id: int, user_id: int) -> sqlite3.Row:
	'''Return a game the player is in, or None.'''

	return c.execute('SELECT * FROM games WHERE id=? AND (a=? OR b=?)', (game_id, user_id, user_id)).fetchone()


def guess(user_id: int, game_id: int, text: str) -> dict:
	'''Guess the word of the drawing waiting for this player. Returns {'correct': bool, ...} or {'error': ...}.'''

	with DB_LOCK, db_connect() as c:
		g = game_row(c, game_id, user_id)
		r = pending(c, game_id, user_id) if g and g['turn'] == user_id and g['status'] == 'active' else None
		if not r:
			return {'error': 'there is nothing to guess'}
		if not words.check(r['word'], text):
			misses = json.loads(r['misses'])[1 - MISSES_KEEP:] + [' '.join((text or '').lower().split())[:30]]
			c.execute('UPDATE rounds SET attempts = attempts + 1, misses=? WHERE id=?', (json.dumps(misses), r['id']))
			return {'correct': False}
		now    = int(time.time())
		coins  = r['tier']
		streak = g['streak'] + 1
		c.execute('UPDATE rounds SET result=\'guessed\', solved_at=?, attempts = attempts + 1 WHERE id=?', (now, r['id']))
		c.execute('UPDATE games SET streak=?, best=MAX(best, ?), coins = coins + ?, updated_at=? WHERE id=?', (streak, streak, coins * 2, now, game_id))
		for uid in (r['drawer'], user_id):
			add_coins(c, uid, coins)
			c.execute('UPDATE users SET best_streak=MAX(best_streak, ?) WHERE id=?', (streak, uid))
		stat(c, r['drawer'], drawn=1, drawn_guessed=1)
		stat(c, user_id, guessed=1)
		words.played(c, r['word_id'], [user_id])
		for uid in (r['drawer'], user_id):
			arcade_push(c, uid)
		return {'correct': True, 'word': r['word'], 'coins': coins, 'streak': streak}


def hint(user_id: int, game_id: int, kind: str) -> dict:
	'''Buy a hint on the drawing waiting for this player: reveal a letter, remove the decoy tiles, or skip it without losing the streak.'''

	if kind not in HINTS:
		return {'error': 'unknown hint'}
	with DB_LOCK, db_connect() as c:
		g = game_row(c, game_id, user_id)
		r = pending(c, game_id, user_id) if g and g['turn'] == user_id and g['status'] == 'active' else None
		if not r:
			return {'error': 'there is nothing to guess'}
		hints = json.loads(r['hints'])
		need  = words.letters(r['word'])
		if kind == 'reveal':
			left = [i for i in range(len(need)) if i not in hints.get('revealed', [])]
			if len(left) <= 1:
				return {'error': 'only one letter is left'}
		if kind == 'decoys' and hints.get('decoys'):
			return {'error': 'the decoys are already gone'}
		if not spend(c, user_id, HINTS[kind]):
			return {'error': f'that costs {HINTS[kind]} coins'}
		if kind == 'skip':
			resolve_pass(c, g, r, keep_streak=True)
			return {'skipped': True, 'word': r['word']}
		if kind == 'reveal':
			hints['revealed'] = hints.get('revealed', []) + [random.choice(left)]
		else:
			hints['decoys'] = True
		c.execute('UPDATE rounds SET hints=? WHERE id=?', (dumps(hints), r['id']))
	return {'ok': True}


def join(user_id: int, code: str) -> tuple:
	'''Take the open seat in a game from an invite link. Returns (game id, error).'''

	with DB_LOCK, db_connect() as c:
		g = c.execute('SELECT id, a, b, status FROM games WHERE invite=?', (code or '',)).fetchone()
		if not g:
			return None, 'that invite link does not work'
		if user_id in (g['a'], g['b']):
			return g['id'], None
		if g['status'] != 'open':
			return None, 'someone already took that game'
		seat(c, g['id'], user_id)
		return g['id'], None


def list_games(user_id: int) -> list:
	'''Return a player's games: everything going, then games that ended in the last 3 days.'''

	now = int(time.time())
	out = []
	with DB_LOCK, db_connect() as c:
		rows = c.execute('SELECT * FROM games WHERE (a=? OR b=?) AND (status!=\'over\' OR updated_at > ?) ORDER BY updated_at DESC LIMIT 60', (user_id, user_id, now - EXPIRE_AFTER)).fetchall()
		for g in rows:
			other = g['b'] if g['a'] == user_id else g['a']
			out.append({'id': g['id'], 'opponent': username_of(c, other), 'opponent_id': other, 'status': g['status'], 'step': step(c, g, user_id), 'streak': g['streak'], 'best': g['best'], 'updated_at': g['updated_at'], 'expires_at': g['updated_at'] + EXPIRE_AFTER, 'reason': g['reason'], 'invite': g['invite'] if g['a'] == user_id and g['status'] == 'open' else None})
	order = {'guess': 0, 'draw': 0, 'wait': 1, 'over': 2}
	out.sort(key=lambda x: (order[x['step']], -x['updated_at']))
	return out


def new_game(c: sqlite3.Connection, user_id: int, other: int, status: str, invite: str, now: int) -> int:
	'''Insert a game where the creator draws first. Returns its id.'''

	return c.execute('INSERT INTO games(a, b, invite, status, turn, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)', (user_id, other, invite, status, user_id, now, now)).lastrowid


def pass_round(user_id: int, game_id: int) -> dict:
	'''Give up on the drawing waiting for this player. The streak goes back to zero.'''

	with DB_LOCK, db_connect() as c:
		g = game_row(c, game_id, user_id)
		r = pending(c, game_id, user_id) if g and g['turn'] == user_id and g['status'] == 'active' else None
		if not r:
			return {'error': 'there is nothing to guess'}
		resolve_pass(c, g, r, keep_streak=False)
		return {'passed': True, 'word': r['word']}


def pending(c: sqlite3.Connection, game_id: int, user_id: int) -> sqlite3.Row:
	'''Return the drawing in a game waiting for this player to guess, or None.'''

	return c.execute('SELECT * FROM rounds WHERE game_id=? AND guesser=? AND result=\'pending\' ORDER BY id LIMIT 1', (game_id, user_id)).fetchone()


def players(game_id: int) -> set:
	'''Return the players in a game.'''

	with db_connect() as c:
		g = c.execute('SELECT a, b FROM games WHERE id=?', (game_id,)).fetchone()
	return {u for u in (g['a'], g['b']) if u} if g else set()


def resign(user_id: int, game_id: int) -> str:
	'''Leave a game for good. Returns an error or None.'''

	with DB_LOCK, db_connect() as c:
		g = game_row(c, game_id, user_id)
		if not g or g['status'] == 'over':
			return 'that game is already over'
		reason = 'cancelled' if g['status'] == 'open' else f'{username_of(c, user_id)} resigned'
		c.execute('UPDATE games SET status=\'over\', turn=NULL, choices=NULL, reason=?, updated_at=? WHERE id=?', (reason, int(time.time()), game_id))


def resolve_pass(c: sqlite3.Connection, g: sqlite3.Row, r: sqlite3.Row, keep_streak: bool):
	'''Close a drawing nobody guessed. Skipping with coins keeps the streak, passing for free loses it.'''

	now = int(time.time())
	c.execute('UPDATE rounds SET result=?, solved_at=? WHERE id=?', ('skipped' if keep_streak else 'passed', now, r['id']))
	c.execute(f'UPDATE games SET {"" if keep_streak else "streak=0, "}updated_at=? WHERE id=?', (now, g['id']))
	stat(c, r['drawer'], drawn=1)
	words.played(c, r['word_id'], [r['guesser']])


def round_ops(user_id: int, game_id: int, round_id: int) -> dict:
	'''Return a drawing so it can be replayed. The word is left out until the drawing has been guessed or given up.'''

	with db_connect() as c:
		if not game_row(c, game_id, user_id):
			return None
		r = c.execute('SELECT id, drawer, word, tier, ops, result FROM rounds WHERE id=? AND game_id=?', (round_id, game_id)).fetchone()
	if not r:
		return None
	show = r['result'] != 'pending' or r['drawer'] == user_id
	return {'id': r['id'], 'ops': json.loads(r['ops']), 'word': r['word'] if show else None, 'tier': r['tier'], 'result': r['result']}


def seat(c: sqlite3.Connection, game_id: int, user_id: int):
	'''Put a second player into an open game. If the creator has already drawn, it becomes their turn to guess.'''

	now = int(time.time())
	r   = c.execute('SELECT id FROM rounds WHERE game_id=? AND result=\'pending\' AND guesser IS NULL', (game_id,)).fetchone()
	if r:
		c.execute('UPDATE rounds SET guesser=? WHERE id=?', (user_id, r['id']))
		c.execute('UPDATE games SET b=?, status=\'active\', turn=?, updated_at=? WHERE id=?', (user_id, user_id, now, game_id))
	else:
		c.execute('UPDATE games SET b=?, status=\'active\', updated_at=? WHERE id=?', (user_id, now, game_id))


def step(c: sqlite3.Connection, g: sqlite3.Row, user_id: int) -> str:
	'''Return what a player has to do in a game: guess, draw, wait or over.'''

	if g['status'] == 'over':
		return 'over'
	if g['turn'] != user_id:
		return 'wait'
	return 'guess' if pending(c, g['id'], user_id) else 'draw'


def tile_view(r: sqlite3.Row) -> dict:
	'''Return the letter tiles and revealed letters a guesser sees, never the word itself.'''

	hints    = json.loads(r['hints'])
	need     = words.letters(r['word'])
	tiles    = list(r['tiles'])
	revealed = {i: need[i] for i in hints.get('revealed', [])}
	if hints.get('decoys'):
		left, keep = list(need), []
		for t in tiles:
			if t in left:
				left.remove(t)
				keep.append(t)
		tiles = keep
	for letter in revealed.values():
		tiles.remove(letter)
	return {'tiles': tiles, 'revealed': revealed, 'decoys': bool(hints.get('decoys'))}


def used_words(c: sqlite3.Connection, game_id: int) -> set:
	'''Return the ids of words already drawn in a game.'''

	return {r['word_id'] for r in c.execute('SELECT word_id FROM rounds WHERE game_id=?', (game_id,))}


def view(user_id: int, game_id: int) -> dict:
	'''Return a game as one player sees it.'''

	with DB_LOCK, db_connect() as c:
		g = game_row(c, game_id, user_id)
		if not g:
			return None
		other = g['b'] if g['a'] == user_id else g['a']
		now   = step(c, g, user_id)
		out   = {'id': g['id'], 'status': g['status'], 'step': now, 'opponent': username_of(c, other), 'opponent_id': other, 'streak': g['streak'], 'best': g['best'], 'coins': g['coins'], 'reason': g['reason'], 'expires_at': g['updated_at'] + EXPIRE_AFTER, 'invite': g['invite'] if g['status'] == 'open' else None, 'mine': g['a'] == user_id}
		if now == 'guess':
			r = pending(c, game_id, user_id)
			out['round'] = {'id': r['id'], 'tier': r['tier'], 'pattern': words.pattern(r['word']), 'attempts': r['attempts'], 'misses': json.loads(r['misses']), 'drawer': username_of(c, r['drawer']), 'drawer_id': r['drawer'], **tile_view(r)}
		elif now == 'draw':
			out['choices'] = [{'word': p['word'], 'tier': p['tier'], 'new': p['new']} for p in ensure_choices(c, g, user_id)]
		rows = c.execute('SELECT id, drawer, word, word_id, tier, result, attempts, misses, created_at, solved_at FROM rounds WHERE game_id=? ORDER BY id DESC LIMIT ?', (game_id, HISTORY_N)).fetchall()
		out['history'] = [{'id': r['id'], 'mine': r['drawer'] == user_id, 'word': r['word'] if r['result'] != 'pending' or r['drawer'] == user_id else None, 'tier': r['tier'], 'result': r['result'], 'attempts': r['attempts'], 'misses': json.loads(r['misses']), 'at': r['solved_at'] or r['created_at']} for r in rows]
		last = next((r for r in rows if r['result'] != 'pending'), None)
		if last:
			out['last'] = {'id': last['id'], 'mine': last['drawer'] == user_id, 'word': last['word'], 'tier': last['tier'], 'result': last['result'], 'misses': json.loads(last['misses']), 'vote': words.word_info(c, last['word_id'], user_id)}
	return out
