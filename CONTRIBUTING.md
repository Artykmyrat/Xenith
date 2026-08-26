# Contributing to SkyPanel

Thanks for considering contributing to SkyPanel!

SkyPanel is a fork of [Marzban](https://github.com/Gozargah/Marzban) and is
licensed under the AGPL-3.0. By submitting a pull request you agree that your
contribution is distributed under that same license.

## Reporting issues

Include the following in your report:

- What you expected to happen and what actually happened.
- Server logs, or the error shown in the browser.
- Your Xray JSON config and relevant `.env` settings, with secrets censored.
- The versions of SkyPanel, Xray and Docker you are running.

## Submitting a pull request

Branch off `main`. If there is no open issue covering your change, prefer opening
one first so the approach can be discussed before you invest time in it.

## Project structure

```
.
├── app                      # Backend (FastAPI - Python)
│   └── dashboard            # Frontend (React - TypeScript)
├── cli                      # CLI (Typer - Python)
├── docs/upstream            # Original Marzban documentation, kept for reference
└── xray_api                 # Client for Xray's gRPC API
```

## Backend

FastAPI with SQLAlchemy as the ORM. Pydantic models live in `app/models`,
database models and queries in `app/db`, and Alembic migrations in
`app/db/migrations`. Any change to `app/db/models.py` needs a matching migration.

Note that the database schema stays compatible with Marzban so that existing
installations can migrate — do not rename tables or reorder existing revisions.

### Formatting

```bash
autopep8 <file> --max-line-length 120
```

## Frontend

Chakra UI is the component library; follow its conventions. Prefer cohesive,
single-purpose components, and favour readability over brevity.

The frontend is pre-built and served from `app/dashboard/build`. To rebuild,
run `npm install` in `app/dashboard`, delete the `build` directory and start the
backend again.

## CLI

Built with [Typer](https://typer.tiangolo.com/). Command code lives in `cli/`.
Regenerate its documentation with `typer-cli` installed:

```bash
PYTHONPATH=$(pwd) typer skypanel-cli.py utils docs --name "" --output ./cli/README.md
```

## Debug mode

Set `DEBUG=true` in `.env` and run `main.py`. Backend and frontend then run
separately with auto-reload. Install the npm packages first:

```bash
cd app/dashboard && npm install && cd ../..
```
