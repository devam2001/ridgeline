# ridgeline runtime: the official Freqtrade image, the few extras the panel and
# watchdog use, and the ridgeline code itself. Releases publish it as
# ghcr.io/devam2001/ridgeline, so a server can pull it instead of building.
# docker-compose mounts user_data/ from the host: config, strategies, data,
# databases, logs and the audit trail stay there.
FROM freqtradeorg/freqtrade:2026.8

ARG VERSION=dev
LABEL org.opencontainers.image.title="ridgeline" \
      org.opencontainers.image.description="Freqtrade bot with paper trading, research gates, risk controls and reporting" \
      org.opencontainers.image.source="https://github.com/devam2001/ridgeline" \
      org.opencontainers.image.version="${VERSION}"

# joblib 1.6 dropped a module Freqtrade's hyperopt imports
RUN pip install --no-cache-dir "joblib<1.6" "httpx>=0.27" "psutil>=5.9"

WORKDIR /ridgeline
COPY panel/ panel/
COPY ridgeline/ ridgeline/
COPY scripts/ scripts/
# Defaults for a container started without the user_data mount.
COPY --chown=ftuser:ftuser user_data/ user_data/

ENTRYPOINT []
