# Deploy the app on AWS

`docker/compose.yaml` runs Thorax AI on one Linux server with three containers:

| Container | Role |
| --- | --- |
| `caddy` | HTTPS on ports 80 and 443; gets and renews the certificate itself |
| `web` | Next.js app (`docker/Dockerfile.web`) |
| `api` | FastAPI backend and SQLite (`docker/Dockerfile.api`) |

Only Caddy's ports are published. The database and uploaded images are in the `thorax-data` volume, and certificates are in `caddy-data`. The real model runs on a separate GPU server and is not part of this file.

Sizing: one API worker and SQLite. This setup is for a capstone demo with a few users, not production scale (see `src/thorax_api/README.md`, Limits).

## From sign-up to a running app

The whole path takes about 1–1.5 hours, mostly waiting. Use a laptop with Chrome. The AWS console layout changes often, so button names may differ slightly from these steps.

### 1. Create the AWS account (15 min)

1. Go to **aws.amazon.com** and click **Create an AWS Account**.
2. Enter your email and an account name such as `thorax-capstone`. Verify the email with the code AWS sends, then set a password.
3. Contact info: choose **Personal** and fill in your details.
4. Add a **debit or credit card**. AWS charges about $1 to verify it and refunds it.
5. Verify your phone with an SMS code.
6. When asked to choose a plan, pick **Paid plan**. New accounts still get $100–200 in credits, and the Paid plan allows the GPU server later. The budget alert in step 2 protects you from surprise bills.

### 2. Protect the account (5 min)

1. Search **IAM** in the top search bar, then add MFA for the root user with an authenticator app on your phone.
2. Search **Budgets** and click **Create budget**, then choose the **Monthly cost budget** template. Set the amount to **$10** and enter your email. AWS will email you if you go over.

### 3. Create the server (10 min)

1. Search **Lightsail** and open it.
2. Click **Create instance**:
   - **Region:** Singapore (ap-southeast-1), the closest to Vietnam.
   - **Platform:** Linux/Unix, then **OS only**, then **Ubuntu 24.04 LTS**.
   - **Plan:** the **2 GB** plan that includes public IPv4.
   - **Name:** `thorax`, then click **Create instance**.
3. Wait until it says **Running**.
4. Open the **Networking** tab, click **Create static IP** and attach it to `thorax`. Write down the IP.
5. Still under **Networking**, in the **IPv4 Firewall** section, click **Add rule**, choose **HTTPS** (TCP 443) and save. SSH (22) and HTTP (80) are already open.

### 4. Get a free domain (5 min)

Sign-in only works over HTTPS, which needs a domain name; a bare IP address cannot be used.

1. Go to **duckdns.org** and sign in with Google or GitHub.
2. Type a subdomain such as `thorax-capstone` and click **add domain**.
3. Put your **static IP** in the "current ip" box and click **update ip**.

Your address is now `thorax-capstone.duckdns.org`. A domain you own also works: point its DNS `A` record at the static IP.

### 5. Connect to the server

In Lightsail, open your instance and click **Connect using SSH**. A terminal opens in the browser. Type every command below into that terminal.

### 6. Install Docker (5 min)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
```

Close the terminal window and click **Connect using SSH** again, so the Docker permission takes effect. Then add swap, because `next build` needs more memory than a 2 GB server has free:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 7. Download the code (2 min)

```bash
git clone -b vietanh https://github.com/daisme369/Efficient-Reliable-VLM.git
cd Efficient-Reliable-VLM
```

If the repository is **private**, Git asks for a username and password:

- **Username:** your GitHub username.
- **Password:** a token, not your real password. On GitHub, go to Settings → Developer settings → Personal access tokens → Fine-grained tokens. Create one with **read-only Contents** access to this repository and paste it.
- The repository owner may have to grant you access first.

### 8. Start the app (10–15 min the first time)

Replace the domain with yours. `docker/.env` is ignored by Git; never commit it.

```bash
echo "THORAX_DOMAIN=thorax-capstone.duckdns.org" > docker/.env
docker compose -f docker/compose.yaml up -d --build --wait
```

Wait until you see three lines that end in **Healthy**. Caddy requests the certificate on the first start, which takes a few seconds.

### 9. Create the accounts and demo data

```bash
read -rsp "Doctor password: " THORAX_DEMO_DOCTOR_PASSWORD; echo
read -rsp "Admin password: " THORAX_DEMO_ADMIN_PASSWORD; echo
export THORAX_DEMO_DOCTOR_PASSWORD THORAX_DEMO_ADMIN_PASSWORD
docker compose -f docker/compose.yaml exec -e THORAX_DEMO_DOCTOR_PASSWORD -e THORAX_DEMO_ADMIN_PASSWORD api python -m thorax_api.accounts seed-demo
```

Use passwords of at least 12 characters. The typing stays invisible, which is normal. The passwords never go into Git or the image.

To add one more account (the password is prompted):

```bash
docker compose -f docker/compose.yaml exec api python -m thorax_api.accounts create --email someone@example.org --name "Name" --role doctor
```

Upload the demo X-rays through the app. Keep restricted research data, such as MIMIC, off this server.

### 10. Open it

Open `https://<your domain>` and sign in:

- Doctor: `doctor@thorax.demo`
- Admin: `admin@thorax.demo`

### 11. Turn on backups

In Lightsail, open your instance, go to the **Snapshots** tab and turn on **Automatic snapshots**. A snapshot includes the Docker volumes. To restore, create a new instance from a snapshot and move the static IP to it.

## Run it

| Task | Command or step |
| --- | --- |
| Update to the latest code | `cd Efficient-Reliable-VLM && git pull && docker compose -f docker/compose.yaml up -d --build --wait`; data and sign-ins are kept |
| Status and logs | `docker compose -f docker/compose.yaml ps`, then `docker compose -f docker/compose.yaml logs api --tail 50` |
| Switch to the real model | Add `THORAX_VLM_URL=http://<GPU server private IP>:8500` and `THORAX_VLM_KEY=<the model service's secret>` to `docker/.env`, then run the update command. While the GPU server is stopped, AI requests fail as unavailable instead of falling back to mock answers, so remove both lines between demos. |
| Stop | `docker compose -f docker/compose.yaml down`. Adding `-v` also deletes the database and images. |
| End of the project | Lightsail bills even while the server is stopped. **Delete** the instance and the static IP to stop the charges. |

## Try it locally

With Docker Desktop, run `THORAX_DOMAIN=localhost docker compose -f docker/compose.yaml up -d --build --wait` and open `https://localhost`. Caddy uses its own local certificate, so the browser shows a warning.

Checked locally on 18 November 2026:
- the build, health checks, HTTP-to-HTTPS redirect, demo seed and doctor sign-in
- the `Secure` session cookie, the worklist, opening a reading and loading its image
- an 8.7 MB upload through both proxies
- the sign-in limit, which still applies when the client forges `X-Forwarded-For`
- sign-ins and data surviving `down` and `up`

None of this has been run on AWS yet.
