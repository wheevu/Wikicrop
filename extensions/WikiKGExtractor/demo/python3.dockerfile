FROM python:3.11.8-slim-bookworm AS python

FROM docker-registry.wikimedia.org/dev/bookworm-php83-fpm:1.0.0

COPY --from=python /usr/local /usr/local

RUN /usr/local/bin/python3 --version
