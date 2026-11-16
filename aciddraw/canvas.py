#!/usr/bin/env python3
# acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
# aciddraw/canvas.py

SIZE       = 800                  # drawings are recorded on an 800x800 board
SIZES      = (3, 8, 16, 32)       # brush widths
KINDS      = {'p': 0, 'l': 4, 'r': 4, 'o': 4, 'f': 2, 'u': 0, 'x': 0} # op kind -> exact number of coordinates (0 for free strokes and no-coordinate ops)
OPS_MAX    = 4000                 # ops in one drawing
POINTS_MAX = 80_000               # stroke points in one drawing
STROKE_MAX = 3000                 # points in one stroke
TIME_MAX   = 3_600_000            # ms, the latest an op may start
PAY_INK    = 30                   # ink a drawing needs before a guess of it pays coins (see ink)
BASE       = ['#111111', '#ffffff', '#8a8a8a', '#e53935', '#fb8c00', '#fdd835', '#43a047', '#1e88e5', '#8e24aa', '#6d4c41']
PACKS      = {
	'pastel' : ('Pastel',    100, ['#ffb3c7', '#ffd8a8', '#fff3a8', '#c3f0c8', '#b3e5fc', '#d7c4f2']),
	'earth'  : ('Earth',     100, ['#3e2723', '#8d6e63', '#c2a878', '#556b2f', '#a0522d', '#d2b48c']),
	'skin'   : ('Skin tones', 150, ['#ffe0c4', '#f1c27d', '#e0ac69', '#c68642', '#8d5524', '#5c3a1e']),
	'neon'   : ('Neon',      150, ['#39ff14', '#ff2bd6', '#00f0ff', '#fff01f', '#ff5f1f', '#bc13fe']),
	'ocean'  : ('Ocean',     150, ['#003f5c', '#006d77', '#2ec4b6', '#83c5be', '#90e0ef', '#0077b6']),
	'royal'  : ('Royal',     250, ['#d4af37', '#c0c0c0', '#b87333', '#4b0082', '#800020', '#0b6623'])
}


def colors_for(packs: list) -> set:
	'''Return every color a player can draw with.'''

	allowed = set(BASE)
	for p in packs:
		if p in PACKS:
			allowed.update(PACKS[p][2])
	return allowed


def ink(ops: list) -> int:
	'''Measure how much was drawn: one per free stroke point and 10 per line, box or circle. Fills, undos and clears add nothing.'''

	return sum(len(op['p']) // 3 if op['k'] == 'p' else 10 if op['k'] in 'lro' else 0 for op in ops)


def validate(ops, colors: set, have_ops: int = 0, have_points: int = 0) -> tuple:
	'''Check drawing ops and return (clean ops, points used, error).

	:param ops: list of ops from the browser
	:param colors: colors the drawer may use
	:param have_ops: ops already in the drawing (live rooms send a drawing in pieces)
	:param have_points: stroke points already in the drawing
	'''

	if not isinstance(ops, list) or not ops:
		return None, 0, 'nothing drawn'
	if have_ops + len(ops) > OPS_MAX:
		return None, 0, 'that drawing is too big'
	clean  = []
	points = 0
	for op in ops:
		if not isinstance(op, dict) or op.get('k') not in KINDS:
			return None, 0, 'bad drawing data'
		k = op['k']
		t = op.get('t')
		if not isinstance(t, int) or not 0 <= t <= TIME_MAX:
			return None, 0, 'bad drawing data'
		out = {'k': k, 't': t}
		if k in 'ux':
			clean.append(out)
			continue
		if op.get('c') not in colors:
			return None, 0, 'you do not own that color'
		out['c'] = op['c']
		if k != 'f':
			if op.get('s') not in SIZES:
				return None, 0, 'bad brush size'
			out['s'] = op['s']
		p = op.get('p')
		if not isinstance(p, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in p):
			return None, 0, 'bad drawing data'
		if k == 'p':
			if not 3 <= len(p) <= STROKE_MAX * 3 or len(p) % 3:
				return None, 0, 'bad drawing data'
			p = [min(max(v, -50), SIZE + 50) if i % 3 < 2 else min(max(v, 0), 60_000) for i, v in enumerate(p)]
			points += len(p) // 3
		elif len(p) != KINDS[k]:
			return None, 0, 'bad drawing data'
		else:
			p = [min(max(v, -50), SIZE + 50) for v in p]
		out['p'] = p
		clean.append(out)
	if have_points + points > POINTS_MAX:
		return None, 0, 'that drawing is too big'
	return clean, points, None
