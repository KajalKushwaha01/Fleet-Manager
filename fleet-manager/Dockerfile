FROM python:3.12.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app.py /app/app.py
COPY scripts /app/scripts
COPY static /app/static
COPY templates /app/templates

RUN useradd --system --no-create-home --shell /usr/sbin/nologin fleet \
    && mkdir -p /data \
    && chown -R fleet:fleet /data /app

USER fleet

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "app:app"]
