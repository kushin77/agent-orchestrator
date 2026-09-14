# Container image for the agent-orchestrator portal — the public web surface
# (ai.purebliss.app, infra/terraform/modules/web-surface).
#
# The portal is a self-contained, stdlib-only Python HTTP console (no npm, no
# node_modules, no build step). The only third-party runtime dependency is
# PyYAML. The image copies the repo checkout (portal/ + its imported tree:
# identity/, telemetry/, fleet/, infra/feature-flags/...) and serves
# `python3 -m portal.server.main` on 0.0.0.0:8080 — the Cloud Run default
# container port (the web-surface module does not override it).
#
# Built by infra/cloudbuild/web-image.yaml (the code-native image build);
# runtime secrets (the auth-gate JWKS mirror, the root-admin allowlist) are
# mounted/env-injected at apply time — never baked into the image (GR-6).

FROM python:3.12-slim

# Non-root runtime user (defense in depth; Cloud Run also sets this at the
# service level, but the image must not assume root).
RUN addgroup --system ao && adduser --system --ingroup ao ao

WORKDIR /app

# Copy the checkout first so cached layers track code changes, not deps.
COPY --chown=ao:ao . /app

# The only non-stdlib runtime import in portal/server/*.
RUN pip install --no-cache-dir PyYAML

USER ao

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Cloud Run sends SIGTERM; the stdlib http.server handles it cleanly.
EXPOSE 8080

CMD ["python3", "-m", "portal.server.main", "--host", "0.0.0.0", "--port", "8080"]
