// acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
// utils.js

const $         = (id) => document.getElementById(id);
const AUTH_KEY  = 'aciddraw_auth';
const NAME_KEY  = 'aciddraw_guest';
const TIER_NAME = { 1: 'Easy', 2: 'Medium', 3: 'Hard' };
const TOOLS     = [['pen', 'i-pen', 'Pen'], ['eraser', 'i-eraser', 'Eraser'], ['fill', 'i-fill', 'Fill'], ['line', 'i-line', 'Line'], ['rect', 'i-rect', 'Box'], ['oval', 'i-oval', 'Circle']];
const SHAPES    = { line: 'l', rect: 'r', oval: 'o' };
const FILL_TOL  = 90;   // how different a pixel may be from the clicked one and still get filled
const GAP_MAX   = 700;  // ms, longest pause kept when replaying a drawing
const STEP_MAX  = 120;  // ms, longest pause kept between two points of a stroke on replay
const SPLIT_MS  = 120;  // ms, how often a live stroke is cut into a piece and sent

let token     = localStorage.getItem(AUTH_KEY) || '';
let me        = null;   // the signed in player
let cfg       = null;   // rules from the server
let games     = [];
let rooms     = [];
let route     = { view: 'games' };
let offset    = 0;      // server clock minus ours, in ms
let source    = null;
let sourceKey = '';
let authMode  = 'login';
let captchaId = '';
let board     = 'coins';
let adminPage = 0;
let gamesFor  = null;   // token the games list was last loaded for, so the first load does not ring
let ssoTried  = false;  // the shared arcade sign-in is tried once at page load...
let ssoRetry  = false;  // ...and once more when a token stops working, never in a loop
let challenge = null;   // ?challenge=<username> from an arcade profile, started once signed in

// The async game on screen
const G = { data: null, id: null, ui: null, chat: [], unread: 0, chatOpen: false };

// The live room on screen
const R = { data: null, code: null, pid: null, board: null, recorder: null, outbox: [], sending: false, chat: [], mode: null, chatLoaded: false, chatOpen: false, unread: 0, tick: null };


// ---------- helpers ----------

function esc(s) {
	return String(s).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
}

function now() {
	return (Date.now() + offset) / 1000;
}

function dur(sec) {
	sec = Math.max(0, Math.round(sec));
	const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
	if (d) return `${d}d ${h}h`;
	if (h) return `${h}h ${m}m`;
	return `${m}m`;
}

function ago(at) {
	const s = now() - at;
	if (s < 60) return 'just now';
	return dur(s) + ' ago';
}

async function api(path, body) {
	const opts = { method: body ? 'POST' : 'GET', headers: { 'X-Auth-Token': token } };
	if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
	let res, data;
	try {
		res  = await fetch(path, opts);
		data = await res.json();
	} catch (e) {
		toast('Connection problem, try again');
		throw e;
	}
	if (res.status === 401 && path !== '/auth/login') signedOut(true);
	if (data && data.error) { toast(data.error); throw new Error(data.error); }
	return data;
}

let toastTimer = null;
function toast(text) {
	const el = $('toast');
	el.textContent = text;
	el.classList.remove('hidden');
	clearTimeout(toastTimer);
	toastTimer = setTimeout(() => el.classList.add('hidden'), 2600);
}

function modal(html) {
	$('modal').innerHTML = html;
	$('modal-bg').classList.remove('hidden');
}

function closeModal() {
	$('modal-bg').classList.add('hidden');
	$('modal').innerHTML = '';
}

function tierTag(tier) {
	return `<span class="tier t${tier}">${TIER_NAME[tier]} · ${tier}<span class="coin sm"></span></span>`;
}

function copy(text, what) {
	if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => toast(`${what} copied`), () => toast(text));
	else toast(text);
}

function confirmTap(btn, label, fn) {
	// Two taps to do something you can not take back
	if (btn.dataset.armed) return fn();
	btn.dataset.armed = '1';
	const old = btn.innerHTML;
	btn.textContent = label;
	btn.classList.add('danger');
	setTimeout(() => { delete btn.dataset.armed; btn.innerHTML = old; btn.classList.remove('danger'); }, 3000);
}


// ---------- sounds and notifications ----------

const SOUND_KEY = 'aciddraw_sound';

// Every sound is made on the fly: a list of [frequency, start, length, wave, volume] notes
const SOUNDS = {
	correct: [[523, 0, 0.12, 'triangle', 0.3], [659, 0.1, 0.12, 'triangle', 0.3], [784, 0.2, 0.12, 'triangle', 0.3], [1047, 0.3, 0.35, 'triangle', 0.3]],
	other:   [[784, 0, 0.1, 'triangle', 0.22], [1047, 0.09, 0.2, 'triangle', 0.22]],
	wrong:   [[220, 0, 0.12, 'square', 0.1], [160, 0.11, 0.22, 'square', 0.1]],
	close:   [[660, 0, 0.08, 'sine', 0.22], [740, 0.09, 0.14, 'sine', 0.22]],
	pop:     [[880, 0, 0.06, 'sine', 0.12]],
	tap:     [[1300, 0, 0.03, 'sine', 0.08]],
	tick:    [[1000, 0, 0.05, 'square', 0.07]],
	turn:    [[587, 0, 0.12, 'sine', 0.3], [880, 0.13, 0.26, 'sine', 0.3]],
	start:   [[392, 0, 0.1, 'triangle', 0.25], [523, 0.1, 0.1, 'triangle', 0.25], [659, 0.2, 0.1, 'triangle', 0.25], [784, 0.3, 0.3, 'triangle', 0.25]],
	reveal:  [[440, 0, 0.16, 'sine', 0.2], [349, 0.16, 0.32, 'sine', 0.2]],
	win:     [[523, 0, 0.12, 'triangle', 0.28], [659, 0.12, 0.12, 'triangle', 0.28], [784, 0.24, 0.12, 'triangle', 0.28], [1047, 0.36, 0.14, 'triangle', 0.28], [784, 0.5, 0.1, 'triangle', 0.28], [1047, 0.6, 0.45, 'triangle', 0.28]],
	send:    [[600, 0, 0.07, 'sine', 0.15], [900, 0.06, 0.12, 'sine', 0.15]],
};

let audio   = null;
let soundOn = localStorage.getItem(SOUND_KEY) !== '0';
let swReg   = null;

function sfx(name) {
	if (!soundOn || !audio || audio.state !== 'running') return;
	const t0 = audio.currentTime + 0.01;
	for (const [freq, at, len, wave, vol] of SOUNDS[name]) {
		const osc = audio.createOscillator(), gain = audio.createGain();
		osc.type = wave;
		osc.frequency.value = freq;
		gain.gain.setValueAtTime(0.0001, t0 + at);
		gain.gain.exponentialRampToValueAtTime(vol, t0 + at + 0.01);
		gain.gain.exponentialRampToValueAtTime(0.0001, t0 + at + len);
		osc.connect(gain).connect(audio.destination);
		osc.start(t0 + at);
		osc.stop(t0 + at + len + 0.02);
	}
}

function unlockAudio() {
	// Browsers only let a page make sound once the player has touched it
	if (!audio) {
		try { audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return; }
	}
	if (audio.state === 'suspended') audio.resume();
}

function setSound(on) {
	soundOn = on;
	localStorage.setItem(SOUND_KEY, on ? '1' : '0');
	document.querySelectorAll('.sound-btn').forEach((b) => { b.innerHTML = `<svg><use href="#i-${on ? 'sound' : 'mute'}"/></svg>`; b.title = on ? 'Sound on' : 'Sound off'; });
	if (on) { unlockAudio(); sfx('pop'); }
}

function canNotify() {
	return 'Notification' in window;
}

function askNotify() {
	if (canNotify() && Notification.permission === 'default') Notification.requestPermission().then(updateNotifyBtn);
}

function updateNotifyBtn() {
	$('btn-notify').classList.toggle('hidden', !canNotify() || Notification.permission !== 'default');
}

function notify(body, hash) {
	// Only while the page is in the background, otherwise the sound and the screen say it already
	if (!document.hidden || !canNotify() || Notification.permission !== 'granted') return;
	const opts = { body, tag: hash, icon: '/static/icon-192.png', badge: '/static/icon-192.png', data: { hash } };
	if (swReg) return swReg.showNotification('acid draw', opts).catch(() => {});
	try {
		const n = new Notification('acid draw', opts);
		n.onclick = () => { window.focus(); location.hash = hash; n.close(); };
	} catch (e) {}
}

function viewportVars() {
	// The visible height and how much the keyboard covers, so phones can fit the board above it
	const v = window.visualViewport, root = document.documentElement.style;
	root.setProperty('--vvh', (v ? v.height : innerHeight) + 'px');
	root.setProperty('--kb', (v ? Math.max(0, innerHeight - v.height - v.offsetTop) : 0) + 'px');
}


// ---------- canvas ----------

function hexRgb(hex) {
	return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}

function floodFill(ctx, x0, y0, hex) {
	const W = ctx.canvas.width, H = ctx.canvas.height;
	x0 = Math.round(x0); y0 = Math.round(y0);
	if (x0 < 0 || y0 < 0 || x0 >= W || y0 >= H) return;
	const img = ctx.getImageData(0, 0, W, H), d = img.data;
	const [r, g, b] = hexRgb(hex);
	const s = (y0 * W + x0) * 4, tr = d[s], tg = d[s + 1], tb = d[s + 2];
	if (Math.abs(tr - r) + Math.abs(tg - g) + Math.abs(tb - b) < 6) return;
	const mask  = new Uint8Array(W * H);
	const match = (p) => !mask[p] && Math.abs(d[p * 4] - tr) + Math.abs(d[p * 4 + 1] - tg) + Math.abs(d[p * 4 + 2] - tb) <= FILL_TOL;
	const stack = [x0, y0];
	while (stack.length) {
		const y = stack.pop(), x = stack.pop();
		let lx = x;
		while (lx > 0 && match(y * W + lx - 1)) lx--;
		let up = false, down = false;
		for (let cx = lx; cx < W && match(y * W + cx); cx++) {
			mask[y * W + cx] = 1;
			if (y > 0) { const m = match((y - 1) * W + cx); if (m && !up) stack.push(cx, y - 1); up = m; }
			if (y < H - 1) { const m = match((y + 1) * W + cx); if (m && !down) stack.push(cx, y + 1); down = m; }
		}
	}
	// Grow the fill by a pixel so it tucks under the soft edges of the lines around it
	for (let p = 0; p < W * H; p++) {
		if (mask[p] !== 1) continue;
		const x = p % W;
		for (const q of [p - W, p + W, x > 0 ? p - 1 : -1, x < W - 1 ? p + 1 : -1]) if (q >= 0 && q < W * H && !mask[q]) mask[q] = 2;
	}
	for (let p = 0; p < W * H; p++) {
		if (!mask[p]) continue;
		d[p * 4] = r; d[p * 4 + 1] = g; d[p * 4 + 2] = b; d[p * 4 + 3] = 255;
	}
	ctx.putImageData(img, 0, 0);
}

