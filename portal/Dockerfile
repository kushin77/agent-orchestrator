# Container image for the agent-orchestrator portal — the console/web surface.
#
# The portal is a self-contained, stdlib-only Python HTTP console (no npm, no
# node_modules, no build step). Its third-party runtime dependencies are PyYAML
# and cryptography. The image copies the repo checkout (portal/ + its imported
# tree: identity/, telemetry/, fleet/, infra/feature-flags/...) and serves
# `python3 -m portal.server.main` on 0.0.0.0:8080.
#
# WHY cryptography is here (measured, issue #801): the boot path is not only
# `portal/server/*`. `ConsoleSso.__init__` probes `identity.sso.tokens` at boot,
# which imports `identity.sso.saml`, which imports the RSA/X.509 helpers that
# `identity/sso/jose.py` defines ONLY when `cryptography` imports (it guards the
# whole stack with `try/except Exception`). Without it the image does not degrade
# — it exits 1 with `RuntimeError: portal SSO requires the merged identity/sso
# lane (issue #35); import failed`, i.e. the image could not serve its own CMD.
# Verify after any base-image bump:
#   docker run --rm --entrypoint python3 <image> -c \
#     "import cryptography, yaml; print(cryptography.__version__, yaml.__version__)"
#
# BUILT BY: infra/cloudbuild/web-image.yaml (the code-native Cloud Run image
# build — RETIRED route) and, for the live host, the shared-services run half
# lifting contrib/shared-services/agentconsole.compose.yml (docs/AGENTCONSOLE-HOSTING.md).
# Runtime secrets (the auth-gate JWKS mirror, the root-admin allowlist) are
# mounted/env-injected at apply time — never baked into the image (GR-6).

FROM python:3.12-slim

# Non-root runtime user (defense in depth; Cloud Run also sets this at the
# service level, but the image must not assume root).
RUN addgroup --system ao && adduser --system --ingroup ao ao

WORKDIR /app

# Copy the checkout first so cached layers track code changes, not deps.
COPY --chown=ao:ao . /app

# The non-stdlib runtime imports: portal/server/* needs PyYAML, and the boot
# path's identity/sso/* needs cryptography (see the header — omitting it makes
# the container exit 1 instead of starting).
RUN pip install --no-cache-dir PyYAML cryptography

USER ao

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# SIGTERM (container stop) is handled cleanly by the stdlib http.server.
EXPOSE 8080

CMD ["python3", "-m", "portal.server.main", "--host", "0.0.0.0", "--port", "8080"]
