#!/bin/bash
# Set up and start the M1 model server on the GPU host (docker/DEPLOY.md, "Model server").
# Runs as root through Run Command on an AWS Deep Learning Base GPU AMI (NVIDIA driver, Docker, NVIDIA container toolkit).
# The first run merges the M1 adapter into MedGemma once (about 10 minutes); later runs only start vLLM.
# Needs the instance role thorax-gpu: read s3://<bucket>/models/* and the /thorax/hf/token and /thorax/vlm/key parameters.
set -euo pipefail
region=ap-southeast-2
bucket=thorax-files-637804394917
repo=https://github.com/daisme369/Efficient-Reliable-VLM.git
commit=${THORAX_COMMIT:-vietanh}
image=vllm/vllm-openai:v0.33.0@sha256:d825baf9ce4b1b6bfc5ecc7b79880c7e6748c65a72d697ce3d47607aafa84c6e
merged=/models/medgemma-m1
param() { aws ssm get-parameter --region "$region" --name "$1" --with-decryption --query Parameter.Value --output text; }

mkdir -p /models
if [ ! -f "$merged/thorax_merge.json" ]; then
  work=$(mktemp -d)
  aws s3 sync --only-show-errors --region "$region" "s3://$bucket/models/m1-v2/" "$work/m1"  # adapter/ and config.json
  git clone --quiet --depth 1 --branch "$commit" "$repo" "$work/code"
  # Merge with the library versions M1 was trained with (requirements/medgemma-cu128.txt), so the adapter's
  # processor and chat-template checks match; vLLM's own versions are used only for serving.
  HF_TOKEN=$(param /thorax/hf/token) docker run --rm -e HF_TOKEN -e HF_HOME=/models/hf -e PYTHONPATH=/code/src \
    -v /models:/models -v "$work/code:/code:ro" -v "$work/m1:/m1:ro" --entrypoint bash "$image" -c "
      set -e
      pip install --quiet transformers==5.19.0 peft==0.21.2 accelerate==1.15.0 huggingface-hub==2.2.0 safetensors==0.8.0
      python /code/scripts/gpu/merge_m1_adapter.py --config /m1/config.json --adapter /m1/adapter --out $merged"
  rm -rf /models/hf "$work"  # the merged copy is all vLLM needs; drop the 8 GB download cache and the token's use
fi

name=$(python3 -c "import json; print(json.load(open('$merged/thorax_merge.json'))['served_model_name'])")
docker rm -f thorax-vllm >/dev/null 2>&1 || true
# Port 8000 is reachable only from the app server (security group thorax-gpu); every request needs the API key.
VLLM_API_KEY=$(param /thorax/vlm/key) docker run -d --name thorax-vllm --restart unless-stopped --gpus all \
  -p 8000:8000 -v /models:/models:ro -e VLLM_API_KEY "$image" \
  --model "$merged" --served-model-name "$name" --dtype bfloat16 --max-model-len 4096 --limit-mm-per-prompt '{"image": 1}' >/dev/null
for _ in $(seq 1 60); do
  if curl -fs http://127.0.0.1:8000/health >/dev/null; then echo "vLLM ready, model $name"; exit 0; fi
  sleep 10
done
docker logs --tail 40 thorax-vllm
echo "vLLM did not become ready in 10 minutes" >&2
exit 1
