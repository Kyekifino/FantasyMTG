# Fantasy MTG

A fantasy-football style game for Magic: The Gathering. Players spend a budget buying and
selling cards at their TCGplayer market price (as reported by Scryfall), aiming for the
highest total value when the game ends. It ships as a self-hosted Discord bot, but the game
logic doesn't depend on Discord.

## Self-hosting on Discord

One running copy of the bot serves every server it's added to. Each channel can hold its own
game, and adding the bot to a new server needs no changes on the machine running it.

### 1. Create the bot

1. Go to <https://discord.com/developers/applications>, click **New Application**, and name it.
2. Open **Bot**, click **Reset Token**, and copy the token. No privileged intents are needed.
   While you're there, decide on **Public Bot**: on lets anyone with the invite link add the
   bot to their server; off means only you can add it.
3. Open **Installation**:
   - Under **Installation Contexts**, keep **Guild Install** ticked and untick **User Install**
     (the commands only work in servers).
   - Under **Install Link**, choose **Discord Provided Link**.
   - Under **Default Install Settings → Guild Install**, add the scopes `applications.commands`
     and `bot`, then the permissions **View Channels**, **Send Messages**, **Add Reactions** and
     **Read Message History**. Save.

### 2. Configure

```bash
cp .env.example .env
```

Set `DISCORD_TOKEN` in `.env`. That's the only required setting.

### Adding the bot to servers

Open the install link from the **Installation** page, pick a server, and approve. You need the
**Manage Server** permission in that server. You can share the link with other people the same
way; if **Public Bot** is on, they can add the bot to their own servers.

The bot registers its slash commands globally each time it starts, so they appear in every
server, including ones it joins later. On the very first start they can take a few minutes to
show up. If a channel restricts the bot, give it the four permissions above there.

### 3. Run with Docker (recommended)

```bash
docker compose up -d --build
```

The SQLite database lives in `./data/`, so it survives restarts and upgrades. To upgrade, pull the
new code and run the same command again.

