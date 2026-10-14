FROM python:3.13.16-slim-bookworm@sha256:5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed

COPY tools/requirements-reviewed-projection.txt tools/requirements-reviewed-runtime.txt /opt/wikikg/
RUN python -m pip install --no-cache-dir \
    -r /opt/wikikg/requirements-reviewed-projection.txt \
    -c /opt/wikikg/requirements-reviewed-runtime.txt

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
