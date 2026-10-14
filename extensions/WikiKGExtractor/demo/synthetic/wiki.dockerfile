# Public, version-pinned inputs. The MediaWiki archive is verified by digest;
# that digest is an integrity pin, not a publisher signature.
FROM python:3.13.16-slim-bookworm@sha256:5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed AS python
FROM composer:2.8.3@sha256:4d417e98197a96c0be9fed0eb62b0b262807d1d1aed88c4cb5e081b1a8349955 AS composer

FROM php:8.3.14-apache-bookworm@sha256:7a3fd6ae9c9d240810bae95d3aaf894be4f0ec1a022b0a5f129d658a625e2ac2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcurl4-openssl-dev libicu-dev libonig-dev libsqlite3-dev libxml2-dev \
    libfreetype6-dev libjpeg62-turbo-dev libpng-dev sqlite3 ca-certificates curl git unzip \
 && docker-php-ext-configure gd --with-freetype --with-jpeg \
 && docker-php-ext-install gd intl mbstring mysqli pdo_sqlite \
 && rm -rf /var/lib/apt/lists/*
RUN docker-php-ext-install calendar
COPY --from=python /usr/local /usr/local
COPY --from=composer /usr/bin/composer /usr/local/bin/composer
RUN curl --http1.1 --fail --location --retry 3 --retry-all-errors --max-time 300 \
    https://releases.wikimedia.org/mediawiki/1.43/mediawiki-1.43.1.tar.gz -o /tmp/mediawiki.tar.gz \
 && echo '3c85aa9c4cd6c353c67804a3a58e7b79615d4141c3d66b101e5f3aeb40653d6c  /tmp/mediawiki.tar.gz' | sha256sum --check -
RUN mkdir -p /var/www/html/w \
 && tar -xzf /tmp/mediawiki.tar.gz --strip-components=1 -C /var/www/html/w \
 && rm /tmp/mediawiki.tar.gz
COPY demo/synthetic/mediawiki-composer.lock /var/www/html/w/composer.lock
RUN cd /var/www/html/w && composer install --no-interaction --no-progress --prefer-dist --no-scripts
# Frozen legacy path guards locate the checkout by this marker. No Git history,
# host configuration, or credentials are copied into the test image.
RUN mkdir /var/www/html/w/.git
COPY . /var/www/html/w/extensions/WikiKGExtractor
RUN python3 -m pip install --no-cache-dir \
    -r /var/www/html/w/extensions/WikiKGExtractor/tools/requirements-reviewed-projection.txt \
    -c /var/www/html/w/extensions/WikiKGExtractor/tools/requirements-reviewed-runtime.txt
RUN echo 'ServerName localhost' >> /etc/apache2/apache2.conf \
 && sed -i 's/^export APACHE_RUN_USER=.*/export APACHE_RUN_USER=${APACHE_RUN_USER:-www-data}/; s/^export APACHE_RUN_GROUP=.*/export APACHE_RUN_GROUP=${APACHE_RUN_GROUP:-www-data}/' /etc/apache2/envvars \
 && a2enmod rewrite
ARG WIKIKG_UID=1000
ARG WIKIKG_GID=1000
RUN getent group "$WIKIKG_GID" >/dev/null || groupadd --gid "$WIKIKG_GID" wikikg-synthetic
RUN getent passwd "$WIKIKG_UID" >/dev/null || useradd --no-create-home --no-log-init \
    --uid "$WIKIKG_UID" --gid "$WIKIKG_GID" --shell /usr/sbin/nologin wikikg-synthetic
ENV MW_INSTALL_PATH=/var/www/html/w DOCUMENT_ROOT=/var/www/html \
    MW_CONFIG_FILE=/private/wikikg/LocalSettings.synthetic.php \
    WIKIKG_SYNTHETIC=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /var/www/html/w
