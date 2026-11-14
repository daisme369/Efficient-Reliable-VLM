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

## Build and verify

```powershell
pnpm typecheck
pnpm build
pnpm start
```

The pinned dependencies supply the specified frontend stack and Lucide's SVG icon set. Next.js, React, TanStack Query, Tailwind, TypeScript, and the type packages use MIT licenses. Lucide uses ISC.

Set `THORAX_API_ORIGIN` before building or starting dev to proxy the browser's same-origin `/api/v1` calls to a compatible server. That server must implement the role, assignment, CSRF, session, ETag and provider contracts and accept the deployed web origin. The provider badge and provenance come from its responses. Supplied VLM and CV artifacts and production backend acceptance are separate integration work.