function strokeSeg(ctx, op, i) {
	// Draw point i of a stroke, joined to the point before it
	const p = op.p;
	ctx.strokeStyle = ctx.fillStyle = op.c;
	ctx.lineWidth   = op.s;
	ctx.lineCap     = ctx.lineJoin = 'round';
	if (i === 0) {
		ctx.beginPath();
		ctx.arc(p[0], p[1], op.s / 2, 0, Math.PI * 2);
		ctx.fill();
		return;
	}
	ctx.beginPath();
	ctx.moveTo(p[(i - 1) * 3], p[(i - 1) * 3 + 1]);
	ctx.lineTo(p[i * 3], p[i * 3 + 1]);
	ctx.stroke();
}

function shape(ctx, k, c, s, p) {
	ctx.strokeStyle = c;
	ctx.lineWidth   = s;
	ctx.lineCap     = ctx.lineJoin = 'round';
	ctx.beginPath();
	if (k === 'l') { ctx.moveTo(p[0], p[1]); ctx.lineTo(p[2], p[3]); }
	else if (k === 'r') ctx.rect(Math.min(p[0], p[2]), Math.min(p[1], p[3]), Math.abs(p[2] - p[0]), Math.abs(p[3] - p[1]));
	else ctx.ellipse((p[0] + p[2]) / 2, (p[1] + p[3]) / 2, Math.abs(p[2] - p[0]) / 2 || 0.5, Math.abs(p[3] - p[1]) / 2 || 0.5, 0, 0, Math.PI * 2);
	ctx.stroke();
}

function renderOp(ctx, op) {
	if (op.k === 'p') {
		const n = op.p.length / 3;
		strokeSeg(ctx, op, 0);
		if (n > 1) {
			ctx.beginPath();
			ctx.moveTo(op.p[0], op.p[1]);
			for (let i = 1; i < n; i++) ctx.lineTo(op.p[i * 3], op.p[i * 3 + 1]);
			ctx.stroke();
		}
	} else if (op.k in { l: 1, r: 1, o: 1 }) shape(ctx, op.k, op.c, op.s, op.p);
	else if (op.k === 'f') floodFill(ctx, op.p[0], op.p[1], op.c);
	else if (op.k === 'x') { ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height); }
}

class Board {
	// A drawing surface that keeps every op, so undo can redraw what is left
	constructor(wrap) {
		const size   = cfg ? cfg.size : 800;
		this.el      = document.createElement('div');
		this.el.className = 'board';
		this.canvas  = document.createElement('canvas');
		this.over    = document.createElement('canvas');
		this.canvas.width = this.canvas.height = this.over.width = this.over.height = size;
		this.over.className = 'over';
		this.el.append(this.canvas, this.over);
		this.ctx     = this.canvas.getContext('2d', { willReadFrequently: true });
		this.octx    = this.over.getContext('2d');
		this.turn    = null;
		wrap.replaceChildren(this.el);
		this.reset();
	}

	reset() {
		this.ops     = [];
		this.visible = [];
		this.blank();
	}

	blank() {
		this.ctx.fillStyle = '#ffffff';
		this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
	}

	apply(op, drawn) {
		// Add an op. drawn means it is already on the canvas (a stroke drawn point by point)
		this.ops.push(op);
		if (op.k === 'u') {
			if (this.visible.length) { this.visible.pop(); this.redraw(); }
			return;
		}
		this.visible.push(op);
		if (!drawn) renderOp(this.ctx, op);
	}

	load(ops) {
		this.ops = [];
		this.visible = [];
		for (const op of ops) {
			this.ops.push(op);
			if (op.k === 'u') this.visible.pop();
			else this.visible.push(op);
		}
		this.redraw();
	}

	redraw() {
		this.blank();
		for (const op of this.visible) renderOp(this.ctx, op);
	}

	save(name) {
		this.canvas.toBlob((blob) => {
			const file = new File([blob], `acid-draw-${(name || 'drawing').replace(/\s+/g, '-')}.png`, { type: 'image/png' });
			if (navigator.canShare && navigator.canShare({ files: [file] }) && matchMedia('(pointer: coarse)').matches) {
				navigator.share({ files: [file], title: 'acid draw' }).catch(() => {});
				return;
			}
			const a = document.createElement('a');
			a.href = URL.createObjectURL(blob);
			a.download = file.name;
			a.click();
			setTimeout(() => URL.revokeObjectURL(a.href), 5000);
		}, 'image/png');
	}
}

class Replay {
	// Plays a recorded drawing back stroke by stroke, with long pauses cut short
	constructor(target, ops, onDone) {
		this.board  = target;
		this.ops    = ops;
		this.onDone = onDone;
		this.speed  = 1;
		this.clock  = 0;
		this.index  = 0;    // next op
		this.point  = 0;    // next point of the op being drawn
		this.items  = [];
		this.done   = false;
		let clock = 0, rawEnd = 0;
		for (const op of ops) {
			clock += Math.min(Math.max(op.t - rawEnd, 0), GAP_MAX);
			const item = { op, start: clock, times: null };
			rawEnd = op.t;
			if (op.k === 'p') {
				item.times = [];
				let t = clock;
				for (let i = 0; i < op.p.length / 3; i++) {
					const dt = i ? op.p[i * 3 + 2] : 0;
					t += Math.min(dt, STEP_MAX);
					rawEnd += dt;
					item.times.push(t);
				}
				clock = t;
			}
			this.items.push(item);
		}
		this.length = clock;
		this.board.reset();
		this.last = performance.now();
		this.raf  = requestAnimationFrame((ts) => this.frame(ts));
	}

	frame(ts) {
		if (this.done) return;
		this.clock += (ts - this.last) * this.speed;
		this.last   = ts;
		while (this.index < this.items.length) {
			const item = this.items[this.index];
			if (item.start > this.clock) break;
			if (item.times) {
				while (this.point < item.times.length && item.times[this.point] <= this.clock) strokeSeg(this.board.ctx, item.op, this.point++);
				if (this.point < item.times.length) break;
				this.board.apply(item.op, true);
			} else this.board.apply(item.op);
			this.index++;
			this.point = 0;
		}
		if (this.index >= this.items.length) return this.finish();
		this.raf = requestAnimationFrame((t) => this.frame(t));
	}

	skip() {
		if (this.done) return;
		this.board.load(this.ops);
		this.finish();
	}

	finish() {
		this.done = true;
		cancelAnimationFrame(this.raf);
		if (this.onDone) this.onDone();
	}

	stop() {
		this.done = true;
		cancelAnimationFrame(this.raf);
	}
}

class Recorder {
	// Turns pointer input into drawing ops
	constructor(target, opts) {
		this.board  = target;
		this.onOp   = opts.onOp || (() => {});
		this.live   = !!opts.live;
		this.tool   = 'pen';
		this.color  = '#111111';
		this.size   = 8;
		this.t0     = null;
		this.cur    = null;
		this.start  = null;
		this.on     = true;
		this.packs  = opts.packs || [];
		const el    = target.over;
		el.style.touchAction = 'none';
		el.addEventListener('pointerdown', (e) => this.down(e));
		el.addEventListener('pointermove', (e) => this.move(e));
		el.addEventListener('pointerup', (e) => this.up(e));
		el.addEventListener('pointercancel', (e) => this.up(e));
		target.el.classList.add('drawable');
	}

	stamp() {
		if (this.t0 === null) this.t0 = performance.now();
		return Math.round(performance.now() - this.t0);
	}

	resume(lastT) {
		// Carry on the clock of a drawing restored from a draft
		this.t0 = performance.now() - lastT - 1000;
	}

	pos(e) {
		const r = this.board.over.getBoundingClientRect(), size = this.board.over.width;
		return [Math.round((e.clientX - r.left) * size / r.width), Math.round((e.clientY - r.top) * size / r.height)];
	}

	down(e) {
		if (!this.on || (e.pointerType === 'mouse' && e.button !== 0)) return;
		e.preventDefault();
		this.board.over.setPointerCapture(e.pointerId);
		const [x, y] = this.pos(e);
		if (this.tool === 'fill') return this.commit({ k: 'f', c: this.color, t: this.stamp(), p: [x, y] });
		if (SHAPES[this.tool]) { this.start = [x, y]; return; }
		this.cur  = { k: 'p', c: this.tool === 'eraser' ? '#ffffff' : this.color, s: this.size, t: this.stamp(), p: [x, y, 0] };
		this.lastT = this.cut = performance.now();
		strokeSeg(this.board.ctx, this.cur, 0);
	}

	move(e) {
		if (!this.on) return;
		const [x, y] = this.pos(e);
		if (this.start) {
			this.lastPos = [x, y];
			const o = this.board.octx;
			o.clearRect(0, 0, this.board.over.width, this.board.over.height);
			shape(o, SHAPES[this.tool], this.color, this.size, [...this.start, x, y]);
			return;
		}
		if (!this.cur) return;
		const p = this.cur.p, n = p.length;
		if (Math.hypot(x - p[n - 3], y - p[n - 2]) < 2.5) return;
		const t = performance.now();
		p.push(x, y, Math.min(60000, Math.round(t - this.lastT)));
		this.lastT = t;
		strokeSeg(this.board.ctx, this.cur, p.length / 3 - 1);
		if (p.length >= 9000 || (this.live && t - this.cut > SPLIT_MS)) {
			// Send the stroke so far and carry on from its last point
			this.commit(this.cur, true);
			this.cur = { k: 'p', c: this.cur.c, s: this.cur.s, t: this.stamp(), p: [x, y, 0] };
			this.cut = t;
		}
	}

