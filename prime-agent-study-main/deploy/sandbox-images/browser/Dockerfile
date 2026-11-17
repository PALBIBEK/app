# The browser sandbox image: what the browser code mode's runtime needs in a session's
# sandbox (the githi-browser-docker-v1 profile), and what CI's browser-sandboxes job tests.
#
#   docker build -t githi-browser-sandbox:1.56.0 deploy/sandbox-images/browser
#
# Playwright's own image carries Chromium and every library it needs, but not the
# Playwright package for Python: install it, at the version the image's browsers are for.
# curl and git are a coding agent's everyday tools (and curl checks a sandbox's network).
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

RUN pip install --no-cache-dir --break-system-packages "playwright==1.56.0" \
 && apt-get update \
 && apt-get install -y --no-install-recommends curl git \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -c "import playwright.sync_api"

# Lightpanda (https://lightpanda.io), the lightweight engine of the sandbox-lightpanda browser,
# when built with --build-arg LIGHTPANDA=1 (its nightly binary, from its GitHub releases).
ARG LIGHTPANDA=0
RUN if [ "$LIGHTPANDA" = "1" ]; then \
      curl -fsSL -o /usr/local/bin/lightpanda https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux \
      && chmod +x /usr/local/bin/lightpanda; \
    fi

WORKDIR /workspace
