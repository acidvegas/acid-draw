# acid-draw

Draw a word, someone else guesses it. Turn based 1v1 games and live rooms in the browser, built with Flask.

## Features

### Turn based 1v1

- Pick one of three words *(easy, medium or hard, worth 1, 2 or 3 coins)* and draw it. Your opponent watches a replay of every stroke, including the mistakes and the undos, and guesses the word from a bank of scrambled letter tiles
- A correct guess pays the coins to both players *(up to 150 coins a day per account from games)* and adds to the streak. Then the guesser draws one back
- A drawing needs a little ink *(a short stroke or a few shapes)* before it can be sent, and guesses are limited to one a second
- Hints cost coins: reveal a letter *(10)*, remove the decoy tiles *(15)* or skip the drawing without losing the streak *(20)*. Giving up is free but the streak goes back to 0
- Bombs *(25 coins)* swap the three word choices for new ones
- Every wrong guess is kept. The drawer sees them as they come in and again when the round is over
- Find an opponent at random, challenge a username, or send an invite link. Random and invite games start with your drawing, so the other player has something to guess as soon as they show up
- Up to 30 games at once. A game ends after 3 days without a move
- A chat in every game, a history of every drawing with replays, and the word choices are fixed until you draw so a refresh can not reroll them. Drawings in progress survive a refresh

### Live rooms

- 2 to 10 players take turns drawing while everyone else types guesses. Faster guesses score more, and the drawer scores for every player who gets it
- Letters of the word are revealed at half time and three quarters time
- Wrong guesses show up for everyone, and a right guess flashes over the board with the points it scored
- Guesses one letter off are flagged as close *(only to the guesser)*. Players who already got the word chat among themselves so they can not give it away
- Rooms are public *(listed)* or private *(code or link only)*. The host sets rounds, time to draw and room size, starts the game and can kick players
- Guests can play with just a name. Names that belong to accounts are reserved
- Up to 5 open rooms and 20 open event streams per IP address
- Right guesses pay coins *(1, 2 or 3 by word)* to the guesser and the drawer, but only between two accounts, only once something real is drawn, and only up to 150 coins a day per account from live rooms

### Drawing

- Pen, eraser, flood fill, line, box and circle, 4 brush sizes, undo and clear
- 10 colors to start. Six color packs can be bought with coins
- Save any finished drawing as a PNG

### Words

- 1,539 built-in words split into easy, medium and hard, including a spicy pack that is mixed in by default
- Anyone signed in can submit words *(10 a day)*. New words are offered now and then as a choice marked **new**. Players who played one can vote it up or down: 5 more ups than downs puts it in the main pool, 3 more downs than ups pulls it
- Admins *(set with `DRAW_ADMINS`)* get a page to approve, reject, ban, retier, delete and add words

### Accounts

- Optional *(username, password and a captcha)*. Every new account starts with 50 coins
- Captchas are drawn as paths, not text. Logins lock for 15 minutes after 10 failures per account or 30 per address, captchas are limited to 30 a minute per address
- Leaderboards for coins earned, best streak, live wins and best drawer *(how often your drawings get guessed, after at least 10)*
- Live updates over Server-Sent Events

### Sounds and notifications

- Sound effects for right and wrong guesses, your turn, the last seconds of the timer and the end of a game *(mute button at the top)*
- Browser notifications when it is your turn, someone starts a game with you, or a live game starts, while the page is open in the background

### Phones

- In live rooms the guess box stays at the bottom of the screen and the chat pops up over the board. New guesses float over the board for a few seconds
- The word or the blanks always fit on one line

## Run it

```bash
DRAW_BIND=127.0.0.1 DRAW_ADMINS=acidvegas ./setup.sh
```

This builds the `aciddraw` image and starts a container on port 20010 with the `aciddraw_data` volume mounted at `/data`.