	up() {
		if (this.start) {
			// Shapes end where the pointer was last seen
			const end = this.lastPos || this.start;
			this.board.octx.clearRect(0, 0, this.board.over.width, this.board.over.height);
			if (end[0] !== this.start[0] || end[1] !== this.start[1]) this.commit({ k: SHAPES[this.tool], c: this.color, s: this.size, t: this.stamp(), p: [...this.start, ...end] });
			this.start   = null;
			this.lastPos = null;
		}
		if (this.cur) { this.commit(this.cur, true); this.cur = null; }
	}

	commit(op, drawn) {
		this.board.apply(op, drawn);
		this.onOp(op);
	}

	undo() {
		if (this.on && this.board.visible.length) this.commit({ k: 'u', t: this.stamp() });
	}

	clear() {
		if (this.on && this.board.visible.length && this.board.visible[this.board.visible.length - 1].k !== 'x') this.commit({ k: 'x', t: this.stamp() });
	}

	toolbar(el) {
		const colors = [...cfg.base];
		for (const p of this.packs) if (cfg.packs[p]) colors.push(...cfg.packs[p].colors);
		const locked = Object.keys(cfg.packs).filter((k) => !this.packs.includes(k));
		el.innerHTML = `<div class="tools">
			<div class="tool-row">
				${TOOLS.map(([k, icon, label]) => `<button class="tool${k === this.tool ? ' on' : ''}" data-tool="${k}" title="${label}"><svg><use href="#${icon}"/></svg></button>`).join('')}
				<span class="sep"></span>
				${cfg.sizes.map((s) => `<button class="size${s === this.size ? ' on' : ''}" data-size="${s}" title="Brush ${s}"><i style="width:${Math.max(4, s / 1.4)}px;height:${Math.max(4, s / 1.4)}px"></i></button>`).join('')}
				<span class="sep"></span>
				<button class="tool" data-do="undo" title="Undo"><svg><use href="#i-undo"/></svg></button>
				<button class="tool" data-do="clear" title="Clear"><svg><use href="#i-trash"/></svg></button>
			</div>
			<div class="swatches">
				${colors.map((c) => `<button class="sw${c === this.color ? ' on' : ''}" data-color="${c}" style="background:${c}" title="${c}"></button>`).join('')}
				${locked.length && me ? `<button class="sw more" data-do="shop" title="More colors">+</button>` : ''}
			</div>
		</div>`;
		el.querySelector('.tools').addEventListener('click', (e) => {
			const b = e.target.closest('button');
			if (!b) return;
			if (b.dataset.tool) { this.tool = b.dataset.tool; el.querySelectorAll('[data-tool]').forEach((x) => x.classList.toggle('on', x === b)); }
			if (b.dataset.size) { this.size = +b.dataset.size; el.querySelectorAll('[data-size]').forEach((x) => x.classList.toggle('on', x === b)); }
			if (b.dataset.color) {
				this.color = b.dataset.color;
				if (this.tool === 'eraser') { this.tool = 'pen'; el.querySelectorAll('[data-tool]').forEach((x) => x.classList.toggle('on', x.dataset.tool === 'pen')); }
				el.querySelectorAll('[data-color]').forEach((x) => x.classList.toggle('on', x === b));
			}
			if (b.dataset.do === 'undo') this.undo();
			if (b.dataset.do === 'clear') this.clear();
			if (b.dataset.do === 'shop') openAccount('shop');
		});
	}
}

// ---------- account ----------

function signedOut(expired) {
	token = '';
	me = null;
	localStorage.removeItem(AUTH_KEY);
	if (expired && !ssoRetry && cfg && cfg.arcade_public_url) {
		ssoRetry = true;
		return trySso().then(async (ok) => {
			if (ok) { await loadMe(); connect(true); onRoute(); }
			else { updateUser(); connect(true); }
		});
	}
	updateUser();
	connect(true);
}

async function trySso() {
	// Signed in on another site of this domain? Swap the shared cookie for a session here, staying a guest quietly if not
	try {
		const res = await fetch('/auth/sso');
		if (!res.ok) return false;
		token = (await res.json()).token;
		localStorage.setItem(AUTH_KEY, token);
		return true;
	} catch (e) {
		return false;
	}
}

function updateUser() {
	document.body.classList.toggle('signed-in', !!me);
	$('btn-coins').classList.toggle('hidden', !me);
	if (me) $('coins').textContent = me.coins.toLocaleString();
	const turns = me ? me.turns : 0;
	$('turn-badge').textContent = turns;
	$('turn-badge').classList.toggle('hidden', !turns);
	document.title = (turns ? `(${turns}) ` : '') + 'acid draw';
	$('admin').classList.toggle('hidden', !(me && me.admin));
}

async function newCaptcha() {
	const c = await api('/auth/captcha');
	captchaId = c.captcha_id;
	const el = $('captcha-img');
	if (el) el.innerHTML = c.svg;
}

function openAuth(mode) {
	authMode = mode;
	const signup = mode === 'signup';
	modal(`<h2>${signup ? 'Make an account' : 'Sign in'}</h2>
		<form id="auth-form" class="stack">
			<input id="auth-user" placeholder="Username" maxlength="16" autocomplete="username" required>
			<input id="auth-pass" type="password" placeholder="Password" maxlength="128" autocomplete="${signup ? 'new-password' : 'current-password'}" required>
			${signup ? `<div class="captcha" id="captcha-img" title="New captcha"></div><input id="auth-captcha" placeholder="Letters above" maxlength="5" autocomplete="off" required>` : ''}
			<button class="btn primary" type="submit">${signup ? `Sign up and get ${cfg.start} coins` : 'Sign in'}</button>
			<button class="btn link" type="button" id="auth-switch">${signup ? 'Have an account? Sign in' : 'New here? Make an account'}</button>
		</form>`);
	if (signup) { newCaptcha(); $('captcha-img').onclick = newCaptcha; }
	$('auth-switch').onclick = () => openAuth(signup ? 'login' : 'signup');
	$('auth-form').onsubmit = async (e) => {
		e.preventDefault();
		const body = { username: $('auth-user').value.trim(), password: $('auth-pass').value };
		if (signup) Object.assign(body, { captcha_id: captchaId, captcha: $('auth-captcha').value });
		try {
			const res = await api(signup ? '/auth/signup' : '/auth/login', body);
			token = res.token;
			localStorage.setItem(AUTH_KEY, token);
			closeModal();
			await loadMe();
			connect(true);
			onRoute();
			startChallenge();
		} catch (err) {
			if (signup) newCaptcha();
		}
	};
	$('auth-user').focus();
}

function openAccount(tab) {
	if (!me) return openAuth('login');
	const stats = [['Coins earned', me.coins_earned], ['Best streak', me.best_streak], ['Words guessed', me.guessed], ['Drawings', me.drawn], ['Guessed by others', me.drawn ? Math.round(100 * me.drawn_guessed / me.drawn) + '%' : '-'], ['Live wins', `${me.live_wins} / ${me.live_games}`]];
	const packs = Object.entries(cfg.packs).map(([k, p]) => {
		const owned = me.packs.includes(k);
		return `<div class="pack${owned ? ' owned' : ''}"><div class="pack-sw">${p.colors.map((c) => `<i style="background:${c}"></i>`).join('')}</div><b>${esc(p.name)}</b>${owned ? '<span class="dim">Owned</span>' : `<button class="btn sm" data-buy="${k}">${p.price}<span class="coin sm"></span></button>`}</div>`;
	}).join('');
	modal(`<h2>${pic(me.id, 'sm')}${esc(me.username)}</h2>
		<div class="wallet"><span class="coin lg"></span><b>${me.coins.toLocaleString()}</b> coins</div>
		<div class="tabs" id="acct-tabs"><button data-tab="stats" class="${tab !== 'shop' ? 'on' : ''}">Stats</button><button data-tab="shop" class="${tab === 'shop' ? 'on' : ''}">Color packs</button></div>
		<div id="acct-stats" class="${tab === 'shop' ? 'hidden' : ''}"><div class="stats">${stats.map(([k, v]) => `<div><b>${v}</b><span>${k}</span></div>`).join('')}</div>
			<p class="dim small">Coins come from words guessed: easy 1, medium 2, hard 3, paid to both the drawer and the guesser. Spend them on color packs, hints and bombs.</p>
			${cfg.arcade_public_url ? `<a class="btn" href="${esc(cfg.arcade_public_url)}/account" target="_blank" rel="noopener">Account settings</a> ` : ''}<button class="btn" id="btn-logout">Sign out</button></div>
		<div id="acct-shop" class="${tab === 'shop' ? '' : 'hidden'}"><div class="packs">${packs}</div></div>`);
	$('acct-tabs').onclick = (e) => {
		const t = e.target.dataset.tab;
		if (!t) return;
		$('acct-tabs').querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.tab === t));
		$('acct-stats').classList.toggle('hidden', t !== 'stats');
		$('acct-shop').classList.toggle('hidden', t !== 'shop');
	};
	$('acct-shop').onclick = async (e) => {
		const k = e.target.closest('[data-buy]');
		if (!k) return;
		me = Object.assign(me, await api('/shop/buy', { pack: k.dataset.buy }));
		updateUser();
		toast(`${cfg.packs[k.dataset.buy].name} colors unlocked`);
		openAccount('shop');
		refreshToolbars();
	};
	$('btn-logout').onclick = async () => {
		await api('/auth/logout', {});
		closeModal();
		signedOut();
		onRoute();
	};
}

async function loadMe() {
	const res = await api('/auth/me');
	cfg = res;
	me  = res.user;
	if (!me && token) { token = ''; localStorage.removeItem(AUTH_KEY); }
	if (!me && !ssoTried && cfg.arcade_public_url) {
		ssoTried = true;
		if (await trySso()) return loadMe();
	}
	updateUser();
}

function voteBox(info) {
	// Thumbs up or down for a community word after it was played
	if (!info || info.by || !me) return '';
	return `<div class="vote" data-word="${info.id}"><span>New word, keep it?</span><button class="icon-btn${info.vote === 1 ? ' on' : ''}" data-vote="1" title="Keep it"><svg><use href="#i-up"/></svg></button><button class="icon-btn${info.vote === -1 ? ' on' : ''}" data-vote="-1" title="Drop it"><svg><use href="#i-down"/></svg></button></div>`;
}

document.addEventListener('click', async (e) => {
	const b = e.target.closest('.vote [data-vote]');
	if (!b) return;
	const box = b.closest('.vote');
	await api('/words/vote', { word: +box.dataset.word, vote: +b.dataset.vote });
	box.querySelectorAll('[data-vote]').forEach((x) => x.classList.toggle('on', x === b));
	toast('Thanks for voting');
});


