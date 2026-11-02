FROM python:3.11-slim

RUN pip3 install --no-cache-dir pyyaml

COPY topoproxy.py /opt/topoproxy.py

# Reads the token files and prometheus.yml (both mode 0644) as an unprivileged user.
USER 65534
EXPOSE 9098

CMD ["python3", "/opt/topoproxy.py"]
