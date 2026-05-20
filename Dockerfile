FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REPO_ROOT=/

WORKDIR /app

COPY aula-pipeline/backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY aula-pipeline/backend /app
COPY aula-pipeline/dashboard /dashboard
COPY agents /agents
COPY aulas/templates /aulas/templates
COPY livros/*.md /livros/
COPY livros/extrair_tema_tratado.py /livros/extrair_tema_tratado.py

EXPOSE 8080

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]
