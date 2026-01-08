FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/sense-o-py-client
RUN pip3 install --no-cache-dir --upgrade setuptools wheel && \
    git clone https://github.com/sdn-sense/sense-o-py-client.git . && \
    pip3 install --no-cache-dir -r requirements.txt && pip3 install --no-cache-dir .

COPY oidcgen.py /opt/oidcgen.py

CMD ["python3", "/opt/oidcgen.py"]