// ---------- live updates ----------

function connect(force) {
	const key = [token, route.view === 'game' ? route.id : '', route.view === 'room' ? R.code : '', route.view === 'room' ? R.pid : ''].join('|');
	if (!force && key === sourceKey && source) return;
	if (source) source.close();
	sourceKey = key;
	const q = new URLSearchParams({ t: token });
	if (route.view === 'game') q.set('game', route.id);
	if (route.view === 'room' && R.pid) { q.set('room', R.code); q.set('pid', R.pid); }
	source = new EventSource('/events?' + q);
	const on = (name, fn) => source.addEventListener(name, (ev) => {
		const msg = JSON.parse(ev.data);
		offset = msg.now * 1000 - Date.now();
		fn(msg.data);
	});
	on('me', (d) => { me = d; updateUser(); });
	on('games', gamesUpdate);
	on('rooms', (d) => { rooms = d; if (route.view === 'live') renderRooms(); });
	on('game', (d) => { if (route.view === 'game' && d && d.id === G.id) { G.data = d; renderGame(); } });
	on('gchat', (d) => { if (route.view === 'game') gameChat(d); });
	on('room', (d) => { if (route.view === 'room') roomUpdate(d); });
	on('rchat', (d) => { if (route.view === 'room') roomChat(d); });
	on('ops', (d) => { if (route.view === 'room') roomOps(d); });
}


// ---------- routing ----------

function onRoute() {
	const parts = location.hash.replace(/^#\/?/, '').split('/');
	const prev  = route;
	if (parts[0] === 'game' && +parts[1]) route = { view: 'game', id: +parts[1] };
	else if (parts[0] === 'room' && parts[1]) route = { view: 'room', code: parts[1].toUpperCase() };
	else if (parts[0] === 'join' && parts[1]) return joinInvite(parts[1]);
	else if (['live', 'ranks', 'words'].includes(parts[0])) route = { view: parts[0] };
	else route = { view: 'games' };
	if (prev.view === 'game' && (route.view !== 'game' || route.id !== prev.id)) leaveGame();
	if (prev.view === 'room' && (route.view !== 'room' || route.code !== prev.code)) leaveRoom(prev.code);
	document.querySelectorAll('.view').forEach((v) => v.classList.add('hidden'));
	$('v-' + route.view).classList.remove('hidden');
	document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('on', a.dataset.nav === route.view || (a.dataset.nav === 'games' && route.view === 'game') || (a.dataset.nav === 'live' && route.view === 'room')));
	document.body.dataset.view = route.view;
	if (route.view === 'games') renderGames();
	if (route.view === 'live') renderRooms();
	if (route.view === 'ranks') loadRanks();
	if (route.view === 'words') loadWords();
	if (route.view === 'game') return openGame(route.id);
	if (route.view === 'room') return openRoom(route.code);
	connect();
}

async function joinInvite(code) {
	if (!me) { location.hash = '#/'; toast('Sign in to take the game'); return openAuth('signup'); }
	try {
		const res = await api('/games/join', { code });
		location.hash = `#/game/${res.id}`;
	} catch (e) {
		location.hash = '#/';
	}
}


// ---------- games list ----------

function gameLine(g) {
	if (g.status === 'over') return `<span class="dim">${esc(g.reason || 'Over')}</span>`;
	if (g.step === 'guess') return '<b class="go">Your turn to guess</b>';
	if (g.step === 'draw') return '<b class="go">Your turn to draw</b>';
	if (!g.opponent) return '<span class="dim">Waiting for an opponent</span>';
	return `<span class="dim">Waiting for ${esc(g.opponent)}</span>`;
}

function gamesUpdate(list) {
	// Ring when a game becomes your turn or someone starts one with you
	const before = new Map(games.map((g) => [g.id, g])), first = gamesFor !== token;
	games    = list;
	gamesFor = token;
	if (route.view === 'games') renderGames();
	if (first) return;
	for (const g of list) {
		const was = before.get(g.id), mine = g.step === 'guess' || g.step === 'draw';
		if (g.status === 'over' || (was && was.step === g.step)) continue;
		let msg = '';
		if (mine && (!was || was.step !== g.step)) msg = g.step === 'guess' ? `${g.opponent} drew you something!` : `Your turn to draw for ${g.opponent}`;
		else if (!was && g.opponent) msg = `${g.opponent} started a game with you`;
		if (!msg) continue;
		const viewing = route.view === 'game' && route.id === g.id;
		if (viewing && g.step !== 'guess') continue; // your own move, not news
		sfx('turn');
		if (!viewing) toast(msg);
		notify(msg, `#/game/${g.id}`);
	}
}

function renderGames() {
	const el = $('game-list');
	if (!me) return;
	if (!games.length) { el.innerHTML = '<p class="empty">No games yet. Start one above!</p>'; return; }
	el.innerHTML = games.map((g) => `<a class="game-card${g.step === 'guess' || g.step === 'draw' ? ' mine' : ''}${g.status === 'over' ? ' over' : ''}" href="#/game/${g.id}">
		${avatar(g.opponent || '?', g.opponent_id)}
		<div class="gc-main"><b>${esc(g.opponent || (g.invite ? 'Invite link' : 'Random opponent'))}</b><div>${gameLine(g)}</div></div>
		<div class="gc-side">${g.streak ? `<span class="streak-pill">🔥 ${g.streak}</span>` : ''}${g.status !== 'over' ? `<span class="dim small">ends in ${dur(g.expires_at - now())}</span>` : ''}</div>
	</a>`).join('');
}

function hue(name) {
	let h = 0;
	for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
	return h;
}

function pic(uid, size) {
	// An account player's acid-arcade-db picture, or nothing for guests and standalone mode
	return cfg && cfg.arcade_public_url && uid ? `<img class="avatar${size ? ' ' + size : ''}" src="${esc(cfg.arcade_public_url)}/avatar/${uid}" alt="">` : '';
}

function avatar(name, uid, size) {
	// The arcade picture when there is one, otherwise the colored first letter
	return pic(uid, size) || `<span class="avatar${size ? ' ' + size : ''}" style="--h:${hue(name)}">${esc(name[0].toUpperCase())}</span>`;
}

function startChallenge() {
	// Start the 1v1 game an arcade challenge link asked for, once
	if (!challenge || !me) return;
	const opponent = challenge;
	challenge = null;
	newGame({ mode: 'user', opponent }).catch(() => {});
}

async function newGame(body) {
	askNotify();
	const res = await api('/games/new', body);
	location.hash = `#/game/${res.id}`;
}


// ---------- one async game ----------

async function openGame(id) {
	if (!me) { location.hash = '#/'; return openAuth('login'); }
	if (G.id !== id) {
		G.id = id; G.data = null; G.ui = null; G.chat = []; G.unread = 0; G.chatLoaded = false;
		$('g-chat-log').innerHTML = '';
		$('g-board-wrap').innerHTML = '';
		$('g-under').innerHTML = '';
		$('g-prompt').innerHTML = '';
	}
	connect();
	try {
		G.data = await api(`/games/${id}`);
		renderGame();
	} catch (e) {
		location.hash = '#/';
	}
}

function leaveGame() {
	if (G.ui && G.ui.replay) G.ui.replay.stop();
	G.id = null; G.data = null; G.ui = null;
}

function gameBoard() {
	if (!G.ui.board) G.ui.board = new Board($('g-board-wrap'));
	return G.ui.board;
}

function renderGame() {
	const g = G.data;
	if (!g) return;
	$('g-opp').innerHTML = pic(g.opponent_id, 'xs') + esc(g.opponent || (g.invite ? 'whoever opens your link' : 'a random player'));
	$('g-streak').innerHTML = `<span class="streak-pill${g.streak ? ' hot' : ''}">🔥 ${g.streak}</span>`;
	renderHistory(g);
	renderBanner(g);
	G.ui = G.ui || { mode: null };
	const ui = G.ui;
	if (ui.mode === 'result') return;
	if (g.step === 'guess') {
		if (ui.mode !== 'guess' || ui.roundId !== g.round.id) startGuess(g);
		else renderTiles(g);
	} else if (g.step === 'draw') {
		if (ui.mode === 'draw') return;
		const key = JSON.stringify(g.choices);
		if (ui.mode !== 'choose' || ui.choicesKey !== key) showChoices(g, key);
	} else {
		const key = g.step + (g.history[0] ? `${g.history[0].id}:${g.history[0].result}` : '') + (g.opponent || '');
		if (ui.mode !== key) showWait(g, key);
	}
	renderMisses(g);
}

function renderMisses(g) {
	// Wrong guesses so far: your own while guessing, your opponent's while you wait on them
	const el = $('g-misses');
	if (!el) return;
	const mine = g.history.find((h) => h.mine && h.result === 'pending');
	const list = g.step === 'guess' ? g.round.misses : mine ? mine.misses : [];
	el.innerHTML = list.length ? `<span class="dim">${g.step === 'guess' ? 'Tried' : `${esc(g.opponent)} tried`}</span> ${list.map((m) => `<s>${esc(m)}</s>`).join(' ')}` : '';
}

function renderBanner(g) {
	const el = $('g-banner'), l = g.last;
	if (!l || !l.mine || l.id === +sessionStorage.getItem('seen-last-' + g.id)) { el.classList.add('hidden'); return; }
	const tried = l.misses.length ? ` <span class="dim">after ${l.misses.length} wrong ${l.misses.length === 1 ? 'guess' : 'guesses'}: ${l.misses.map((m) => `<s>${esc(m)}</s>`).join(' ')}</span>` : '';
	const text  = (l.result === 'guessed' ? `${esc(g.opponent)} guessed your <b>${esc(l.word)}</b>! +${l.tier} coins` : `${esc(g.opponent)} ${l.result === 'skipped' ? 'skipped' : 'gave up on'} your <b>${esc(l.word)}</b>`) + tried;
	el.innerHTML = `<span>${text}</span><button class="btn sm" data-replay="${l.id}">Replay</button><button class="icon-btn" id="banner-x"><svg><use href="#i-x"/></svg></button>`;
	el.classList.remove('hidden');
	$('banner-x').onclick = () => { sessionStorage.setItem('seen-last-' + g.id, l.id); el.classList.add('hidden'); };
}

