FROM opensciencegrid/software-base:23-al8-release

RUN yum -y install voms-clients python3-pyyaml && yum clean all

RUN pip3 install kubernetes

RUN mkdir -p /etc/grid-security/vomsdir/cms/ && mkdir -p /etc/vomses/

COPY files/etc/vomses /etc/vomses/
COPY files/etc/grid-security/vomsdir/cms/lcg-voms2.cern.ch.lsc /etc/grid-security/vomsdir/cms/lcg-voms2.cern.ch.lsc

RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Add auto renewal script
ADD files/etc/supervisord.d/10-certrenew.conf /etc/supervisord.d/
ADD files/renewal.sh /
# Get latest CA's
RUN fetch-crl || echo "Supress warnings."

