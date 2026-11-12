#!/usr/bin/env python3
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# aciddraw/storage.py

import json
import os
import sqlite3
import threading
import time

import arcade


DATA_DIR      = os.environ.get('DRAW_DATA_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))
DB_PATH       = os.path.join(DATA_DIR, 'aciddraw.db')
DB_LOCK       = threading.RLock()
START_COINS   = 50
SIGNUP_EVERY  = 86400 # one new account per IP address in this many seconds
LEADERBOARD_N = 25
DRAWER_MIN    = 10    # drawings needed before showing up on the best drawer board
BOARDS        = ('coins', 'streak', 'live', 'drawer')

os.makedirs(DATA_DIR, exist_ok=True)


def add_coins(c: sqlite3.Connection, user_id: int, amount: int):
	'''Give a player coins they earned, which also count toward the coins leaderboard.'''

	if user_id and amount:
		c.execute('UPDATE users SET coins = coins + ?, coins_earned = coins_earned + ? WHERE id=?', (amount, amount, user_id))


def arcade_event(user_id: int, kind: str, text: str):
	'''Add a line to the arcade activity feed off the request path. Guests and standalone mode send nothing.'''

	if not user_id or not arcade.enabled():
		return

	def send():
		try:
			arcade.event(user_id, kind, text)
		except Exception as e:
			print(f'arcade event error for user {user_id}: {e!r}')

	threading.Thread(target=send, daemon=True).start()


def arcade_push(c: sqlite3.Connection, user_id: int):
	'''Send a player's summary to acid-arcade-db off the request path. Read through the open connection so the change being made is included.'''

	if not user_id or not arcade.enabled():
		return
	row = c.execute('SELECT coins, coins_earned, best_streak, drawn, live_games FROM users WHERE id=?', (user_id,)).fetchone()
	if not row:
		return
	games = c.execute('SELECT COUNT(*) FROM games WHERE (a=? OR b=?) AND b IS NOT NULL', (user_id, user_id)).fetchone()[0] + row['live_games']
	stats = {'Coins earned': row['coins_earned'], 'Coins': row['coins'], 'Best streak': row['best_streak'], 'Drawings': row['drawn'], 'Games': games}
	threading.Thread(target=arcade_send, args=(user_id, row['coins_earned'], stats), daemon=True).start()


def arcade_send(user_id: int, main: int, stats: dict):
	'''Push stats to acid-arcade-db, printing failures so gameplay never notices.'''

	try:
		arcade.push_stats(user_id, main, stats)
	except Exception as e:
		print(f'arcade stats error for user {user_id}: {e!r}')


def db_connect() -> sqlite3.Connection:
	'''Open a connection to the database.'''

	conn = sqlite3.connect(DB_PATH, timeout=10)
	conn.row_factory = sqlite3.Row
	return conn


def delete_user(user_id: int):
	'''Delete everything kept for an account deleted in acid-arcade-db: its games, chat, plays, votes, sessions and the mirror row. Words it submitted stay in the pool.'''

	with DB_LOCK, db_connect() as c:
		c.execute('DELETE FROM rounds WHERE game_id IN (SELECT id FROM games WHERE a=? OR b=?)', (user_id, user_id))
		c.execute('DELETE FROM game_chat WHERE user_id=? OR game_id IN (SELECT id FROM games WHERE a=? OR b=?)', (user_id, user_id, user_id))
		c.execute('DELETE FROM games WHERE a=? OR b=?', (user_id, user_id))
		c.execute('DELETE FROM plays WHERE user_id=?', (user_id,))
		c.execute('DELETE FROM votes WHERE user_id=?', (user_id,))
		c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
		c.execute('UPDATE words SET submitted_by=NULL WHERE submitted_by=?', (user_id,))
		c.execute('DELETE FROM users WHERE id=?', (user_id,))


def dumps(obj) -> str:
	'''Compact JSON for storing in the database.'''

	return json.dumps(obj, separators=(',', ':'))


def init_db():
	'''Create tables.'''

	with DB_LOCK, db_connect() as c:
		c.execute('PRAGMA journal_mode=WAL')
		c.execute('''CREATE TABLE IF NOT EXISTS users (
			id            INTEGER PRIMARY KEY AUTOINCREMENT,
			username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
			password_hash TEXT NOT NULL,
			created_at    INTEGER NOT NULL,
			signup_ip     TEXT,
			coins         INTEGER NOT NULL DEFAULT 0,
			coins_earned  INTEGER NOT NULL DEFAULT 0,
			best_streak   INTEGER NOT NULL DEFAULT 0,
			drawn         INTEGER NOT NULL DEFAULT 0,
			drawn_guessed INTEGER NOT NULL DEFAULT 0,
			guessed       INTEGER NOT NULL DEFAULT 0,
			live_games    INTEGER NOT NULL DEFAULT 0,
			live_wins     INTEGER NOT NULL DEFAULT 0,
			packs         TEXT NOT NULL DEFAULT ''
		)''')
		c.execute('''CREATE TABLE IF NOT EXISTS sessions (
			token      TEXT PRIMARY KEY,
			user_id    INTEGER NOT NULL,
			expires_at INTEGER NOT NULL
		)''')
		c.execute('''CREATE TABLE IF NOT EXISTS words (
			id           INTEGER PRIMARY KEY AUTOINCREMENT,
			text         TEXT NOT NULL UNIQUE COLLATE NOCASE,
			tier         INTEGER NOT NULL,
			pack         TEXT NOT NULL DEFAULT 'base',
			source       TEXT NOT NULL,
			status       TEXT NOT NULL,
			submitted_by INTEGER,
			created_at   INTEGER NOT NULL,
			up           INTEGER NOT NULL DEFAULT 0,
			down         INTEGER NOT NULL DEFAULT 0,
			plays        INTEGER NOT NULL DEFAULT 0
		)''')
		c.execute('CREATE INDEX IF NOT EXISTS words_pick ON words(status, tier)')
		c.execute('''CREATE TABLE IF NOT EXISTS plays (
			word_id INTEGER NOT NULL,
			user_id INTEGER NOT NULL,
			PRIMARY KEY (word_id, user_id)
		)''')
		c.execute('''CREATE TABLE IF NOT EXISTS votes (
			word_id INTEGER NOT NULL,
			user_id INTEGER NOT NULL,
			vote    INTEGER NOT NULL,
			PRIMARY KEY (word_id, user_id)
		)''')
		c.execute('''CREATE TABLE IF NOT EXISTS games (
			id         INTEGER PRIMARY KEY AUTOINCREMENT,
			a          INTEGER NOT NULL,
			b          INTEGER,
			invite     TEXT UNIQUE,
			status     TEXT NOT NULL,
			turn       INTEGER,
			streak     INTEGER NOT NULL DEFAULT 0,
			best       INTEGER NOT NULL DEFAULT 0,
			coins      INTEGER NOT NULL DEFAULT 0,
			choices    TEXT,
			reason     TEXT,
			created_at INTEGER NOT NULL,
			updated_at INTEGER NOT NULL
		)''')
		c.execute('CREATE INDEX IF NOT EXISTS games_a ON games(a, status)')
		c.execute('CREATE INDEX IF NOT EXISTS games_b ON games(b, status)')
		c.execute('''CREATE TABLE IF NOT EXISTS rounds (
			id         INTEGER PRIMARY KEY AUTOINCREMENT,
			game_id    INTEGER NOT NULL,
			drawer     INTEGER NOT NULL,
			guesser    INTEGER,
			word_id    INTEGER NOT NULL,
			word       TEXT NOT NULL,
			tier       INTEGER NOT NULL,
			ops        TEXT NOT NULL,
			tiles      TEXT NOT NULL,
			hints      TEXT NOT NULL DEFAULT '{}',
			result     TEXT NOT NULL DEFAULT 'pending',
			attempts   INTEGER NOT NULL DEFAULT 0,
			misses     TEXT NOT NULL DEFAULT '[]',
			created_at INTEGER NOT NULL,
			solved_at  INTEGER
		)''')
		c.execute('CREATE INDEX IF NOT EXISTS rounds_game ON rounds(game_id, id)')
		if 'misses' not in {r[1] for r in c.execute('PRAGMA table_info(rounds)')}: # databases made before wrong guesses were kept
			c.execute('ALTER TABLE rounds ADD COLUMN misses TEXT NOT NULL DEFAULT \'[]\'')
		c.execute('''CREATE TABLE IF NOT EXISTS game_chat (
			id      INTEGER PRIMARY KEY AUTOINCREMENT,
			game_id INTEGER NOT NULL,
			user_id INTEGER NOT NULL,
			text    TEXT NOT NULL,
			at      INTEGER NOT NULL
		)''')
		c.execute('CREATE INDEX IF NOT EXISTS game_chat_game ON game_chat(game_id, id)')


def leaderboard(board: str) -> list:
	'''Return the top players on a board as (user_id, name, value, extra) rows.'''

	sql = {
		'coins'  : 'SELECT id AS user_id, username AS name, coins_earned AS value, NULL AS extra FROM users WHERE coins_earned > 0 ORDER BY coins_earned DESC, id LIMIT ?',
		'streak' : 'SELECT id AS user_id, username AS name, best_streak AS value, NULL AS extra FROM users WHERE best_streak > 0 ORDER BY best_streak DESC, id LIMIT ?',
		'live'   : 'SELECT id AS user_id, username AS name, live_wins AS value, live_games AS extra FROM users WHERE live_wins > 0 ORDER BY live_wins DESC, live_games, id LIMIT ?',
		'drawer' : f'SELECT id AS user_id, username AS name, ROUND(100.0 * drawn_guessed / drawn, 1) AS value, drawn AS extra FROM users WHERE drawn >= {DRAWER_MIN} ORDER BY 1.0 * drawn_guessed / drawn DESC, drawn DESC, id LIMIT ?'
	}[board]
	with db_connect() as c:
		return [dict(r) for r in c.execute(sql, (LEADERBOARD_N,)).fetchall()]


def packs_of(c: sqlite3.Connection, user_id: int) -> list:
	'''Return the color packs a player owns.'''

	if not user_id:
		return []
	row = c.execute('SELECT packs FROM users WHERE id=?', (user_id,)).fetchone()
	return [p for p in row['packs'].split(',') if p] if row else []


def profile(user_id: int, admin: bool = False) -> dict:
	'''Return a signed in player's coins, packs and stats.'''

	with db_connect() as c:
		row = c.execute('SELECT username, coins, coins_earned, best_streak, drawn, drawn_guessed, guessed, live_games, live_wins, packs FROM users WHERE id=?', (user_id,)).fetchone()
	if not row:
		return None
	out          = dict(row)
	out['id']    = user_id
	out['packs'] = [p for p in row['packs'].split(',') if p]
	out['admin'] = admin
	return out


def prune_sessions():
	'''Delete expired sessions.'''

	with DB_LOCK, db_connect() as c:
		c.execute('DELETE FROM sessions WHERE expires_at < ?', (int(time.time()),))


def signup_allowed(c: sqlite3.Connection, ip: str) -> bool:
	'''Return True if no account was made from this address in the last day.'''

	return not c.execute('SELECT 1 FROM users WHERE signup_ip=? AND created_at > ?', (ip, int(time.time()) - SIGNUP_EVERY)).fetchone()


def spend(c: sqlite3.Connection, user_id: int, amount: int) -> bool:
	'''Take coins from a player. Returns False when they can not afford it.'''

	return c.execute('UPDATE users SET coins = coins - ? WHERE id=? AND coins >= ?', (amount, user_id, amount)).rowcount == 1


def stat(c: sqlite3.Connection, user_id: int, **counts):
	'''Add to a player's stat counters.'''

	if user_id and counts:
		c.execute(f'UPDATE users SET {", ".join(f"{k} = {k} + ?" for k in counts)} WHERE id=?', (*counts.values(), user_id))


def username_of(c: sqlite3.Connection, user_id: int) -> str:
	'''Return a player's name.'''

	row = c.execute('SELECT username FROM users WHERE id=?', (user_id,)).fetchone() if user_id else None
	return row['username'] if row else None