function renderHistory(g) {
	const rows = g.history.map((h) => {
		const res = { pending: 'waiting', guessed: 'guessed', passed: 'gave up', skipped: 'skipped' }[h.result];
		return `<li class="r-${h.result}"><span class="who">${h.mine ? 'You drew' : 'They drew'}</span><b>${h.word ? esc(h.word) : '???'}</b><span class="dim">${res}</span>${h.word || h.result !== 'pending' ? `<button class="icon-btn sm" data-replay="${h.id}" title="Replay"><svg><use href="#i-play"/></svg></button>` : ''}</li>`;
	}).join('');
	$('g-history').innerHTML = `<h4>History <span class="dim small">best streak ${g.best}</span></h4><ul>${rows || '<li class="dim">Nothing drawn yet</li>'}</ul>${g.status !== 'over' ? '<button class="btn sm ghost" id="g-resign">Resign</button>' : ''}`;
	const r = $('g-resign');
	if (r) r.onclick = () => confirmTap(r, 'Tap again to resign', async () => { await api(`/games/${g.id}/resign`, {}); });
}

document.addEventListener('click', async (e) => {
	const b = e.target.closest('[data-replay]');
	if (!b || !G.id) return;
	const r = await api(`/games/${G.id}/round/${b.dataset.replay}`);
	modal(`<h3>${r.word ? esc(r.word) : 'A drawing'}</h3><div class="board-wrap modal-board" id="replay-wrap"></div><div class="row center"><button class="btn sm" id="rp-skip"><svg><use href="#i-ff"/></svg>Skip</button><button class="btn sm" id="rp-again"><svg><use href="#i-play"/></svg>Again</button><button class="btn sm" id="rp-save"><svg><use href="#i-save"/></svg>Save PNG</button></div>`);
	const bd = new Board($('replay-wrap'));
	let rp = new Replay(bd, r.ops);
	$('rp-skip').onclick = () => rp.skip();
	$('rp-again').onclick = () => { rp.stop(); rp = new Replay(bd, r.ops); };
	$('rp-save').onclick = () => { rp.skip(); bd.save(r.word); };
});

async function startGuess(g) {
	const ui = G.ui;
	if (ui.replay) ui.replay.stop();
	Object.assign(ui, { mode: 'guess', roundId: g.round.id, board: null, recorder: null, tilesKey: null });
	const bd = gameBoard();
	$('g-prompt').innerHTML = `<span>${pic(g.round.drawer_id, 'xs')}<b>${esc(g.round.drawer)}</b> drew you something</span>${tierTag(g.round.tier)}`;
	renderTiles(g);
	const r = await api(`/games/${g.id}/round/${g.round.id}`);
	if (G.ui !== ui || ui.roundId !== r.id) return;
	ui.ops    = r.ops;
	ui.replay = new Replay(bd, r.ops, () => { const s = $('rp-speed'); if (s) s.classList.add('hidden'); });
	const s = $('rp-speed');
	if (s) s.classList.remove('hidden');
}

function renderTiles(g) {
	const ui = G.ui, rd = g.round;
	const key = JSON.stringify([rd.tiles, rd.revealed]);
	if (ui.tilesKey === key) return;
	ui.tilesKey = key;
	const n = rd.pattern.reduce((a, b) => a + b, 0);
	ui.slots = Array.from({ length: n }, (_, i) => rd.revealed[i] !== undefined ? { letter: rd.revealed[i], fixed: true } : null);
	ui.tiles = rd.tiles.map((t) => ({ letter: t, used: false }));
	$('g-under').innerHTML = `<div class="replay-ctl hidden" id="rp-speed"><button class="btn sm" id="rp-fast"><svg><use href="#i-ff"/></svg>Faster</button><button class="btn sm" id="rp-skip2">Skip to the end</button></div>
		<div class="slots" id="slots"></div><div class="misses" id="g-misses"></div><div class="tiles" id="tiles"></div>
		<div class="hints">
			<button class="btn sm" data-hint="reveal">Reveal a letter <span class="price">${cfg.hints.reveal}<span class="coin sm"></span></span></button>
			<button class="btn sm" data-hint="decoys"${rd.decoys ? ' disabled' : ''}>Remove decoys <span class="price">${cfg.hints.decoys}<span class="coin sm"></span></span></button>
			<button class="btn sm" data-hint="skip" title="Skip this drawing and keep your streak">Skip <span class="price">${cfg.hints.skip}<span class="coin sm"></span></span></button>
			<button class="btn sm ghost" id="g-pass" title="Give up for free, the streak goes back to 0">Give up</button>
		</div>`;
	if (ui.replay && !ui.replay.done) $('rp-speed').classList.remove('hidden');
	$('rp-fast').onclick = () => { if (ui.replay) { ui.replay.speed = ui.replay.speed >= 4 ? 1 : ui.replay.speed * 2; $('rp-fast').lastChild.textContent = ui.replay.speed > 1 ? `${ui.replay.speed}x` : 'Faster'; } };
	$('rp-skip2').onclick = () => { if (ui.replay) ui.replay.skip(); };
	$('g-under').querySelectorAll('[data-hint]').forEach((b) => {
		b.onclick = async () => {
			const res = await api(`/games/${g.id}/hint`, { kind: b.dataset.hint });
			if (res.skipped) return showResult({ kind: 'skipped', word: res.word }, res.game);
			G.data = res.game;
			renderGame();
		};
	});
	$('g-pass').onclick = () => confirmTap($('g-pass'), 'Lose your streak?', async () => {
		const res = await api(`/games/${g.id}/pass`, {});
		showResult({ kind: 'passed', word: res.word }, res.game);
	});
	drawTiles(rd);
	renderMisses(g);
}

function drawTiles(rd) {
	const ui = G.ui;
	let i = 0;
	$('slots').style.setProperty('--n', ui.slots.length + rd.pattern.length - 1); // phones size the slots so the answer fits one line
	$('slots').innerHTML = rd.pattern.map((len) => `<div class="slot-word">${Array.from({ length: len }, () => {
		const s = ui.slots[i];
		return `<button class="slot${s ? ' full' : ''}${s && s.fixed ? ' fixed' : ''}" data-slot="${i++}">${s ? s.letter : ''}</button>`;
	}).join('')}</div>`).join('');
	$('tiles').innerHTML = ui.tiles.map((t, j) => `<button class="tile${t.used ? ' used' : ''}" data-tile="${j}">${t.letter}</button>`).join('');
	$('slots').onclick = (e) => {
		const b = e.target.closest('[data-slot]');
		if (!b) return;
		const s = ui.slots[+b.dataset.slot];
		if (!s || s.fixed) return;
		ui.tiles[s.tile].used = false;
		ui.slots[+b.dataset.slot] = null;
		sfx('tap');
		drawTiles(rd);
	};
	$('tiles').onclick = (e) => {
		const b = e.target.closest('[data-tile]');
		if (!b) return;
		const t = ui.tiles[+b.dataset.tile];
		const at = ui.slots.findIndex((s) => !s);
		if (t.used || at < 0) return;
		t.used = true;
		ui.slots[at] = { letter: t.letter, tile: +b.dataset.tile };
		sfx('tap');
		drawTiles(rd);
		if (ui.slots.every((s) => s)) submitGuess(rd);
	};
}

async function submitGuess(rd) {
	const ui = G.ui, guess = ui.slots.map((s) => s.letter).join('');
	const res = await api(`/games/${G.id}/guess`, { guess });
	if (res.correct) return showResult({ kind: 'correct', word: res.word, coins: res.coins, streak: res.streak }, res.game);
	sfx('wrong');
	G.data = res.game;
	renderMisses(res.game);
	$('slots').classList.add('shake');
	setTimeout(() => {
		if (G.ui !== ui) return;
		ui.slots = ui.slots.map((s) => s && s.fixed ? s : null);
		ui.tiles.forEach((t) => { t.used = false; });
		drawTiles(rd);
		$('slots').classList.remove('shake');
	}, 650);
}

function showResult(res, game) {
	const ui = G.ui;
	if (ui.replay) ui.replay.skip();
	ui.mode  = 'result';
	G.data   = game;
	const vote = game.last && game.last.id === ui.roundId ? voteBox(game.last.vote) : '';
	const head = res.kind === 'correct' ? `<h2 class="good">Got it!</h2><p>It was <b>${esc(res.word.toUpperCase())}</b>. You both get <b>+${res.coins}</b><span class="coin sm"></span> and the streak is <b>🔥 ${res.streak}</b></p>` : `<h2 class="bad">${res.kind === 'skipped' ? 'Skipped' : 'Gave up'}</h2><p>It was <b>${esc(res.word.toUpperCase())}</b>.${res.kind === 'skipped' ? ' Your streak is safe.' : ' The streak is back to 0.'}</p>`;
	$('g-prompt').innerHTML = '';
	$('g-under').innerHTML = `<div class="result">${head}${vote}<div class="row center"><button class="btn sm" id="res-save"><svg><use href="#i-save"/></svg>Save PNG</button><button class="btn primary" id="res-next">Your turn to draw →</button></div></div>`;
	if (res.kind === 'correct') { confetti(); sfx('correct'); }
	else sfx('reveal');
	$('res-save').onclick = () => ui.board.save(res.word);
	$('res-next').onclick = () => { ui.mode = null; renderGame(); };
	renderHistory(game);
}

function showChoices(g, key) {
	const ui = G.ui;
	if (ui.replay) ui.replay.stop();
	Object.assign(ui, { mode: 'choose', choicesKey: key, replay: null, board: null, recorder: null });
	gameBoard();
	$('g-prompt').innerHTML = '<span>Pick a word to draw</span>';
	$('g-under').innerHTML = `<div class="choices">${g.choices.map((c, i) => `<button class="choice t${c.tier}" data-choice="${i}">${c.new ? '<span class="new-tag">new</span>' : ''}<b>${esc(c.word)}</b>${tierTag(c.tier)}</button>`).join('')}</div>
		<div class="row center"><button class="btn sm" id="g-bomb" title="Swap all three for new words">💣 New words <span class="price">${cfg.bomb}<span class="coin sm"></span></span></button></div>`;
	$('g-under').querySelectorAll('[data-choice]').forEach((b) => { b.onclick = () => startDraw(g, +b.dataset.choice); });
	$('g-bomb').onclick = async () => {
		const res = await api(`/games/${g.id}/bomb`, {});
		sessionStorage.removeItem('draft-' + g.id);
		G.data = res.game;
		renderGame();
	};
	const draft = loadDraft(g);
	if (draft) startDraw(g, draft.choice, draft.ops);
}

