#!/usr/bin/env python3
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# aciddraw/hub.py

import queue
import secrets
import threading
import time


LOCK         = threading.RLock() # guards every live room; always taken before the database lock
SUBS         = {}                # event stream queue -> {'user': id, 'room': code, 'game': id}
ROOMS        = {}                # room code -> live room
CODE_CHARS   = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
EXPIRE_EVERY = 60                # seconds between sweeps for async games nobody has moved in


def loop(every):
	'''Background thread: run every live room's clock and the once a minute job.

	:param every: called once a minute, returning the user ids whose games it changed
	'''

	last = 0
	while True:
		time.sleep(0.25)
		now = time.time()
		if now - last >= EXPIRE_EVERY:
			last = now
			try:
				users = every()
				if users:
					notify(users=users)
			except Exception as e:
				print(f'sweep error: {e!r}')
		with LOCK:
			for room in list(ROOMS.values()):
				try:
					changed = room.tick()
				except Exception as e:
					print(f'room {room.code} error: {e!r}')
					changed = False
				if room.gone():
					del ROOMS[room.code]
					changed = True
				if changed:
					notify(room=room.code)


def new_code() -> str:
	'''Return an unused four character room code.'''

	while True:
		code = ''.join(secrets.choice(CODE_CHARS) for _ in range(4))
		if code not in ROOMS:
			return code


def notify(users: set = None, room: str = None):
	'''Wake event streams. With nothing given every stream wakes.

	:param users: only wake streams of these signed in players
	:param room: only wake streams watching this live room
	'''

	for q, info in list(SUBS.items()):
		if users is not None and info['user'] not in users:
			continue
		if room is not None and info['room'] != room:
			continue
		try:
			q.put_nowait('room' if room is not None else 'all')
		except queue.Full:
			pass


def start(every):
	'''Start the room clock.

	:param every: job to run once a minute
	'''

	threading.Thread(target=loop, args=(every,), daemon=True).start()
