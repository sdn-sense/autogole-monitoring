FROM python:3.11-slim AS sources

# The combined all-sites page reuses SiteRM's diagram builder and the Archify renderer the SiteRM Frontend image pins.
ARG SITERM_REF=976541be3f7e3d09d932e9d9f76a569f3ac14912
ARG ARCHIFY_REF=f325d86230553e1dd15d3464324f1fcd01f02352
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git init -q /tmp/siterm && \
    git -C /tmp/siterm fetch -q --depth 1 https://github.com/sdn-sense/siterm "$SITERM_REF" && \
    git -C /tmp/siterm checkout -q FETCH_HEAD && \
    mkdir -p /opt/lib/SiteRMLibs && touch /opt/lib/SiteRMLibs/__init__.py && \
    cp /tmp/siterm/src/python/SiteRMLibs/ArchifyTopology.py /tmp/siterm/src/python/SiteRMLibs/ArchifyLayout.py \
       /tmp/siterm/src/python/SiteRMLibs/DefaultParams.py /opt/lib/SiteRMLibs/
RUN git init -q /tmp/archify && \
    git -C /tmp/archify fetch -q --depth 1 https://github.com/tt-a1i/archify "$ARCHIFY_REF" && \
    git -C /tmp/archify checkout -q FETCH_HEAD && \
    mkdir -p /opt/archify && mv /tmp/archify/archify /opt/archify/archify

FROM python:3.11-slim

# Debian's Node.js package (20 on the current python:3.11-slim); Archify needs Node.js >= 18.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/* && \
    pip3 install --no-cache-dir pyyaml

COPY --from=sources /opt/lib /opt/lib
COPY --from=sources /opt/archify /opt/archify
COPY topoproxy.py /opt/topoproxy.py

ENV PYTHONPATH=/opt/lib \
    SITERM_ARCHIFY_CLI=/opt/archify/archify/bin/archify.mjs

# Reads the token files and prometheus.yml (both mode 0644) as an unprivileged user.
USER 65534
EXPOSE 9098

CMD ["python3", "/opt/topoproxy.py"]