function loadDraft(g) {
	try {
		const d = JSON.parse(sessionStorage.getItem('draft-' + g.id) || 'null');
		return d && d.key === JSON.stringify(g.choices) ? d : null;
	} catch (e) {
		return null;
	}
}

function startDraw(g, choice, draftOps) {
	const ui = G.ui, pick = g.choices[choice];
	Object.assign(ui, { mode: 'draw', choice, ops: draftOps ? [...draftOps] : [] });
	const bd = gameBoard();
	bd.load(ui.ops);
	const save = () => { try { sessionStorage.setItem('draft-' + g.id, JSON.stringify({ key: JSON.stringify(g.choices), choice, ops: ui.ops })); } catch (e) {} };
	ui.recorder = new Recorder(bd, { packs: me.packs, onOp: (op) => { ui.ops.push(op); save(); } });
	if (ui.ops.length) ui.recorder.resume(ui.ops[ui.ops.length - 1].t);
	$('g-prompt').innerHTML = `<span>Draw <b class="word" style="--n:${pick.word.length}">${esc(pick.word)}</b></span>${tierTag(pick.tier)}`;
	$('g-under').innerHTML = `<div id="g-tools"></div><div class="row center"><button class="btn ghost sm" id="g-rechoose">Pick another word</button><button class="btn primary" id="g-send"><svg><use href="#i-send"/></svg>Send to ${esc(g.opponent || 'your opponent')}</button></div>`;
	ui.recorder.toolbar($('g-tools'));
	$('g-rechoose').onclick = () => confirmTap($('g-rechoose'), 'Throw this drawing away?', () => { sessionStorage.removeItem('draft-' + g.id); showChoices(G.data, JSON.stringify(G.data.choices)); });
	$('g-send').onclick = async () => {
		if (!bd.visible.length) return toast('Draw something first');
		$('g-send').disabled = true;
		try {
			const res = await api(`/games/${g.id}/draw`, { choice, ops: ui.ops });
			sessionStorage.removeItem('draft-' + g.id);
			ui.mode = null;
			G.data = res.game;
			sfx('send');
			toast('Sent!');
			renderGame();
		} catch (e) {
			$('g-send').disabled = false;
		}
	};
}

async function showWait(g, key) {
	const ui = G.ui;
	if (ui.replay) ui.replay.stop();
	Object.assign(ui, { mode: key, replay: null, board: null, recorder: null });
	const bd = gameBoard();
	const mine = g.history.find((h) => h.mine && h.result === 'pending');
	if (g.step === 'over') $('g-prompt').innerHTML = `<span>${esc(g.reason || 'This game is over')}</span>`;
	else if (!g.opponent) $('g-prompt').innerHTML = `<span>Waiting for ${g.invite ? 'someone to open your link' : 'a random player to pick it up'}</span>`;
	else $('g-prompt').innerHTML = `<span>Waiting for <b>${esc(g.opponent)}</b> to ${mine ? 'guess' : 'draw'}</span>`;
	const link = g.invite ? `${location.origin}/#/join/${g.invite}` : '';
	$('g-under').innerHTML = `${link ? `<div class="invite"><input readonly value="${esc(link)}"><button class="btn sm" id="g-copy"><svg><use href="#i-link"/></svg>Copy</button></div>` : ''}
		<div class="misses" id="g-misses"></div>
		${g.step !== 'over' ? `<p class="dim center small">The game ends ${dur(g.expires_at - now())} from now if nobody moves.</p>` : '<div class="row center"><a class="btn primary" href="#/">Back to games</a></div>'}`;
	if (link) $('g-copy').onclick = () => copy(link, 'Invite link');
	if (mine) {
		const r = await api(`/games/${g.id}/round/${mine.id}`);
		if (G.ui === ui && ui.mode === key) bd.load(r.ops);
	}
}

function gameChat(msgs) {
	const log = $('g-chat-log');
	for (const m of msgs) {
		if (G.chat.some((x) => x.id === m.id)) continue;
		G.chat.push(m);
		log.insertAdjacentHTML('beforeend', `<div class="msg${me && m.name === me.username ? ' me' : ''}">${pic(m.user_id, 'xs')}<b>${esc(m.name)}</b> ${esc(m.text)}</div>`);
		if (me && m.name !== me.username) {
			if (!G.chatOpen) G.unread++;
			if (G.chatLoaded) sfx('pop');
		}
	}
	G.chatLoaded = true;
	log.scrollTop = log.scrollHeight;
	$('g-chat-badge').textContent = G.unread;
	$('g-chat-badge').classList.toggle('hidden', !G.unread);
}


// ---------- live rooms ----------

function renderRooms() {
	const el = $('room-list');
	if (!rooms.length) { el.innerHTML = '<p class="empty">No public rooms right now. Make one!</p>'; return; }
	el.innerHTML = rooms.map((r) => `<a class="room-card" href="#/room/${r.code}"><b>${esc(r.name)}</b><span class="dim">${r.state === 'lobby' ? 'Waiting to start' : `Round ${Math.min(r.round, r.rounds)} of ${r.rounds}`}</span><span class="pill">${r.players}/${r.size}</span><span class="code">${r.code}</span></a>`).join('');
}

function guestNameField() {
	return me ? '' : `<input id="guest-name" placeholder="Your name" maxlength="16" value="${esc(localStorage.getItem(NAME_KEY) || '')}" autocomplete="off" required>`;
}

function openCreateRoom() {
	const opt = (list, pick, fmt) => list.map((v) => `<option value="${v}"${v === pick ? ' selected' : ''}>${fmt(v)}</option>`).join('');
	modal(`<h2>New live room</h2><form class="stack" id="create-room">
		${guestNameField()}
		<label>Rounds <select id="cr-rounds">${opt(cfg.live.rounds, 3, (v) => v)}</select></label>
		<label>Time to draw <select id="cr-seconds">${opt(cfg.live.seconds, 80, (v) => v + 's')}</select></label>
		<label>Players <select id="cr-size">${opt(cfg.live.sizes, 8, (v) => 'up to ' + v)}</select></label>
		<label class="check"><input type="checkbox" id="cr-public" checked> Show in the public room list</label>
		<button class="btn primary" type="submit">Create</button></form>`);
	$('create-room').onsubmit = async (e) => {
		e.preventDefault();
		const name = $('guest-name') ? $('guest-name').value.trim() : '';
		if (name) localStorage.setItem(NAME_KEY, name);
		askNotify();
		const res = await api('/live/create', { name, rounds: +$('cr-rounds').value, seconds: +$('cr-seconds').value, size: +$('cr-size').value, public: $('cr-public').checked });
		sessionStorage.setItem('pid-' + res.code, res.pid);
		closeModal();
		location.hash = `#/room/${res.code}`;
	};
}

async function openRoom(code) {
	if (R.code !== code) {
		Object.assign(R, { code, data: null, pid: sessionStorage.getItem('pid-' + code), board: null, recorder: null, outbox: [], chat: [], mode: null, chatLoaded: false, unread: 0 });
		$('r-chat-log').innerHTML = '';
		roomChatBadge();
		$('r-players').innerHTML = '';
		$('r-prompt').innerHTML = '';
		$('r-under').innerHTML = '';
		R.board = new Board($('r-board-wrap'));
		$('r-board-wrap').insertAdjacentHTML('beforeend', '<div class="board-fx"><div class="flash hidden" id="r-flash"></div><div class="feed" id="r-feed"></div></div>');
	}
	if (!R.pid) {
		if (me) {
			try { await joinRoom(code, ''); } catch (e) { location.hash = '#/live'; return; }
		} else {
			modal(`<h2>Join room ${esc(code)}</h2><form class="stack" id="join-room">${guestNameField()}<button class="btn primary" type="submit">Join</button><button class="btn link" type="button" id="jr-login">Or sign in to play with your account</button></form>`);
			$('jr-login').onclick = () => openAuth('login');
			$('join-room').onsubmit = async (e) => {
				e.preventDefault();
				const name = $('guest-name').value.trim();
				localStorage.setItem(NAME_KEY, name);
				await joinRoom(code, name);
				closeModal();
				connect();
			};
			return;
		}
	}
	connect();
}

async function joinRoom(code, name) {
	askNotify();
	const res = await api('/live/join', { code, name });
	R.pid = res.pid;
	sessionStorage.setItem('pid-' + code, res.pid);
}

function leaveRoom(code) {
	if (R.pid) fetch(`/live/${code}/leave`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pid: R.pid }) });
	sessionStorage.removeItem('pid-' + code);
	Object.assign(R, { code: null, pid: null, data: null, recorder: null, outbox: [] });
	toggleRoomChat(false);
	document.body.classList.remove('r-drawer');
}

function roomPost(action, body) {
	return api(`/live/${R.code}/${action}`, Object.assign({ pid: R.pid }, body || {}));
}

function roomUpdate(d) {
	if (!d) {
		if (R.pid) { toast('You are no longer in that room'); sessionStorage.removeItem('pid-' + R.code); R.pid = null; location.hash = '#/live'; }
		return;
	}
	const prev = R.data;
	R.data = d;
	$('r-name').textContent = d.name;
	$('r-round').textContent = d.state === 'lobby' ? `· code ${d.code}` : `· round ${Math.min(d.round, d.rounds)} of ${d.rounds}`;
	$('r-chat-input').placeholder = d.state === 'drawing' && d.drawer !== d.me && !(d.players.find((p) => p.id === d.me) || {}).guessed ? 'Type your guess' : 'Chat';
	$('r-players').innerHTML = [...d.players].sort((a, b) => b.score - a.score).map((p) => `<li class="${p.id === d.me ? 'me ' : ''}${p.guessed ? 'got ' : ''}${p.id === d.drawer ? 'drawing' : ''}">
		${avatar(p.name, p.user_id, 'sm')}
		<span class="pname">${esc(p.name)}${p.host ? ' <span title="Host">👑</span>' : ''}${p.guest ? '' : ''}</span>
		<span class="pscore">${p.score}</span>${p.id === d.drawer ? '<span class="ptag">✏️</span>' : p.guessed ? '<span class="ptag">✓</span>' : ''}
		${d.host && p.id !== d.me ? `<button class="icon-btn sm kick" data-kick="${p.id}" title="Kick"><svg><use href="#i-x"/></svg></button>` : ''}</li>`).join('');
	const drawing = d.state === 'drawing' && d.drawer === d.me;
	document.body.classList.toggle('r-drawer', drawing);
	if (prev) roomSounds(prev, d);
	const mode = `${d.state}:${d.turn}:${drawing}` + (d.state === 'lobby' ? `:${d.players.length}:${d.rounds}:${d.seconds}:${d.size}:${d.public}:${d.host}` : '');
	if (R.mode !== mode) {
		R.mode = mode;
		roomStage(d, prev);
	} else roomPrompt(d);
}

