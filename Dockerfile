# One container: the API serves the built portal on the same origin.
# Build:  docker build -t asdp .
# Run:    see docs/production.md (needs ASDP_DEV_MODE=false and the secrets listed there).

FROM node:22-alpine AS portal
WORKDIR /src
COPY apps/portal/package.json apps/portal/package-lock.json ./
RUN npm ci
COPY apps/portal/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ASDP_SERVE_PORTAL=true \
    ASDP_DATABASE_URL=sqlite:////data/asdp.db
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]>=3.1"
COPY services ./services
COPY config ./config
COPY playbooks ./playbooks
COPY docs ./docs
COPY --from=portal /src/dist ./apps/portal/dist
RUN useradd --system --uid 10001 asdp && mkdir /data && chown asdp /data
USER asdp
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=4).status == 200 else 1)"
# One worker on purpose: the scheduler and Jira reconciliation run inside the process.
CMD ["python", "-m", "uvicorn", "asdp.main:app_factory", "--factory", "--app-dir", "services/api", "--host", "0.0.0.0", "--port", "8000"]
