# Phase 2 Electron + React Frontend Log

## What was built

Created a new top-level `electron/` app that replaces the legacy Tkinter UI with an Electron main process, secure preload bridge, and Vite + React renderer. The renderer centralizes FastAPI calls through `renderer/api/client.js` and uses a client-side log context to capture API activity.

## Screen list

1. Dashboard — backend health, quick totals, quick links, recent campaign rollups.
2. Campaigns — create/select campaigns, view status rollups, send only from verified sender domains.
3. Compose — HTML/plain text editor, sandboxed HTML preview, plain text preview, autograb personalization, validation, spam, placeholder, Jinja, and HTML-ratio indicators.
4. Contacts — localStorage recipient list with add/import/remove.
5. Analytics — selected campaign status rollups rendered as CSS bars.
6. Settings — backend URL display, default sender name, non-SMTP default toggle.
7. Logs — client-side API activity log with severity filters.
8. Domains — Phase 4 domain management: add wizard, DNS records, copy buttons, verify, rotate DKIM, DMARC policy change, delete, health history bars, verification warnings.

## Dev/build workflow

```bash
cd electron
npm install
npm run dev
```

`npm run dev` runs Vite on port 5173 and launches Electron after `wait-on` confirms the dev server is ready. The FastAPI backend must already be running at `http://127.0.0.1:8000`.

```bash
npm run build
```

`npm run build` runs `vite build` and `electron-builder`. Production Electron loads `electron/dist/index.html`.

## Backend endpoints consumed

- `GET /health`
- `POST /campaigns`
- `GET /campaigns/{id}`
- `POST /campaigns/{id}/send`
- `POST /compose/preview`
- `POST /compose/analyze`
- `GET /domains`
- `POST /domains`
- `GET /domains/{id}`
- `POST /domains/{id}/verify`
- `PATCH /domains/{id}/dmarc`
- `POST /domains/{id}/dkim/rotate`
- `DELETE /domains/{id}`
- `GET /domains/{id}/history`

## Dead Tkinter compose features removed

The Phase 2 Compose screen intentionally omits legacy Tkinter compose concepts such as attachment-encryption controls, per-line Tk variables, and desktop-widget-specific state. It focuses on HTML/text editing, backend preview personalization, validation warnings, placeholder/Jinja feedback, spam scoring, and HTML/text ratio visibility.

## Screenshot note

Screenshots require launching the Electron app locally with a graphical display. This CI/session environment has no display, so Electron was not launched here.

## Quality Gate

Met:

- Skeleton compiles structurally with Vite/Electron source layout.
- API client matches the documented backend endpoints.
- Compose renders HTML through a sandboxed iframe and plain text through a `<pre>` preview.
- Compose validation is wired to `/compose/analyze` with warnings, spam, Jinja, placeholders, and HTML-ratio UI.
- Campaign sending is UI-gated to verified domains and passes `html` plus `non_smtp_delivery` metadata.

Requires local GUI run:

- Actual Electron launch.
- Hot reload verification in a desktop window.
- Visual QA and screenshots.
