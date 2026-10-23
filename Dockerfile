ARG BASE_IMAGE=agenthack/base:1.1.1
FROM ${BASE_IMAGE}
WORKDIR /app
USER root
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY --chown=agent:agent src /app/src
COPY --chown=agent:agent agent.py README.md /app/
RUN mkdir -p /output && chown agent:agent /output
ENV PYTHONPATH=/app/src RESEARCH_WORKSPACE_ROOT=/data RESEARCH_RESULT_ROOT=/output
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.agenthack.team_id="research-assistant"
LABEL org.agenthack.version="0.4.2-hybrid-v6"
LABEL org.agenthack.protocol="layered-d-v3-source-v1"
USER agent
EXPOSE 8080
CMD ["python", "agent.py"]