| Variable          | Default     | What it does                                                                                                                             |
|-------------------|-------------|------------------------------------------------------------------------------------------------------------------------------------------|
| `DRAW_BIND`       | `127.0.0.1` | Address the port is published on                                                                                                         |
| `DRAW_PROXIES`    | `0`         | Number of reverse proxies in front of the app. Set it to `1` behind nginx                                                                |
| `DRAW_ADMINS`     |             | Comma separated usernames that can manage the word list                                                                                  |
| `DRAW_PUBLIC_URL` |             | Main address for canonical links, share cards and the sitemap *(e.g. `https://draw.acid.vegas`)*. Unset uses the address of each request |

For development:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd aciddraw && ../.venv/bin/gunicorn --worker-class gevent --workers 1 --bind 127.0.0.1:20010 app:app
```

Live rooms and captchas live in memory, so always run a single worker. Live rooms do not survive a restart, turn based games do.

## acid-arcade-db

acid-draw can optionally use [acid-arcade-db](../acid-arcade-db) for accounts, so one username and password works on every acid arcade game. With `ARCADE_URL` unset nothing changes and the game keeps its own accounts.

With it set, signup, login and captchas go through acid-arcade-db, every request checks its session there, and the local `users` table becomes a mirror of the central account *(new mirrors get the starting coins)*. Renames and deletions are picked up once a minute. Coins earned, coins, best streak, drawings and games are pushed to the player's arcade profile after every paying guess and at the end of each live game. Pictures show next to account names, and the account menu links to the arcade account page for password, picture and deletion. Admins are the `DRAW_ADMINS` names plus arcade admins. If acid-arcade-db is down the game answers 503 instead of treating everyone as a guest.

| Variable            | Default                     | What it does                                                          |
|---------------------|-----------------------------|-----------------------------------------------------------------------|
| `ARCADE_URL`        |                             | Internal URL of acid-arcade-db *(e.g. `http://acid-arcade-db:20021`)* |
| `ARCADE_PUBLIC_URL` | `https://arcade.{domain}`   | Public arcade site, used for pictures and the account page. `{domain}` becomes the player's parent domain *(draw.supernets.org links to arcade.supernets.org)* |
| `ARCADE_GAME`       | `draw`                      | This game's slug in acid-arcade-db                                    |

`setup.sh` passes these and joins the `arcade` docker network when `ARCADE_URL` is set. Logins and signups send the player's address to acid-arcade-db for rate limiting, so set `DRAW_PROXIES=1` behind nginx.

## Tests

```bash
.venv/bin/python tests/test_app.py
```

## Code layout

| Path                  | Contents                                                                |
|-----------------------|-------------------------------------------------------------------------|
| `aciddraw/app.py`     | Flask routes and the event stream                                       |
| `aciddraw/duel.py`    | Turn based games: turns, guesses, hints, streaks, expiry                |
| `aciddraw/live.py`    | Live rooms: turns, timers, guessing in chat, scoring                    |
| `aciddraw/words.py`   | Word list, choices, tiles, submissions, votes and admin actions         |
| `aciddraw/canvas.py`  | Drawing data checks, brush sizes and color packs                        |
| `aciddraw/storage.py` | SQLite storage for users, games, words and leaderboards                 |
| `aciddraw/hub.py`     | Room clock and event stream wake ups                                    |
| `aciddraw/auth.py`    | Sessions and captcha                                                    |
| `aciddraw/words/`     | `easy.txt`, `medium.txt`, `hard.txt` and `spicy.txt` *(tier prefixed)*  |
| `aciddraw/static/`    | `utils.js`, `style.css`, `sw.js` *(notifications on Android)* and icons |

## Data

Everything is written to `DRAW_DATA_DIR`, which defaults to `data/` at the repo root. `aciddraw.db` holds accounts, sessions, games with every drawing, chat, and the word list with its votes. Built-in words are added on startup if missing, so banned ones stay banned.

---

###### Mirrors: [SuperNETs](https://git.supernets.org/acidvegas/) • [GitHub](https://github.com/acidvegas/) • [GitLab](https://gitlab.com/acidvegas/) • [Codeberg](https://codeberg.org/acidvegas/)
