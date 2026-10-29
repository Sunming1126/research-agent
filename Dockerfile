FROM agenthack/base:1.1.1
WORKDIR /app
USER root
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY --chown=agent:agent src /app/src
COPY --chown=agent:agent agent.py README.md /app/
RUN mkdir -p /output && chown agent:agent /output
ENV PYTHONPATH=/app/src RESEARCH_WORKSPACE_ROOT=/data RESEARCH_RESULT_ROOT=/output AGENT_VERSION=0.4.18-quality-20261029
LABEL org.agenthack.version="0.4.18-quality-20261029"
LABEL org.agenthack.prompt_version="20261027-v21-b-retrieval"
LABEL org.agenthack.protocol="layered-d-v3-source-v1"
USER agent
EXPOSE 8080
CMD ["python", "agent.py"]

