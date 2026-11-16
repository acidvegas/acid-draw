#!/usr/bin/env python3
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# aciddraw/words.py

import os
import random
import re
import secrets
import sqlite3
import time

from storage import DB_LOCK, db_connect


WORDS_DIR        = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'words')
TIERS            = {1: 'easy', 2: 'medium', 3: 'hard'}
WORD_RE          = re.compile(r'^[a-z]+( [a-z]+)*$')
LETTERS_RANGE    = (3, 14)  # letters in a word, spaces not counted
TILES_MIN        = 12       # letter tiles offered to an async guesser
COMMUNITY_CHANCE = 0.3      # chance a new community word is offered as one of the three choices
PROMOTE_NET      = 5        # a new word with this many more thumbs up than down joins the main pool
REJECT_NET       = -3       # a new word with this many more thumbs down than up is pulled
DROP_NET         = -8       # an accepted community word this far down is pulled too
SUBMIT_PER_DAY   = 10
PENDING_MAX      = 20       # new words one player can have waiting for votes
DECOY_LETTERS    = 'EEEEAAAARRRIIIOOOTTTNNNSSSLLCCUUDDPMMHGGBFYWKVXZJQ'


def admin_add(text: str, tier: int, user_id: int) -> str:
	'''Add a word straight into the main pool. Returns an error or None.'''

	word = normalize(text)
	if not word:
		return word_rules()
	if tier not in TIERS:
		return 'pick easy, medium or hard'
	with DB_LOCK, db_connect() as c:
		if c.execute('SELECT 1 FROM words WHERE text=?', (word,)).fetchone():
			return 'that word is already in the list'
		c.execute('INSERT INTO words(text, tier, source, status, submitted_by, created_at) VALUES(?, ?, ?, ?, ?, ?)', (word, tier, 'community', 'approved', user_id, int(time.time())))


def admin_list(status: str, query: str, offset: int) -> dict:
	'''Return words for the admin page.

	:param status: pending, approved, rejected, banned or all
	:param query: text the word must contain
	:param offset: rows to skip
	'''

	where, args = ['1=1'], []
	if status in ('pending', 'approved', 'rejected', 'banned'):
		where.append('w.status=?')
		args.append(status)
	if status != 'all' and status != 'banned' and not query:
		where.append('w.source=\'community\'')
	if query:
		where.append('w.text LIKE ?')
		args.append(f'%{query.strip().lower()}%')
	sql = f'SELECT w.id, w.text, w.tier, w.pack, w.source, w.status, w.up, w.down, w.plays, w.created_at, u.username AS by FROM words w LEFT JOIN users u ON u.id = w.submitted_by WHERE {" AND ".join(where)}'
	with db_connect() as c:
		total = c.execute(f'SELECT COUNT(*) FROM ({sql})', args).fetchone()[0]
		rows  = [dict(r) for r in c.execute(sql + ' ORDER BY w.created_at DESC, w.id DESC LIMIT 50 OFFSET ?', (*args, max(0, offset))).fetchall()]
	return {'total': total, 'words': rows}


def admin_set(word_id: int, action: str, tier: int = None) -> str:
	'''Change a word from the admin page. Returns an error or None.

	:param action: approve, reject, ban, tier or delete
	'''

	with DB_LOCK, db_connect() as c:
		row = c.execute('SELECT source FROM words WHERE id=?', (word_id,)).fetchone()
		if not row:
			return 'no such word'
		if action in ('approve', 'reject', 'ban'):
			c.execute('UPDATE words SET status=? WHERE id=?', ({'approve': 'approved', 'reject': 'rejected', 'ban': 'banned'}[action], word_id))
		elif action == 'tier' and tier in TIERS:
			c.execute('UPDATE words SET tier=? WHERE id=?', (tier, word_id))
		elif action == 'delete':
			if row['source'] != 'community':
				return 'built-in words can be banned, not deleted'
			c.execute('DELETE FROM words WHERE id=?', (word_id,))
			c.execute('DELETE FROM votes WHERE word_id=?', (word_id,))
			c.execute('DELETE FROM plays WHERE word_id=?', (word_id,))
		else:
			return 'unknown action'


