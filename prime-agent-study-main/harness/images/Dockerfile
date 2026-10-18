# Harness runtime image — long-lived container; agent is exec'd in, not CMD.
# Build from harness root:
#   docker build -f images/Dockerfile -t pi-harness:coding-v1 .
# Pin:
#   docker inspect --format='{{index .RepoDigests 0}}' pi-harness:coding-v1
FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl python3 \
    && rm -rf /var/lib/apt/lists/*

# Pi coding agent — version pinned by the image digest in the harness manifest.
RUN npm install -g @earendil-works/pi-coding-agent

WORKDIR /opt/harness
COPY package.json tsconfig.json ./
COPY src/extensions ./src/extensions
RUN npm install && npm run build

# Compiled extensions for Pi to load.
COPY dist/extensions /opt/harness/extensions/

WORKDIR /workspace

# sleep infinity: container hosts the session across turns; do not run the agent as PID 1.
# --init (docker run) is required so zombies from Pi subprocesses are reaped.
CMD ["sleep", "infinity"]