function roomSounds(prev, d) {
	// Ring for the moments that matter: the game starts, it is your turn, the word comes out, someone wins
	const here = `#/room/${d.code}`;
	if (prev.state === 'lobby' && d.state === 'choosing') { sfx('start'); notify(`The game in ${d.name} is starting!`, here); }
	if (d.state === 'choosing' && d.drawer === d.me && (prev.state !== 'choosing' || prev.turn !== d.turn)) {
		setTimeout(() => sfx('turn'), prev.state === 'lobby' ? 700 : 0);
		notify('Your turn to draw!', here);
	}
	if (prev.state === 'drawing' && d.state === 'reveal') sfx('reveal');
	if (prev.state !== 'done' && d.state === 'done') {
		const name = (d.players.find((p) => p.id === d.me) || {}).name;
		sfx(d.results && d.results.some((r) => r.winner && r.name === name) ? 'win' : 'reveal');
	}
}

function roomPrompt(d) {
	const drawer = d.players.find((p) => p.id === d.drawer);
	let html = '';
	if (d.state === 'lobby') html = `<span>${d.players.length < 2 ? 'Waiting for players to join' : d.host ? 'Start when everyone is in' : 'Waiting for the host to start'}</span>`;
	else if (d.state === 'choosing') html = d.drawer === d.me ? '<span>Pick a word to draw</span>' : `<span><b>${esc(drawer ? drawer.name : '')}</b> is picking a word</span>`;
	else if (d.state === 'drawing') html = d.word ? `<span>${d.drawer === d.me ? 'Draw' : 'You got it:'} <b class="word" style="--n:${d.word.length}">${esc(d.word)}</b></span>${tierTag(d.tier)}` : `<span class="mask" style="--n:${d.mask.length}">${d.mask.split('').map((c) => c === ' ' ? '<i class="gap"></i>' : `<i>${c === '_' ? '' : esc(c)}</i>`).join('')}</span><span class="dim small">${d.mask.replace(/ /g, '').length} letters</span>`;
	else if (d.state === 'reveal' && d.reveal) html = `<span>The word was <b class="word" style="--n:${d.reveal.word.length}">${esc(d.reveal.word)}</b></span>`;
	else if (d.state === 'done') html = '<span>Game over!</span>';
	$('r-prompt').innerHTML = html;
}

function roomStage(d, prev) {
	roomPrompt(d);
	const under = $('r-under');
	if (R.recorder) { R.recorder.on = false; R.recorder = null; R.board.el.classList.remove('drawable'); }
	if (d.state === 'lobby') {
		const link = `${location.origin}/#/room/${d.code}`;
		const sel  = (key, list, fmt) => `<select data-set="${key}"${d.host ? '' : ' disabled'}>${list.map((v) => `<option value="${v}"${v === d[key] ? ' selected' : ''}>${fmt(v)}</option>`).join('')}</select>`;
		under.innerHTML = `${d.results ? resultsTable(d.results) : ''}<div class="lobby">
			<div class="invite"><input readonly value="${esc(link)}"><button class="btn sm" id="r-copy"><svg><use href="#i-link"/></svg>Copy invite</button></div>
			<div class="settings"><label>Rounds ${sel('rounds', cfg.live.rounds, (v) => v)}</label><label>Time ${sel('seconds', cfg.live.seconds, (v) => v + 's')}</label><label>Players ${sel('size', cfg.live.sizes, (v) => v)}</label><label class="check"><input type="checkbox" data-set="public"${d.public ? ' checked' : ''}${d.host ? '' : ' disabled'}> Public</label></div>
			${d.host ? `<button class="btn primary big" id="r-start"${d.players.length < 2 ? ' disabled' : ''}>Start the game</button>` : ''}</div>`;
		$('r-copy').onclick = () => copy(link, 'Invite link');
		under.querySelectorAll('[data-set]').forEach((el) => { el.onchange = () => roomPost('settings', { [el.dataset.set]: el.type === 'checkbox' ? el.checked : +el.value }); });
		if ($('r-start')) $('r-start').onclick = () => roomPost('start');
	} else if (d.state === 'choosing') {
		under.innerHTML = d.choices ? `<div class="choices">${d.choices.map((c, i) => `<button class="choice t${c.tier}" data-choice="${i}">${c.new ? '<span class="new-tag">new</span>' : ''}<b>${esc(c.word)}</b>${tierTag(c.tier)}</button>`).join('')}</div>` : '';
		under.querySelectorAll('[data-choice]').forEach((b) => { b.onclick = () => roomPost('choose', { choice: +b.dataset.choice }); });
	} else if (d.state === 'drawing' && d.drawer === d.me) {
		R.recorder = new Recorder(R.board, { live: true, packs: d.packs, onOp: (op) => { R.outbox.push(op); flushOps(); } });
		under.innerHTML = '<div id="r-tools"></div>';
		R.recorder.toolbar($('r-tools'));
	} else if (d.state === 'reveal' && d.reveal) {
		const gains = Object.entries(d.reveal.gains).map(([id, pts]) => { const p = d.players.find((x) => x.id === +id); return p ? `<li>${pic(p.user_id, 'xs')}<b>${esc(p.name)}</b> +${pts}</li>` : ''; }).join('');
		under.innerHTML = `<div class="result"><ul class="gains">${gains || '<li class="dim">Nobody got it</li>'}</ul>${voteBox(d.reveal.vote)}<div class="row center"><button class="btn sm" id="r-save"><svg><use href="#i-save"/></svg>Save PNG</button></div></div>`;
		$('r-save').onclick = () => R.board.save(d.reveal.word);
	} else if (d.state === 'done') {
		under.innerHTML = resultsTable(d.results);
		if (d.results && d.results.some((r) => r.winner && r.name === (d.players.find((p) => p.id === d.me) || {}).name)) confetti();
	} else under.innerHTML = '';
}

function resultsTable(results) {
	if (!results) return '';
	return `<div class="results"><h3>Final scores</h3><ol>${results.map((r) => `<li class="${r.winner ? 'win' : ''}"><b>${pic(r.user_id, 'xs')}${esc(r.name)}</b><span>${r.score}</span>${r.winner ? ' 🏆' : ''}</li>`).join('')}</ol></div>`;
}

function roomOps(d) {
	const bd = R.board;
	if (!bd) return;
	if (bd.turn !== d.turn) { bd.reset(); bd.turn = d.turn; }
	d.ops.forEach((op, i) => { if (d.from + i >= bd.ops.length) bd.apply(op); });
}

async function flushOps() {
	if (R.sending || !R.outbox.length) return;
	R.sending = true;
	const batch = R.outbox.splice(0, R.outbox.length);
	try {
		await api(`/live/${R.code}/draw`, { pid: R.pid, ops: batch });
	} catch (e) {}
	R.sending = false;
	if (R.outbox.length) setTimeout(flushOps, 60);
}

function roomChat(msgs) {
	const log = $('r-chat-log'), myName = R.data ? (R.data.players.find((p) => p.id === R.data.me) || {}).name : null;
	for (const m of msgs) {
		if (R.chat.includes(m.id)) continue;
		R.chat.push(m.id);
		const seat = m.name && R.data ? R.data.players.find((p) => p.name === m.name) : null;
		const line = `${m.name ? `${pic(seat && seat.user_id, 'xs')}<b>${esc(m.name)}</b> ` : ''}${esc(m.text)}`;
		log.insertAdjacentHTML('beforeend', `<div class="msg k-${m.kind}${m.name && m.name === myName ? ' me' : ''}">${line}</div>`);
		if (!R.chatLoaded) continue; // history from before you joined stays quiet
		const mine = m.name && m.name === myName;
		if (m.kind === 'good') { sfx(mine ? 'correct' : 'other'); roomFlash(mine ? 'You got it!' : `${m.name} got it!`, m.text.replace(/^.*?(\+\d+)$/, '$1')); if (mine) confetti(); }
		else if (m.kind === 'miss') sfx(mine ? 'wrong' : 'pop');
		else if (m.kind === 'close') sfx('close');
		else if (!mine && m.kind !== 'system') sfx('pop');
		if (m.kind !== 'system') roomBubble(m, line);
		if (!R.chatOpen && !mine && m.kind !== 'system') R.unread++;
	}
	R.chatLoaded = true;
	while (log.children.length > 150) log.firstChild.remove();
	log.scrollTop = log.scrollHeight;
	roomChatBadge();
}

function roomChatBadge() {
	$('r-chat-badge').textContent = R.unread > 99 ? '99+' : R.unread;
	$('r-chat-badge').classList.toggle('hidden', !R.unread);
}

function roomBubble(m, line) {
	// On phones the chat is tucked away, so guesses float over the bottom of the board for a few seconds
	const feed = $('r-feed');
	if (!feed) return;
	feed.insertAdjacentHTML('beforeend', `<div class="bubble k-${m.kind}">${line}</div>`);
	const b = feed.lastElementChild;
	while (feed.children.length > 3) feed.firstElementChild.remove();
	setTimeout(() => b.classList.add('out'), 3500);
	setTimeout(() => b.remove(), 4000);
}

function roomFlash(title, sub) {
	const el = $('r-flash');
	if (!el) return;
	el.innerHTML = `<b>${esc(title)}</b>${sub ? `<span>${esc(sub)}</span>` : ''}`;
	el.classList.remove('hidden', 'go');
	void el.offsetWidth; // restart the animation
	el.classList.add('go');
	clearTimeout(el.timer);
	el.timer = setTimeout(() => el.classList.add('hidden'), 1800);
}

function toggleRoomChat(open) {
	R.chatOpen = open;
	$('r-chat').classList.toggle('open', open);
	document.body.classList.toggle('rchat-open', open);
	if (open) { R.unread = 0; roomChatBadge(); $('r-chat-log').scrollTop = 1e9; }
}

function refreshToolbars() {
	if (G.ui && G.ui.recorder && $('g-tools')) { G.ui.recorder.packs = me.packs; G.ui.recorder.toolbar($('g-tools')); }
}

