FROM python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && if [ -n "$PIP_TRUSTED_HOST" ]; then \
        pip install --no-cache-dir --trusted-host "$PIP_TRUSTED_HOST" -r /app/requirements.txt; \
    else \
        pip install --no-cache-dir -r /app/requirements.txt; \
    fi

COPY . /app

RUN sed -i 's/\r$//' /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh

WORKDIR /app/blogsite

EXPOSE 8000

CMD ["/app/docker-entrypoint.sh"]
