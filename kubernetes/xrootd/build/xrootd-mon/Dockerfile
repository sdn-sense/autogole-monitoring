FROM opensciencegrid/software-base:23-al8-release

RUN yum -y install xrootd-client gfal2-util-scripts gfal2-all gfal2-python3 python3-gunicorn python3-pyyaml && yum clean all

RUN pip3 install prometheus_client flask

RUN yum -y install voms-clients-cpp

RUN mkdir -p /opt/ && mkdir -p /srv/
ADD config/default/etc/supervisord.d/10-xrootd.conf /etc/supervisord.d/10-xrootd.conf
ADD config/default/opt/xroot-mon.py /opt/xroot-mon.py
ADD config/default/opt/http-server.py /opt/http-server.py

# Get latest CA's
RUN fetch-crl || echo "Supress warnings."

