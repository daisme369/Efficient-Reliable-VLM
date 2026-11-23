# Thorax AI frontend

The academic chest X-ray reading workspace lives here as a standalone Next.js App Router app. It implements the five screens and MUST workflow from `docs/spec.md` and `docs/frontend_des.md` against the REST contract in `docs/implementation_plan.md`.

## Run the synthetic demo

Use Node.js 24 and pnpm 11. From this directory, copy `.env.example` to `.env.local`, then set different doctor and admin passwords of 12–72 UTF-8 bytes. The initial accounts use `doctor@thorax.demo`, `doctor2@thorax.demo` and `admin@thorax.demo`. Both demo doctors use the configured doctor password. Passwords are seeded when a fresh state file is created.

```powershell
Copy-Item .env.example .env.local
# Set THORAX_DEMO_DOCTOR_PASSWORD and THORAX_DEMO_ADMIN_PASSWORD in .env.local.
pnpm install --frozen-lockfile --ignore-scripts
pnpm dev
```

Open `http://127.0.0.1:3000/login` at a browser width of at least 1280 CSS pixels. The primary doctor has a shared synthetic study under patient code `PX-1042`. Open the study and analyze the selected image to begin. The admin can inspect or reimport the prepared `thorax-demo-v1` fixture.

The local demo keeps synthetic state under `.thorax-demo/state.json`. Set `THORAX_DEMO_STATE_PATH` to an unused absolute path to start an independent fixture. The demo uses a single local server process and deterministic mock AI responses. The radiograph PNGs are generated synthetic phantoms. The app does not use a restricted dataset, contact an external AI provider in mock mode, or retain clinical data in browser storage.

## Run against the Thorax backend

The local demo API above is an in-process mock. The real backend in `src/thorax_api` (FastAPI and SQLite) implements the same contract plus the annotator role, labeling, the full administrator console, self-service password changes and the AI pause switch. See `src/thorax_api/README.md`.

```powershell
# Once, from the repository root: create accounts and synthetic data in thorax.sqlite3. Re-running keeps existing data.
Get-Content src\thorax-web\.env.local | % { if ($_ -match '^(\w+)=(.+)$') { Set-Item "env:$($matches[1])" $matches[2] } }
$env:PYTHONPATH='src'; python -m thorax_api.accounts seed-demo

# Every time: start the API on port 8000 (repository root), then the web app (src\thorax-web) in another terminal.
python scripts/serve_api.py
pnpm dev
```

For `pnpm dev` to use the backend, set `THORAX_API_ORIGIN=http://127.0.0.1:8000` in `.env.local`. Leave it empty to use the local demo API instead. Restart `pnpm dev` after changing it.

Set `THORAX_DEMO_ANNOTATOR_PASSWORD` in `.env.local` to create `annotator@thorax.demo` and `annotator2@thorax.demo`. Run only one Next.js server per folder: two servers share `.next` and break each other.

| Feature | Local demo API | Thorax backend |
| --- | --- | --- |
| Doctor reading workflow | Yes | Yes, stored in SQLite |
| Labeling (annotator role) | No | Yes |
| Admin: users of every role, AI monitoring and pause, labeling oversight, activity log | No | Yes |
| Admin: mock fixtures | Yes | No |
| Change your own password | No | Yes |
| AI answers | Fixed demo answers | Fixed demo answers (the trained model is not connected yet) |

## Build and verify

```powershell
pnpm typecheck
pnpm build
pnpm start
```

The pinned dependencies supply the specified frontend stack and Lucide's SVG icon set. Next.js, React, TanStack Query, Tailwind, TypeScript, and the type packages use MIT licenses. Lucide uses ISC.

Set `THORAX_API_ORIGIN` before building or starting dev to proxy the browser's same-origin `/api/v1` calls to a compatible server. That server must implement the role, assignment, CSRF, session, ETag and provider contracts and accept the deployed web origin. The provider badge and provenance come from its responses. Supplied VLM and CV artifacts and production backend acceptance are separate integration work.
