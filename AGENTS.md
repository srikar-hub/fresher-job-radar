# Project instructions

## Scope and decisions

- Read `PROJECT_SPEC.md` before changing this project. Its **FINAL DEVELOPMENT DECISIONS** section overrides older requirements.
- Implement only the phase the user explicitly requests. Do not start later phases early.
- Keep the application usable with AI disabled. Do not add paid services, paid API dependencies, billing configuration, or card requirements.
- Keep credentials out of source control and logs. Use environment variables locally and GitHub/Cloudflare secrets in hosted environments.

## Python conventions

- Support Python 3.11 and newer.
- Put scanner code in `scanner/` and tests in `tests/`.
- Use type hints, small functions, and standard-library interfaces where practical.
- Mock network and external-service behavior in tests. Tests must not need credentials or make paid/network calls.
- Run the requested test suite after changes and report its result.

## Phase discipline

The initial scaffold may define configuration and architecture, but scanner execution, Telegram authentication/reading, classification, persistence APIs, Worker routes, bot commands, and notifications require their respective implementation phase to be requested.
