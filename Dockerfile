# ridgeline runtime: the official Freqtrade image plus the few extras the
# panel and watchdog use. The repository itself is mounted at /ridgeline by
# docker-compose, so data, databases, logs and the audit trail live on the host.
FROM freqtradeorg/freqtrade:2026.8

# joblib 1.6 dropped a module Freqtrade's hyperopt imports
RUN pip install --no-cache-dir "joblib<1.6" httpx psutil

WORKDIR /ridgeline
ENTRYPOINT []
