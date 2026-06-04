FROM python:3.11-slim

RUN useradd --create-home --shell /usr/sbin/nologin runtime
WORKDIR /var/task

COPY runtime/python311/runner.py /opt/runtime/runner.py

USER runtime
ENTRYPOINT ["python", "/opt/runtime/runner.py"]
