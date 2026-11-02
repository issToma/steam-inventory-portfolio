# Contributing

Thanks for your interest! Issues and pull requests are welcome.

## Setup

```bash
uv sync
docker compose up -d postgres
```

## Before opening a pull request

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

## Ground rules

- **Be gentle with Steam.** All requests to Steam go through the shared rate-limited client.
  Do not add parallel requests, and do not lower the default request interval.
- **Tests never hit Steam.** Parsers are tested against small JSON fixtures in `tests/fixtures/`.
  Trim fixtures to the fields you need, and strip SteamIDs, profile links and other personal data.
- Code, comments and commit messages are in English.