function tickTimer() {
	const d = R.data, el = $('r-timer');
	if (route.view !== 'room' || !d || !['choosing', 'drawing', 'reveal', 'done'].includes(d.state)) { el.textContent = ''; return; }
	const left = Math.max(0, Math.ceil(d.deadline - now()));
	el.textContent = left;
	el.classList.toggle('low', d.state === 'drawing' && left <= 10);
	if (d.state === 'drawing' && left <= 5 && left > 0 && R.tick !== left) sfx('tick');
	R.tick = left;
}


// ---------- ranks and words ----------

async function loadRanks() {
	const rows = await api(`/leaderboard?board=${board}`);
	const fmt  = { coins: (r) => `${r.value.toLocaleString()}<span class="coin sm"></span>`, streak: (r) => `🔥 ${r.value}`, live: (r) => `${r.value} <span class="dim small">of ${r.extra}</span>`, drawer: (r) => `${r.value}% <span class="dim small">of ${r.extra}</span>` }[board];
	$('ranks').innerHTML = rows.length ? rows.map((r, i) => `<li class="${me && r.name === me.username ? 'me' : ''}"><span class="rank">${i + 1}</span>${avatar(r.name, r.user_id, 'sm')}<b>${esc(r.name)}</b><span class="val">${fmt(r)}</span></li>`).join('') : '<li class="empty">Nobody here yet</li>';
	$('rank-note').textContent = { coins: 'Every coin earned, spent or not.', streak: 'The longest streak with any one partner.', live: 'Live games won, of live games played.', drawer: 'How often your drawings get guessed, after at least 10.' }[board];
}

async function loadWords() {
	if (!me) return;
	const rows = await api('/words/mine');
	$('my-words').innerHTML = rows.length ? `<table><tr><th>Word</th><th>Tier</th><th>Status</th><th>Votes</th><th>Plays</th></tr>${rows.map((w) => `<tr><td>${esc(w.text)}</td><td>${TIER_NAME[w.tier]}</td><td><span class="status s-${w.status}">${w.status}</span></td><td>+${w.up} / -${w.down}</td><td>${w.plays}</td></tr>`).join('')}</table>` : '<p class="dim">You have not submitted any words yet.</p>';
	if (me.admin) loadAdmin();
}

async function loadAdmin() {
	const q   = new URLSearchParams({ status: $('admin-status').value, q: $('admin-q').value, offset: adminPage * 50 });
	const res = await api('/admin/words?' + q);
	$('admin-words').innerHTML = res.words.length ? `<table><tr><th>Word</th><th>Tier</th><th>Status</th><th>Votes</th><th>By</th><th></th></tr>${res.words.map((w) => `<tr data-id="${w.id}"><td>${esc(w.text)}${w.pack === 'spicy' ? ' 🌶' : ''}</td>
		<td><select data-tier>${[1, 2, 3].map((t) => `<option value="${t}"${t === w.tier ? ' selected' : ''}>${TIER_NAME[t]}</option>`).join('')}</select></td>
		<td><span class="status s-${w.status}">${w.status}</span></td><td>+${w.up} / -${w.down}</td><td>${w.by ? esc(w.by) : '<span class="dim">built in</span>'}</td>
		<td class="acts">${w.status !== 'approved' ? '<button class="btn sm" data-a="approve">Approve</button>' : ''}${w.status !== 'rejected' && w.source === 'community' ? '<button class="btn sm" data-a="reject">Reject</button>' : ''}${w.status !== 'banned' ? '<button class="btn sm" data-a="ban">Ban</button>' : ''}${w.source === 'community' ? '<button class="btn sm ghost" data-a="delete">Delete</button>' : ''}</td></tr>`).join('')}</table>` : '<p class="dim">No words match.</p>';
	const pages = Math.ceil(res.total / 50);
	$('admin-pages').innerHTML = pages > 1 ? `<span class="dim">${res.total} words</span>${adminPage ? '<button class="btn sm" data-page="-1">Prev</button>' : ''}<span>Page ${adminPage + 1} of ${pages}</span>${adminPage < pages - 1 ? '<button class="btn sm" data-page="1">Next</button>' : ''}` : `<span class="dim">${res.total} words</span>`;
}


// ---------- fun ----------

function confetti() {
	const colors = ['#ff4fa3', '#ffd23f', '#3fd5ff', '#8cff5a', '#ff8a3d', '#b07dff'];
	const box = document.createElement('div');
	box.className = 'confetti';
	for (let i = 0; i < 60; i++) {
		const p = document.createElement('i');
		p.style.cssText = `left:${Math.random() * 100}%;background:${colors[i % colors.length]};animation-delay:${Math.random() * 0.4}s;animation-duration:${1.4 + Math.random()}s;transform:rotate(${Math.random() * 360}deg)`;
		box.appendChild(p);
	}
	document.body.appendChild(box);
	setTimeout(() => box.remove(), 3000);
}


// ---------- wiring ----------

function wire() {
	$('modal-close').onclick = closeModal;
	$('modal-bg').onclick = (e) => { if (e.target === $('modal-bg')) closeModal(); };
	$('btn-account').onclick = () => openAccount();
	$('btn-coins').onclick = () => openAccount('shop');
	document.addEventListener('click', (e) => {
		const a = e.target.closest('[data-act]');
		if (a) openAuth(a.dataset.act);
	});
	$('new-random').onclick = () => newGame({ mode: 'random' });
	$('new-invite').onclick = () => newGame({ mode: 'invite' });
	$('new-user').onsubmit = (e) => { e.preventDefault(); const n = $('new-user-name').value.trim(); if (n) newGame({ mode: 'user', opponent: n }); };
	$('btn-create-room').onclick = openCreateRoom;
	$('join-code').onsubmit = (e) => { e.preventDefault(); const c = $('join-code-input').value.trim().toUpperCase(); if (c.length === 4) location.hash = `#/room/${c}`; };
	$('rank-tabs').onclick = (e) => {
		const b = e.target.closest('[data-board]');
		if (!b) return;
		board = b.dataset.board;
		$('rank-tabs').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
		loadRanks();
	};
	$('submit-word').onsubmit = async (e) => {
		e.preventDefault();
		await api('/words/submit', { text: $('submit-text').value, tier: +$('submit-tier').value });
		$('submit-text').value = '';
		toast('Submitted! It will show up as a new word for others to vote on');
		loadWords();
	};
	$('admin-add').onsubmit = async (e) => {
		e.preventDefault();
		await api('/admin/words/add', { text: $('admin-add-text').value, tier: +$('admin-add-tier').value });
		$('admin-add-text').value = '';
		toast('Added');
		loadAdmin();
	};
	$('admin-filter').onsubmit = (e) => { e.preventDefault(); adminPage = 0; loadAdmin(); };
	$('admin-status').onchange = () => { adminPage = 0; loadAdmin(); };
	$('admin-words').addEventListener('click', async (e) => {
		const b = e.target.closest('[data-a]');
		if (!b) return;
		const id = b.closest('tr').dataset.id;
		if (b.dataset.a === 'delete') return confirmTap(b, 'Sure?', async () => { await api(`/admin/words/${id}`, { action: 'delete' }); loadAdmin(); });
		await api(`/admin/words/${id}`, { action: b.dataset.a });
		loadAdmin();
	});
	$('admin-words').addEventListener('change', async (e) => {
		if (!e.target.matches('[data-tier]')) return;
		await api(`/admin/words/${e.target.closest('tr').dataset.id}`, { action: 'tier', tier: +e.target.value });
		toast('Tier changed');
	});
	$('admin-pages').onclick = (e) => { const b = e.target.closest('[data-page]'); if (b) { adminPage += +b.dataset.page; loadAdmin(); } };
	$('g-chat-btn').onclick = () => {
		G.chatOpen = !G.chatOpen;
		$('g-chat').classList.toggle('hidden', !G.chatOpen);
		document.body.classList.toggle('chat-open', G.chatOpen);
		G.unread = 0;
		$('g-chat-badge').classList.add('hidden');
		if (G.chatOpen) { $('g-chat-input').focus(); $('g-chat-log').scrollTop = 1e9; }
	};
	$('g-chat-form').onsubmit = async (e) => {
		e.preventDefault();
		const text = $('g-chat-input').value;
		if (!text.trim()) return;
		await api(`/games/${G.id}/chat`, { text });
		$('g-chat-input').value = '';
	};
	$('r-chat-form').onsubmit = async (e) => {
		e.preventDefault();
		const text = $('r-chat-input').value;
		if (!text.trim()) return;
		$('r-chat-input').value = '';
		try { await roomPost('say', { text }); } catch (err) { $('r-chat-input').value = text; }
	};
	$('r-players').onclick = (e) => {
		const b = e.target.closest('[data-kick]');
		if (b) confirmTap(b, 'Kick?', () => roomPost('kick', { player: +b.dataset.kick }));
	};
	$('r-chat-toggle').onclick = () => toggleRoomChat(!R.chatOpen);
	$('r-chat-close').onclick = () => toggleRoomChat(false);
	document.querySelectorAll('.sound-btn').forEach((b) => { b.onclick = () => setSound(!soundOn); });
	setSound(soundOn);
	$('btn-notify').onclick = askNotify;
	updateNotifyBtn();
	document.addEventListener('pointerdown', unlockAudio, { passive: true });
	document.addEventListener('keydown', unlockAudio);
	viewportVars();
	if (window.visualViewport) { visualViewport.addEventListener('resize', viewportVars); visualViewport.addEventListener('scroll', viewportVars); }
	window.addEventListener('resize', viewportVars);
	if ('serviceWorker' in navigator) {
		navigator.serviceWorker.register('/sw.js').then((r) => { swReg = r; }).catch(() => {});
		navigator.serviceWorker.addEventListener('message', (e) => { if (e.data && e.data.hash) location.hash = e.data.hash; });
	}
	window.addEventListener('hashchange', onRoute);
	setInterval(tickTimer, 250);
	setInterval(() => { if (route.view === 'games') renderGames(); }, 60000);
}

async function boot() {
	wire();
	await loadMe();
	onRoute();
	const params = new URLSearchParams(location.search);
	if (cfg.arcade_public_url && params.get('challenge')) {
		challenge = params.get('challenge').trim();
		params.delete('challenge');
		history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params : '') + location.hash);
		if (me) startChallenge();
		else openAuth('login');
	}
}

boot();
