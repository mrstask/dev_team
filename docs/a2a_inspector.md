# A2A Inspector

This repo now includes a local A2A gateway that lets you inspect Dev Team tasks with the official external A2A Inspector instead of a custom viewer.

The Inspector UI is a separate application from this repo:

- Repo: [a2aproject/a2a-inspector](https://github.com/a2aproject/a2a-inspector)
- Official local URL after startup: `http://127.0.0.1:5001`

## Start the gateway

Run the gateway from the project environment:

```bash
python main.py a2a-server
```

Defaults:

- Agent card: `http://127.0.0.1:5556/.well-known/agent.json`
- RPC URL: `http://127.0.0.1:5556/a2a`

You can override the bind address:

```bash
python main.py a2a-server --host 127.0.0.1 --port 5556
```

## Recommended workflow

1. Start the normal event loop in this repo:

```bash
python main.py run
```

2. Start the A2A gateway in another terminal:

```bash
python main.py a2a-server
```

3. Start the official Inspector app from its own checkout:

```bash
git clone https://github.com/a2aproject/a2a-inspector.git
cd a2a-inspector
uv sync
cd frontend && npm install && cd ..
```

In one terminal:

```bash
cd frontend
npm run build -- --watch
```

In a second terminal:

```bash
cd backend
uv run app.py
```

4. Open `http://127.0.0.1:5001` and connect the Inspector to the gateway base URL or agent card URL.

## What the gateway exposes

- `message/send` creates a new dashboard task in `architect + action:todo`
- `tasks/get` returns the current task state plus recent inter-agent handoffs
- Task artifacts include:
  - communication history captured from the pipeline
  - persisted task context files like `research.json`, `architect.json`, `developer.json`, `testing.json`, and `feedback.json` when present

## Notes

- The gateway uses dashboard tasks as the system of record.
- Communication history is stored locally in [`_a2a/messages.jsonl`](/Users/stanislavlazarenko/Projects/test_projects/dev_team/_a2a/messages.jsonl).
- If the event loop is not running, tasks created through A2A Inspector will be queued but not processed.
- Connecting the Inspector to `http://127.0.0.1:5556` or `http://127.0.0.1:5556/.well-known/agent.json` should both work with the current gateway.
