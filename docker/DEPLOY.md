# Deploy the app on one server

`docker/compose.yaml` runs Thorax AI on one Linux server with three containers:

| Container | Role |
| --- | --- |
| `caddy` | HTTPS on ports 80 and 443; gets and renews the certificate itself |
| `web` | Next.js app (`docker/Dockerfile.web`) |
| `api` | FastAPI backend and SQLite (`docker/Dockerfile.api`) |

Only Caddy's ports are published. The database and uploaded images are in the `thorax-data` volume, and certificates are in `caddy-data`. The real model runs on a separate GPU server and is not part of this file.

Sizing: one API worker and SQLite. This setup is for a capstone demo with a few users, not production scale (see `src/thorax_api/README.md`, Limits).

## First start

1. **Server.** Create an AWS Lightsail Linux instance (Ubuntu 24.04, 2 GB plan) and attach a static IP. In its firewall, open TCP 80, TCP 443 and UDP 443. Install Docker Engine with the Compose plugin ([docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)). On a 1–2 GB server, add swap before the first build, because `next build` needs more memory:

   ```bash
   sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

2. **Domain.** Point a DNS `A` record at the static IP. A domain you own works, and so does a free DNS name that resolves to the IP. Sign-in requires HTTPS, so a bare IP address cannot be used.

3. **Settings.** Clone the repository on the server and create `docker/.env`. Git ignores this file; never commit it.

   ```bash
   THORAX_DOMAIN=thorax.example.org
   # Optional: the real model on the GPU server. Leave unset to use the mock AI.
   # THORAX_VLM_URL=http://<GPU server private IP>:8500
   # THORAX_VLM_KEY=<the model service's secret>
   ```

4. **Start.**

   ```bash
   docker compose -f docker/compose.yaml up -d --build --wait
   ```

   Caddy requests the certificate on the first start, which takes a few seconds.

5. **Accounts and demo data.** Type the passwords on the server; they never go into Git or the image.

   ```bash
   read -rs THORAX_DEMO_DOCTOR_PASSWORD && read -rs THORAX_DEMO_ADMIN_PASSWORD && export THORAX_DEMO_DOCTOR_PASSWORD THORAX_DEMO_ADMIN_PASSWORD
   docker compose -f docker/compose.yaml exec -e THORAX_DEMO_DOCTOR_PASSWORD -e THORAX_DEMO_ADMIN_PASSWORD api python -m thorax_api.accounts seed-demo
   # One more account (the password is prompted):
   docker compose -f docker/compose.yaml exec api python -m thorax_api.accounts create --email someone@example.org --name "Name" --role doctor
   ```

   Upload the demo X-rays through the app. Keep restricted research data, such as MIMIC, off this server.

## Run it

| Task | Command or step |
| --- | --- |
| Update to the latest code | `git pull && docker compose -f docker/compose.yaml up -d --build --wait`; data and sign-ins are kept |
| Status and logs | `docker compose -f docker/compose.yaml ps`, then `... logs api --tail 50` |
| Switch to the real model | Set `THORAX_VLM_URL` and `THORAX_VLM_KEY` in `docker/.env`, then run the update command. While the GPU server is stopped, AI requests fail as unavailable instead of falling back to mock answers, so unset both between demos. |
| Backups | Turn on Lightsail automatic daily snapshots for the instance; a snapshot includes the Docker volumes. To restore, create a new instance from a snapshot and move the static IP to it. |
| Stop | `docker compose -f docker/compose.yaml down`. Adding `-v` also deletes the database and images. |

## Try it locally

With Docker Desktop, run `THORAX_DOMAIN=localhost docker compose -f docker/compose.yaml up -d --build --wait` and open `https://localhost`. Caddy uses its own local certificate, so the browser shows a warning.

Checked locally on 18 November 2026:
- the build, health checks, HTTP-to-HTTPS redirect, demo seed and doctor sign-in
- the `Secure` session cookie, the worklist, opening a reading and loading its image
- an 8.7 MB upload through both proxies
- the sign-in limit, which still applies when the client forges `X-Forwarded-For`
- sign-ins and data surviving `down` and `up`

None of this has been run on AWS yet.