def check(word: str, guess: str) -> bool:
	'''Return True if a guess matches the word, ignoring case, spaces and anything that is not a letter.'''

	return letters(word) == letters(guess)


def choose(c: sqlite3.Connection, drawer: int = None, exclude: set = ()) -> list:
	'''Pick one easy, one medium and one hard word to draw. Sometimes one is swapped for a new community word.

	:param drawer: the player choosing, whose own submissions are never offered to them
	:param exclude: word ids not to offer
	'''

	picks = []
	for tier in TIERS:
		rows = c.execute('SELECT id, text, tier FROM words WHERE status=\'approved\' AND tier=? ORDER BY RANDOM() LIMIT 8', (tier,)).fetchall()
		row  = next((r for r in rows if r['id'] not in exclude), rows[0] if rows else None)
		if row:
			picks.append({'id': row['id'], 'word': row['text'], 'tier': row['tier'], 'new': False})
	if random.random() < COMMUNITY_CHANCE:
		row = c.execute('SELECT id, text, tier FROM words WHERE status=\'pending\' AND (submitted_by IS NULL OR submitted_by != ?) ORDER BY RANDOM() LIMIT 1', (drawer or 0,)).fetchone()
		if row and row['id'] not in exclude:
			slot        = next((i for i, p in enumerate(picks) if p['tier'] == row['tier']), None)
			new         = {'id': row['id'], 'word': row['text'], 'tier': row['tier'], 'new': True}
			if picks:
				picks[slot if slot is not None else len(picks) - 1] = new
			else:
				picks.append(new)
	return picks


def close(word: str, guess: str) -> bool:
	'''Return True if a guess is one letter away from the word (a typo, a missing or an extra letter).'''

	a, b = letters(word), letters(guess)
	if a == b or abs(len(a) - len(b)) > 1 or len(a) < 4:
		return False
	if len(a) == len(b):
		diff = [i for i in range(len(a)) if a[i] != b[i]]
		return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
	short, long = (a, b) if len(a) < len(b) else (b, a)
	return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def letters(text: str) -> str:
	'''Return only the letters of a word, upper case.'''

	return re.sub(r'[^A-Z]', '', (text or '').upper())


def load_builtin():
	'''Add any built-in words missing from the database. Words already there keep their status, so banned ones stay banned.'''

	rows = []
	for tier, name in TIERS.items():
		with open(os.path.join(WORDS_DIR, f'{name}.txt')) as f:
			rows += [(w, tier, 'base') for w in (normalize(line) for line in f) if w]
	with open(os.path.join(WORDS_DIR, 'spicy.txt')) as f:
		for line in f:
			tier, _, text = line.strip().partition(' ')
			if tier.isdigit() and int(tier) in TIERS and normalize(text):
				rows.append((normalize(text), int(tier), 'spicy'))
	now = int(time.time())
	with DB_LOCK, db_connect() as c:
		c.executemany('INSERT OR IGNORE INTO words(text, tier, pack, source, status, created_at) VALUES(?, ?, ?, \'builtin\', \'approved\', ?)', [(*r, now) for r in rows])


def mine(user_id: int) -> list:
	'''Return the words a player has submitted, newest first.'''

	with db_connect() as c:
		return [dict(r) for r in c.execute('SELECT id, text, tier, status, up, down, plays, created_at FROM words WHERE submitted_by=? ORDER BY id DESC LIMIT 100', (user_id,)).fetchall()]


def normalize(text: str) -> str:
	'''Return a word in its stored form (lower case, single spaces), or None if it breaks the rules.'''

	word = ' '.join((text or '').lower().split())
	if not WORD_RE.match(word) or not LETTERS_RANGE[0] <= len(word.replace(' ', '')) <= LETTERS_RANGE[1]:
		return None
	return word


def pattern(word: str) -> list:
	'''Return the length of each word in a phrase, so the guesser knows where the gaps are.'''

	return [len(w) for w in word.split()]


def played(c: sqlite3.Connection, word_id: int, user_ids: list):
	'''Remember who played a word, which lets them vote on it.'''

	users = [u for u in user_ids if u]
	c.execute('UPDATE words SET plays = plays + 1 WHERE id=?', (word_id,))
	c.executemany('INSERT OR IGNORE INTO plays(word_id, user_id) VALUES(?, ?)', [(word_id, u) for u in users])


