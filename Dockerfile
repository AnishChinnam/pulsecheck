FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000
COPY pyproject.toml requirements.lock ./
COPY pulsecheck ./pulsecheck
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir -e . --no-deps && useradd --create-home pulsecheck
USER pulsecheck
EXPOSE 10000
CMD ["python", "-m", "pulsecheck.deploy"]
