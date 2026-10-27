set dotenv-load

service_name := "reporta-cdmx"
project_id := "daring-pilot-512916-i3"
default_region := "us-central1"

# List available recipes
default:
    @just --list

# Run the web server locally with uv
run port="8000":
    PORT={{port}} uv run reporta_cdmx.py

# Analyze a local image from the CLI
cli image:
    uv run reporta_cdmx.py {{image}}

# Test analyzing the sample pothole (bache) image
test-bache:
    uv run reporta_cdmx.py examples/bache.webp

# Test analyzing the sample water leak (fuga) image
test-fuga:
    uv run reporta_cdmx.py examples/fuga.jpg

# Test analyzing the sample broken streetlight (luminaria) image
test-luminaria:
    uv run reporta_cdmx.py examples/luminaria.jpg

# Build the Docker container locally
docker-build:
    docker build -t {{service_name}} .

# Run the Docker container locally
docker-run port="8000":
    docker run --rm -it -p {{port}}:{{port}} -e PORT={{port}} --env-file .env {{service_name}}

# Deploy to Google Cloud Run (automatically sets GEMINI_API_KEY if present in .env/env)
deploy project=project_id region=default_region:
    #!/usr/bin/env bash
    set -euo pipefail
    EXTRA_FLAGS=()
    if [ -n "${GEMINI_API_KEY:-}" ]; then
        EXTRA_FLAGS=(--set-env-vars "GEMINI_API_KEY=${GEMINI_API_KEY}")
    fi
    gcloud run deploy {{service_name}} \
        --project {{project}} \
        --source . \
        --region {{region}} \
        --allow-unauthenticated \
        --min-instances 0 \
        --max-instances 2 \
        --memory 512Mi \
        --cpu 1 \
        "${EXTRA_FLAGS[@]}"

# Stream logs from the deployed Cloud Run service
logs project=project_id region=default_region:
    gcloud run services logs tail {{service_name}} --project={{project}} --region={{region}}

# Show the public HTTPS URL of the deployed Cloud Run service
url project=project_id region=default_region:
    @gcloud run services describe {{service_name}} --project={{project}} --region={{region}} --format='value(status.url)'

# Clean temporary files, caches, and generated test tickets
clean:
    rm -rf __pycache__ examples/*.suac.json *.suac.json