def submit(user_id: int, text: str, tier: int) -> str:
	'''Submit a new word. It is offered now and then, and the votes it gets decide whether it stays. Returns an error or None.'''

	word = normalize(text)
	if not word:
		return word_rules()
	if tier not in TIERS:
		return 'pick easy, medium or hard'
	now = int(time.time())
	with DB_LOCK, db_connect() as c:
		if c.execute('SELECT 1 FROM words WHERE text=?', (word,)).fetchone():
			return 'that word is already in the list'
		if c.execute('SELECT COUNT(*) FROM words WHERE submitted_by=? AND created_at > ?', (user_id, now - 86400)).fetchone()[0] >= SUBMIT_PER_DAY:
			return f'you can submit {SUBMIT_PER_DAY} words a day'
		if c.execute('SELECT COUNT(*) FROM words WHERE submitted_by=? AND status=\'pending\'', (user_id,)).fetchone()[0] >= PENDING_MAX:
			return f'you already have {PENDING_MAX} words waiting for votes'
		c.execute('INSERT INTO words(text, tier, source, status, submitted_by, created_at) VALUES(?, ?, \'community\', \'pending\', ?, ?)', (word, tier, user_id, now))


def tiles(word: str) -> str:
	'''Return the scrambled letter tiles for a word: its letters plus random decoys.'''

	need  = letters(word)
	tiles = list(need) + [secrets.choice(DECOY_LETTERS) for _ in range(max(TILES_MIN, len(need) + 2) - len(need))]
	random.shuffle(tiles)
	return ''.join(tiles)


def vote(user_id: int, word_id: int, value: int) -> dict:
	'''Thumbs up (1) or down (-1) a community word the player has played. Returns the new counts, or an error.'''

	if value not in (1, -1):
		return {'error': 'vote up or down'}
	with DB_LOCK, db_connect() as c:
		row = c.execute('SELECT source, status, submitted_by FROM words WHERE id=?', (word_id,)).fetchone()
		if not row or row['source'] != 'community':
			return {'error': 'only community words take votes'}
		if row['submitted_by'] == user_id:
			return {'error': 'you can not vote on your own word'}
		if not c.execute('SELECT 1 FROM plays WHERE word_id=? AND user_id=?', (word_id, user_id)).fetchone():
			return {'error': 'play a word before voting on it'}
		old = c.execute('SELECT vote FROM votes WHERE word_id=? AND user_id=?', (word_id, user_id)).fetchone()
		if old and old['vote'] == value:
			return {'error': 'you already voted'}
		c.execute('INSERT OR REPLACE INTO votes(word_id, user_id, vote) VALUES(?, ?, ?)', (word_id, user_id, value))
		up   = c.execute('SELECT COUNT(*) FROM votes WHERE word_id=? AND vote=1', (word_id,)).fetchone()[0]
		down = c.execute('SELECT COUNT(*) FROM votes WHERE word_id=? AND vote=-1', (word_id,)).fetchone()[0]
		status = row['status']
		if status == 'pending' and up - down >= PROMOTE_NET:
			status = 'approved'
		elif (status == 'pending' and up - down <= REJECT_NET) or (status == 'approved' and up - down <= DROP_NET):
			status = 'rejected'
		c.execute('UPDATE words SET up=?, down=?, status=? WHERE id=?', (up, down, status, word_id))
	return {'up': up, 'down': down, 'status': status}


def word_info(c: sqlite3.Connection, word_id: int, user_id: int) -> dict:
	'''Return what the voting buttons need to know about a word after a round: whether it is a community word and how this player voted.'''

	row = c.execute('SELECT source, submitted_by FROM words WHERE id=?', (word_id,)).fetchone()
	if not row or row['source'] != 'community':
		return None
	mine = c.execute('SELECT vote FROM votes WHERE word_id=? AND user_id=?', (word_id, user_id or 0)).fetchone()
	return {'id': word_id, 'by': row['submitted_by'] == user_id, 'vote': mine['vote'] if mine else 0}


def word_rules() -> str:
	'''Return the rules for a word, for error messages.'''

	return f'words are {LETTERS_RANGE[0]}-{LETTERS_RANGE[1]} letters, a-z and spaces only'