### Or run without Docker (Python 3.10+)

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install .
```

```bash
DISCORD_TOKEN=... FANTASYMTG_DB_PATH=data/fantasymtg.db .venv/bin/fantasymtg-discord
```

On Windows, use `.venv\Scripts\` in place of `.venv/bin/`.

### Or run on a Raspberry Pi (no Docker)

A Pi 2 or newer is plenty. Docker is a poor fit for 32-bit Pis, so this installs the bot as a
systemd service instead. You'll need Raspberry Pi OS Bookworm or newer (Python 3.10+).

On the Pi, get the code and run the installer:

```bash
git clone https://github.com/Kyekifino/FantasyMTG.git
```

```bash
cd FantasyMTG
```

```bash
sudo bash deploy/raspberry-pi/install.sh
```

Add your bot token to the settings file it creates:

```bash
sudo nano /etc/fantasymtg/fantasymtg.env
```

Then start the bot:

```bash
sudo systemctl start fantasymtg
```

Check on it with `systemctl status fantasymtg`, or follow its logs:

```bash
journalctl -u fantasymtg -f
```

To upgrade, run `git pull` in the `FantasyMTG` folder and run `install.sh` again. Your settings and
database are kept, and the installer restarts the bot for you. Restarting never loses game state:
every change (settings, players, trades, quotes waiting for a 👍) is saved the moment it happens,
so a game you're still setting up is exactly as you left it. On stop, the bot finishes what it's
doing before exiting, and newer versions automatically upgrade the database's format.

What the installer sets up:

| Path | Contents |
|---|---|
| `/opt/fantasymtg/` | The app and its Python virtual environment |
| `/etc/fantasymtg/fantasymtg.env` | Your settings (readable by root only) |
| `/var/lib/fantasymtg/fantasymtg.db` | The game database |
| `/var/lib/fantasymtg/backups/` | Daily database backups (newest 14 kept) |

**Power outages.** The bot starts by itself when power returns, and it waits until the Pi's
clock has been corrected over the network first. A Pi has no battery-backed clock, so it boots
with a stale time. The installer enables `systemd-time-wait-sync`, and the service starts only
after the clock is synced. If the internet is still down, the bot waits, then retries every
15 seconds until it can reach Discord. Once running, it catches up on whatever it missed:

- Quotes that expired during the outage are announced as timed out.
- Missed budget top-offs are paid out, and a missed weekly leaderboard is posted once.
- If `endDate` passed during the outage, the game ends and the final standings are posted.
- 👍/👎 reactions added while the bot was offline are picked up for quotes that are still
  within their 30 minutes. If the requester left both, it counts as a cancel. Quotes past 30
  minutes have expired, because Discord doesn't say *when* a reaction was added.

Sudden power loss can occasionally corrupt an SD card. The database itself is crash-safe, and
the daily backup timer guards against a damaged database file. A backup on the same card won't
survive the card failing, though, so now and then copy `/var/lib/fantasymtg/backups/` to
another machine. To restore one:

```bash
sudo systemctl stop fantasymtg
```

```bash
sudo cp /var/lib/fantasymtg/backups/<backup-file>.db /var/lib/fantasymtg/fantasymtg.db
```

```bash
sudo rm -f /var/lib/fantasymtg/fantasymtg.db-wal /var/lib/fantasymtg/fantasymtg.db-shm
```

```bash
sudo chown fantasymtg:fantasymtg /var/lib/fantasymtg/fantasymtg.db
```

```bash
sudo systemctl start fantasymtg
```

## Playing

| Command | What it does |
|---|---|
| `/creategame` | Create a game in this channel. You become its admin. |
| `/configure [variable] [value]` | Admin only. No arguments lists settings. Mid-game changes need a 👍 from the admin. |
| `/joingame <name>` | Join before the game starts (or later, if `allowLateJoin` is on). |
| `/startgame` | Admin only. Starts the game now. |
| `/buy <scryfall_link> <quantity>` | Quote a purchase. React 👍 within 30 minutes to confirm, 👎 to cancel. |
| `/sell <scryfall_link> <quantity>` | Quote a sale. Confirm the same way. |
| `/portfolio [name]` | A player's cards, free budget and total value. Defaults to you. |
| `/leaderboard` | Everyone ranked by total value. |
| `/help [command]` | The command list, or details on one command. |

The bot also posts on its own: when a quote times out, when budgets are topped off, a weekly
leaderboard, and the final standings with congratulations when the game ends.

### Settings

| Variable | Default | Notes |
|---|---|---|
| `endDate` | 1 year after start | `YYYY-MM-DD` (midnight UTC) or an ISO timestamp |
| `maxPlayerCount` | 10 | |
| `startingBudget` | $100 | |
| `maxSpecNumber` | 5 | Max number of *different* cards held at once |
| `tradeFee` | $2 | Charged on every buy and every sell |
| `topOffDays` | 30 | |
| `topOffBudget` | $20 | 0 disables top-offs |
| `uniqueOwnership` | true | Only one player may own a given card |
| `allowLateJoin` | false | Let players join after the game has started |

### Rules decisions not spelled out in the spec

- **Price:** Scryfall's nonfoil USD price. Printings with no nonfoil price fall back to foil,
  then etched. A card with no price at all can't be bought or sold.
- **Card identity:** one Scryfall page is one card. The bot resolves links by set code and
  collector number, and the confirmation message names the card it found so players can
  check it.
- **Starting:** `/startgame` starts the game immediately and every player gets
  `startingBudget` at that moment. Top-offs (every `topOffDays`) and weekly leaderboards
  (every 7 days) are counted from the start. No top-off happens at or after `endDate`. The
  game needs at least one player to start, and the admin isn't added as a player unless they
  join.
- **Confirmations:** the quoted price *and* fee are locked in. Everything else (funds,
  ownership, card limit, copies owned) is checked again when the player confirms, so racing
  quotes are settled fairly: whoever confirms first wins.
- **Late joining:** with `allowLateJoin` on, people can join a game in progress, still up to
  `maxPlayerCount`. They start with the current `startingBudget` and get every top-off from
  then on, but top-offs paid before they joined aren't backdated.
- **Mid-game config changes** apply from then on. Changing `startingBudget` doesn't change
  existing balances, and lowering `maxSpecNumber` or turning on `uniqueOwnership` doesn't
  force anyone to sell. Config confirmations also expire after 30 minutes.
- **Valuation:** portfolio and leaderboard values use live prices, with Scryfall responses
  cached for an hour (Scryfall updates prices about once a day). If Scryfall is down, the
  last known price is used. Once the game ends, values are frozen at the final prices, and a
  new game can be created in the same channel.
- **Downtime:** if the bot was offline, missed top-offs are paid out when it comes back,
  quotes that expired during downtime are announced, and reactions on still-open quotes are
  picked up after it reconnects.
- **Internet outages:** the bot keeps running and reconnects on its own. Announcements it
  couldn't post (timeouts, top-offs, leaderboards, the final standings) are saved and posted
  in order once Discord is reachable again. If a channel is deleted or the bot loses access
  to it, those announcements are dropped, and any announcement still unsent after 14 days is
  dropped too.
- **Game end:** trading always stops at `endDate`. The final standings wait for fresh
  Scryfall prices for up to 6 hours, then fall back to the last known prices.

## Architecture

```
src/fantasymtg/
  core/           Game rules and orchestration. No Discord, HTTP or SQL.
    service.py      GameService: the only API an interface needs
    models.py       Game, Player, Holding, PendingAction (plain dataclasses)
    config.py       Settings registry: parsing, validation, defaults
    results.py      Structured return values and scheduled-job notifications
    errors.py       Typed rule violations (data only, no wording)
    pricing.py      PriceSource port        ← implemented by adapters/scryfall.py
    repository.py   GameRepository port     ← implemented by adapters/*_repo.py
    clock.py        Clock port (injectable time)
  adapters/       Scryfall client, SQLite and in-memory repositories
  presentation/   Interface-neutral text: messages, tables, help catalog
  discord_bot/    Slash commands, reaction handling, scheduler loop (thin)
```

- **Ports and adapters.** `GameService` depends only on the `PriceSource`, `GameRepository` and
  `Clock` protocols. Tests pass in fakes (`tests/conftest.py`), so every rule, including
  30-minute timeouts and year-long games, is tested deterministically and offline.
- **Interface-agnostic identifiers.** The core only sees opaque strings: a `channel_key`
  (Discord uses `discord:<guild>:<channel>`) and a `user_id`. Confirmations are generic
  `PendingAction`s. An interface approves or declines them with `respond()` and can link its
  own handle (such as a message ID) with `attach_external_ref()`.
- **Time-based rules** run in `GameService.tick()`. Each announcement is saved in the game's
  outbox in the same write as the change that caused it. The interface reads queued
  announcements with `undelivered()` and calls `mark_delivered()` once each one is posted, so
  nothing is lost to a crash, restart or network outage. The Discord bot does this every
  `FANTASYMTG_TICK_SECONDS`.
- **Stored format** is versioned (`core/serialization.py`); older databases are upgraded when
  they're loaded.
- **Money** is stored as integer cents throughout.
- **Layering is enforced** by `tests/test_architecture.py`, which fails if `core` imports an
  adapter, the presentation layer or Discord.

### Adding another interface (web, Slack, CLI…)

Build a `GameService` with any repository and price source, map your users and rooms to
`user_id` and `channel_key` strings, call the service methods, and run `tick()` on a timer.
Reuse `presentation.messages` if your interface displays Markdown text, or render the result
dataclasses yourself.

## Development

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/pytest
```

## License

MIT. See [LICENSE](LICENSE). The original game design is in [FantasyMTG.txt](FantasyMTG.txt).

Card data and prices come from [Scryfall](https://scryfall.com). This project is not affiliated with Scryfall, TCGplayer or Wizards of the Coast.
