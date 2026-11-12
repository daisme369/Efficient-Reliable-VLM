# Run the Thorax demo

The demo uses synthetic patients, a local SQLite database and simulated inference. Open `http://127.0.0.1:3100/login` after starting the stack.

## Windows

From the checkout, create the Python environment and build the frontend with Node.js 24 and pnpm.

```powershell
uv venv --python 3.12 .venv
uv pip sync --python .venv/Scripts/python.exe apps/api/requirements.lock
Push-Location src/thorax-web
pnpm install --frozen-lockfile
$env:THORAX_API_ORIGIN = 'http://127.0.0.1:8100'
pnpm build
Pop-Location
.\start-thorax.cmd
```

The first launch creates credentials in `.runtime-new-images/access.json`. Use `doctor@thorax.demo` with `doctor_password` or `admin@thorax.demo` with `admin_password`. Keep the console open while using the app.

To add the three CXR studies with five CC0 radiographs, run `.venv/Scripts/python.exe scripts/integration/fetch_public_radiographs.py` before restarting the demo. The downloader checks SHA-256 against the [source manifest](../scripts/integration/fixtures/public-radiographs/fixture.json). The repository contains synthetic default images and the manifest for the optional public images.

## Demo workflow

Sign in as a doctor. Open an assigned study, analyze the selected image, draw or edit a region, and ask about the whole image or the region. Review and save findings at Gate 1. Generate and review a draft, then approve the final report at Gate 2. The finalized report is read-only. The admin role manages doctor accounts, assignments and prepared fixture imports.

```powershell
.venv/Scripts/python.exe -m pytest apps/api/tests -q
.venv/Scripts/python.exe scripts/integration/check_http.py
```

The HTTP check creates temporary demo doctor accounts and exercises the mock analysis, region chat, approval and reporting flow. See [deployment](../infra/README.md) for the PostgreSQL Compose setup and [backend](../apps/api/README.md) for the real inference contract. Real inference requires the base MedGemma model, the team's LoRA adapter and a CV detector.
