# Dev Orchestrator Shortcuts

These are the daily commands for using `orch` without typing long Hermes paths.

## First Commands

```bash
ssh orch
dhelp
ds
db
```

- `dhelp` prints this command set.
- `ds` refreshes and prints the current dev-run status.
- `db` prints the dashboard URL to open from your laptop browser.

The dashboard is served by `orch` over Tailscale. The server does not need a
browser window.

## Observe

```bash
ds
dw
dtail nullspaceton
dattach nullspaceton
```

- `ds`: refresh status and gbrain active-run page.
- `dw`: list tmux workers.
- `dtail <repo>`: read the latest pane output for `<repo>-codex`.
- `dattach <repo>`: attach to the tmux worker.

## Start Work

```bash
dnew ludeme "Continue the Ludeme campaign from the current plan"
```

This creates `ludeme-codex`, records a manager event, sends `codex --yolo`, and
writes a dispatch artifact. It refuses if `ludeme-codex` already exists.

To send a different first command:

```bash
dnew nullspaceton "Run the WATER slice" "codex --yolo"
```

`dstart` is a compatibility alias for `dnew`.

## Steer Work

```bash
dsteer nullspaceton "If auto gen is stalled, write closeout and stop cleanly."
```

This sends a message to `nullspaceton-codex` and records why the steer happened.
It refuses if `nullspaceton-codex` does not exist.

Use `DSESSION` when the session is not the default `<repo>-codex`:

```bash
DSESSION=ludeme-claude dnew ludeme "Design critique only" "claude"
```

## Close Out

```bash
dclose nullspaceton
```

This captures tmux output, git state, recent artifacts, manager events, and
gbrain closeout evidence.

## Change Directory

In an interactive shell:

```bash
dcd
dcd ludeme
```

- `dcd` jumps to `/srv/dev/repos`.
- `dcd <repo>` jumps to `/srv/dev/repos/<repo>`.

## Install Or Repair

From the Hermes repo:

```bash
cd /srv/dev/repos/hermes-agent
scripts/install-dev-shortcuts.sh
```

The installer writes executables to `~/.local/bin` and adds `/usr/local/bin`
symlinks when passwordless sudo is available.

## Rule

Use `dnew` and `dsteer` instead of raw `tmux` for orchestrated work. Raw tmux
does not create manager events, so the dashboard and closeout reports cannot
explain why the worker exists.
