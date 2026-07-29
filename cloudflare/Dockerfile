# Hermes Agent — CF Containers Production Image
FROM debian:13.4

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright
ENV HERMES_HOME=/opt/data HERMES_SCALE_TO_ZERO=true

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl python3 python-is-python3 python3-pip python3-venv \
    ripgrep ffmpeg gcc g++ make python3-dev libffi-dev procps git openssh-client xz-utils \
    libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install Node.js 22 LTS
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash --uid 10000 hermes
WORKDIR /opt/hermes

COPY pyproject.toml uv.lock setup.py MANIFEST.in ./
COPY hermes_constants.py hermes_bootstrap.py hermes_logging.py ./
COPY hermes_time.py hermes_state.py utils.py ./
# Also copy npm manifests for cached Playwright install
COPY package.json package-lock.json ./
RUN uv sync --frozen --no-dev

COPY --chown=hermes:hermes . .
RUN uv sync --frozen --all-extras

# Install Playwright browsers (Chromium) via project's npm setup
ENV npm_config_install_links=false
RUN npm install --prefer-offline --no-audit && \
    npx playwright install --with-deps chromium --only-shell && \
    npm cache clean --force

RUN install -d -o hermes -g hermes /opt/data
USER hermes

HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD curl -sf http://localhost:9119/api/status || exit 1

EXPOSE 9119
ENTRYPOINT ["python3", "-m", "gateway.run"]
CMD ["gateway", "run"]
