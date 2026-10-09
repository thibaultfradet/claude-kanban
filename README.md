# Claude Kanban

**A local Kanban board that hands tickets to Claude Code agents, with a human reviewing every change.**

You write a ticket. A headless [Claude Code](https://docs.claude.com/en/docs/claude-code) agent picks it up and works on it in your project. You test the result and approve it before anything gets committed or pushed. It works across several projects at once, and everything runs on your own machine.

<!-- Screenshot: add an image of the board here. -->

## How it works

```
To do → In progress → Stand by (question) → To review → To commit → Done
```

1. **To do**: tickets wait here. The scheduler picks the oldest one for each project.
2. **In progress**: Claude Code runs headless in the project folder and follows the ticket plan. It commits small, focused steps on the current branch and never pushes. The live log streams into the ticket page.
3. **Stand by**: if the agent needs a decision, it stops and asks a question. You answer from the ticket page, and the same session resumes with your answer.
4. **To review**: the agent says it's done. You test the change yourself:
   - if something is wrong, send feedback with `/kanban-ticket` from Claude Code. It's appended to the ticket history and the ticket goes back to *To do* with the full context;
   - if it's good, move it to *To commit*.
5. **To commit**: a headless commit pass groups any leftover changes into clean commits. You press **Push** when you're ready.
6. **Done**: pushed.

Each project handles one ticket at a time, so agents never step on each other's changes. Different projects run in parallel.

## Requirements

- Python 3.9+
- [Claude Code](https://docs.claude.com/en/docs/claude-code) installed and logged in
- Git, with the projects you want to work on already cloned locally

## Getting started

```bash
git clone https://github.com/thibaultfradet/claude-kanban.git
cd claude-kanban
./run.sh
```

`run.sh` creates a virtualenv, installs dependencies and starts the board on **http://127.0.0.1:8787**. It listens on localhost only.

### Install the Claude Code commands

The repo ships two slash commands in [`claude-commands/`](claude-commands):

| Command | What it does |
|---|---|
| [`/kanban-ticket`](claude-commands/kanban-ticket.md) | Run from a Claude Code session in any project. Claude analyzes the code, asks clarifying questions, writes a detailed plan and creates the ticket on the board. It also sends feedback on an existing ticket. |
| [`/commit`](claude-commands/commit.md) | Groups changes into coherent commits with `<type>: <message>` messages. Used by the board's commit step, and handy on its own. |

Copy them into your user-level commands so they're available in every project:

```bash
mkdir -p ~/.claude/commands
cp claude-commands/*.md ~/.claude/commands/
```

## Creating a ticket

Open Claude Code (CLI or desktop) in the project you want to work on and run:

```
/kanban-ticket add a dark mode toggle to the settings page
```

Claude asks questions until the plan is unambiguous, because the agent that runs the ticket later can't ask you anything mid-run except through *Stand by*. The ticket then appears in *To do*, and the project is added to the board automatically.

You can also create tickets directly through the API:

```bash
curl -X POST http://127.0.0.1:8787/api/tickets \
  -H "Content-Type: application/json" \
  -d '{"project_name": "my-app", "project_path": "/path/to/my-app", "title": "Add dark mode", "plan": "..."}'
```

## Configuration

Set these environment variables before running `./run.sh`:

| Variable | Default | Purpose |
|---|---|---|
| `KANBAN_MODEL` | `sonnet` | Model used for ticket runs |
| `KANBAN_TICKET_TIMEOUT` | `1800` | Max duration of a single run, in seconds |
| `KANBAN_POLL_INTERVAL` | `20` | How often the scheduler looks for new tickets, in seconds |
| `KANBAN_CLAUDE_BIN` | `~/.local/bin/claude` | Path to the Claude Code binary |

## ⚠️ Safety

Agents run with `--permission-mode bypassPermissions`: they can read, write and run commands in the project folder without asking. That is what makes unattended runs possible, so:

- only point the board at projects you trust, ideally with a clean git state;
- review every change in *To review* before moving it on;
- pushing is always a manual step.

## Tech stack

Python, FastAPI, SQLModel (SQLite), Jinja2 templates, and server-sent events for the live board and logs.

> The board's interface is currently in French.
