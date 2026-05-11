# Nautilus Trading — Platform Split

This repo contains the Nautilus Polymarket Whale Follower trading system, adapted to run on **both Linux (1700) and macOS (Mac failover)** from the same git repository.

## Architecture

```
nautilus-trading/
├── platform/
│   ├── mac/         ← launchd plist files (macOS)
│   └── linux/       ← systemd .service files (Linux/1700)
├── deploy-mac.sh    ← Install macOS plists → ~/Library/LaunchAgents/
└── deploy-linux.sh  ← Install Linux services → ~/.config/systemd/user/
```

## Platform Isolation

- **macOS launchd** will NOT process `.service` files (Linux format)
- **Linux systemd** will NOT process `.plist` files (macOS format)

Both platforms can safely have both directories — the OS simply ignores the wrong format.

## Deployment

### On Mac (failover)
```bash
cd ~/workspace/nautilus-trading
bash deploy-mac.sh
```

### On 1700 (primary)
```bash
cd ~/workspace/nautilus-trading
bash deploy-linux.sh
```

## Shared Components

Both platforms share:
- `components/` — trading logic
- `scripts/` — research, validation, grading pipelines
- `pipeline/` — data pipeline
- `run_paper.py` — paper trading executor
- `dashboard.py` — web dashboard

## Service Files

### macOS (launchd plists in `platform/mac/`)
- `com.nautilus.autoresearch.plist`
- `com.nautilus.backtest-grader.plist`
- `com.nautilus.backup-configs.plist`
- `com.nautilus.backup-trades.plist`
- `com.nautilus.dashboard.plist`
- `com.nautilus.jailbreak.plist`
- `com.nautilus.killswitch.plist`
- `com.nautilus.paper-trading.plist`
- `com.nautilus.paper.plist`
- `com.nautilus.signal-gap-monitor.plist`
- `com.nautilus.sports-deep.plist`
- `com.nautilus.sports-scan.plist`
- `com.nautilus.sports-whales.plist`
- `com.nautilus.strategy-daily.plist`
- `com.nautilus.strategy-edges.plist`
- `com.nautilus.strategy-ineff.plist`
- `com.nautilus.strategy-strategies.plist`
- `com.nautilus.validation-live.plist`
- `com.nautilus.validator.plist`
- `com.nautilus.watchdog.plist`

### Linux (systemd services in `platform/linux/`)
- `dashboard.service`
- `nautilus-live.service`
- `nautilus-paper.service`

## Failover

Mac is configured as a warm failover for 1700. If 1700 goes down:
1. Mac's paper trading and dashboard are already running
2. All cron jobs are replicated via launchd
3. To promote Mac to primary: update Polymarket API keys and remove paper-only guard
