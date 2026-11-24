#!/bin/bash
# Deploy or update Thorax AI on the EC2 app server (docker/DEPLOY.md, "AWS EC2 + RDS").
# Runs as root through Session Manager / Run Command. Settings come from Parameter Store (/thorax/*),
# so no password is typed, committed or passed on a command line.
set -euo pipefail
cd /opt/thorax
sudo -u ubuntu git pull --ff-only

region=ap-southeast-2
param() { aws ssm get-parameter --region "$region" --name "$1" --with-decryption --query Parameter.Value --output text; }
optional() { param "$1" 2>/dev/null || true; }

umask 077  # docker/.env holds the database password: readable by root only
mkdir -p docker/calibration docker/certs
chmod 755 docker/calibration docker/certs  # the api container runs as uid 10001, not root
# Amazon's CA bundle for RDS in this Region, so the app checks it is talking to the real database (sslmode=verify-full)
curl -fsSL "https://truststore.pki.rds.amazonaws.com/$region/$region-bundle.pem" -o docker/certs/rds-ca.pem
chmod 644 docker/certs/rds-ca.pem
calibration=$(optional /thorax/vlm/calibration)  # the "selectivity" part of M1's metrics.json, as JSON
if [ -n "$calibration" ]; then printf '%s' "$calibration" > docker/calibration/metrics.json; chmod 644 docker/calibration/metrics.json; fi
{
  echo "THORAX_DOMAIN=$(param /thorax/domain)"
  echo "THORAX_DATABASE_URL=postgresql://$(param /thorax/db/user):$(param /thorax/db/password)@$(param /thorax/db/host):5432/thorax?sslmode=verify-full&sslrootcert=/certs/rds-ca.pem"
  echo "THORAX_VLM_URL=$(optional /thorax/vlm/url)"
  echo "THORAX_VLM_KEY=$(optional /thorax/vlm/key)"
  echo "THORAX_VLM_MODEL=$(optional /thorax/vlm/model)"
  echo "THORAX_VLM_CALIBRATION=${calibration:+/calibration/metrics.json}"
} > docker/.env

docker compose -f docker/compose.yaml up -d --build --wait
docker compose -f docker/compose.yaml ps --format '{{.Name}} {{.Status}}'
