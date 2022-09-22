#!/usr/bin/env python3
import os
import socket
import copy
import tempfile
import shutil
from yaml import safe_load as yload
from yaml import safe_dump as ydump
from git import Repo

# STATE - Will Query FE (/<SITENAME>/sitefe/json/frontend/metrics)
# and get all services states of that FE and Agent's registered to it.
STATE_SCRAPE = {'job_name': 'WILLBEREPLACEDBYCODE',
                'scrape_interval': '30s',
                'static_configs': [{'targets': []}],
                'scheme': 'https',
                'metrics_path': 'WILLBEREPLACEDBYCODE',
                'tls_config': {'cert_file': '/etc/siterm-cert.pem',
                               'key_file': '/etc/siterm-privkey.pem',
                               'insecure_skip_verify': True},
                'relabel_configs': [{'source_labels': ['__address__'],
                                     'target_label': 'sitename',
                                     'replacement': 'WILLBEREPLACEDBYCODE'},
                                    {'source_labels': ['__address__'],
                                     'target_label': 'software',
                                     'replacement': 'WILLBEREPLACEDBYCODE'}]}

# HTTPS - Will query https endpoint of FE and will check that it returns
# 2XX return code and also check certificate validity
# This uses blackbox exporter and we need to relabel config and use localhost to query
# remote endpoints
HTTPS_SCRAPE = {'job_name': 'WILLBEREPLACEDBYCODE',
                'metrics_path': '/probe',
                'params':{'module': ['WILLBEREPLACEDBYCODE']},
                'static_configs':[{'targets': []}],
                'relabel_configs':[{'source_labels': ['__address__'],
                                    'target_label': 'sitename',
                                    'replacement': 'WILLBEREPLACEDBYCODE'},
                                   {'source_labels': ['__address__'],
                                    'target_label': 'software',
                                    'replacement': 'WILLBEREPLACEDBYCODE'},
                                   {'source_labels': ['__address__'],
                                    'target_label': '__param_target'},
                                   {'source_labels': ['__param_target'],
                                    'target_label': 'instance'},
                                   {'target_label': '__address__',
                                    'replacement': 'prometheus-blackbox-exporter-service:9115'}]}


# ICMP - Will ping FE endpoint and get RTT
# v6 only if DNS Replies with v6 record
ICMP_SCRAPE = {'job_name': 'WILLBEREPLACEDBYCODE',
                'metrics_path': '/probe',
                'params':{'module': ['WILLBEREPLACEDBYCODE']},
                'static_configs':[{'targets': []}],
                'relabel_configs':[{'source_labels': ['__address__'],
                                    'target_label': 'sitename',
                                    'replacement': 'WILLBEREPLACEDBYCODE'},
                                   {'source_labels': ['__address__'],
                                    'target_label': 'software',
                                    'replacement': 'WILLBEREPLACEDBYCODE'},
                                   {'source_labels': ['__address__'],
                                    'target_label': '__param_target'},
                                   {'source_labels': ['__param_target'],
                                    'target_label': 'instance'},
                                   {'target_label': '__address__',
                                    'replacement': 'prometheus-blackbox-exporter-service:9115'}]}

# If Agent or FE has:
# general:
#   node_exporter: <URL>
# It will add that to prometheus config and will scrape node exporter endpoint
NODE_EXPORTER_SCRAPE = {'job_name': 'WILLBEREPLACEDBYCODE',
                        'static_configs': [{'targets': []}],
                        'relabel_configs': [{'source_labels': ['__address__'],
                                            'target_label': 'sitename',
                                            'replacement': 'WILLBEREPLACEDBYCODE'},
                                            {'source_labels': ['__address__'],
                                            'target_label': 'software',
                                            'replacement': 'WILLBEREPLACEDBYCODE'}]}


# ===================================================================================
#                   NETWORK_RM CONFIGS
# ===================================================================================
# HTTPS - Will query https endpoint of FE and will check that it returns
# 2XX return code and also check certificate validity
# This uses blackbox exporter and we need to relabel config and use localhost to query
# remote endpoints
# In case it is https, it will use https_v[46]_network_2xx module of blackbox (v6 only if DNS Replies with v6 record)
# In case it is http, it will use http_v[46]_network_2xx module of blackbox (v6 only if DNS Replies with v6 record)
HTTPS_SCRAPE_NRM = {'job_name': 'WILLBEREPLACEDBYCODE',
                   'metrics_path': '/probe',
                   'params':{'module': ['WILLBEREPLACEDBYCODE']},
                   'static_configs':[{'targets': []}],
                   'relabel_configs':[{'source_labels': ['__address__'],
                                       'target_label': 'sitename',
                                       'replacement': 'WILLBEREPLACEDBYCODE'},
                                      {'source_labels': ['__address__'],
                                       'target_label': 'software',
                                       'replacement': 'WILLBEREPLACEDBYCODE'},
                                      {'source_labels': ['__address__'],
                                       'target_label': 'service',
                                       'replacement': 'WILLBEREPLACEDBYCODE'},
                                      {'source_labels': ['__address__'],
                                       'target_label': '__param_target'},
                                      {'source_labels': ['__param_target'],
                                       'target_label': 'instance'},
                                      {'target_label': '__address__',
                                       'replacement': 'prometheus-blackbox-exporter-service:9115'}]}

# Seems OpenNSA requires renegotiation - which should be already unsupported.
# Comment from: https://security.stackexchange.com/questions/24554/should-i-use-ssl-tls-renegotiation
# As of 2020, TLS renegotiation is no more because it was insecure.
#    Renegotiation is removed from TLS 1.3 onward, year 2018.
#    All major software disabled renegotiation by default since as far as 2009 (nginx, haproxy, etc...). See Apache SSLInsecureRenegotiation notes for example.
#    Renegotiation has a variety of vulnerabilities by design, forcing clients to downgrade connections to less secure settings than they would normally do.
#    Verifying the client certificate for mutual authentication is handled separately than a renegotiation. See SSL_verify_client_post_handshake() in OpenSSL
#    CVE-2009-3555 all implementations, CVE-2011-5094 in Mozilla, CVE-2011-1473 in OpenSSL, CVE-2014-1771 in Windows SSL.
#
#

# ICMP - Will ping NRM endpoint and get RTT
# v6 only if DNS Replies with v6 record
ICMP_SCRAPE_NRM = {'job_name': 'WILLBEREPLACEDBYCODE',
                   'metrics_path': '/probe',
                   'params':{'module': ['WILLBEREPLACEDBYCODE']},
                   'static_configs':[{'targets': []}],
                   'relabel_configs':[{'source_labels': ['__address__'],
                                       'target_label': 'sitename',
                                       'replacement': 'WILLBEREPLACEDBYCODE'},
                                      {'source_labels': ['__address__'],
                                       'target_label': 'software',
                                       'replacement': 'WILLBEREPLACEDBYCODE'},
                                      {'source_labels': ['__address__'],
                                       'target_label': 'service',
                                       'replacement': 'WILLBEREPLACEDBYCODE'},
                                      {'source_labels': ['__address__'],
                                       'target_label': '__param_target'},
                                      {'source_labels': ['__param_target'],
                                       'target_label': 'instance'},
                                      {'target_label': '__address__',
                                       'replacement': 'prometheus-blackbox-exporter-service:9115'}]}

def getSiteRMRepo():
    """Get SiteRM Config Repo"""
    gitUrl = "https://github.com/sdn-sense/rm-configs"
    dirPath = tempfile.mkdtemp()

    Repo.clone_from(gitUrl, dirPath)
    return dirPath

def removeDir(dirPath):
    """Remove Dir"""
    shutil.rmtree(dirPath)

def loadYamlFile(fname):
    """Load Yaml file"""
    with open(fname, 'r', encoding='utf-8') as fd:
        return yload(fd.read())

def getIPv6Address(inHostname):
    """Get IPv6 address. If not available, return ::ffff:"""
    try:
        return socket.getaddrinfo(inHostname, None, socket.AF_INET6)[0][4][0]
    except socket.gaierror:
        return "::ffff:"

class PromModel():
    """Class for generating Prometheus config file"""
    def __init__(self,):
        self.default = loadYamlFile('default-prometheus-config.yml')
        self.jobs = []

    def _genName(self, tmpName):
        tmpName = tmpName.replace(' ', '_')
        if tmpName not in self.jobs:
            self.jobs.append(tmpName)
            return tmpName
        for i in range(0,100):
            # Can we have more than 100 DTNs/FEs for single site?
            nName = "%s_%s" % (tmpName, i)
            if nName not in self.jobs:
                self.jobs.append(nName)
                return nName
        return tmpName


    def _addFE(self, dirname):
        confFile = os.path.join(dirname, 'main.yaml')
        if not os.path.isfile(confFile):
            return
        conf = loadYamlFile(confFile)
        webdomain = conf.get('general', {}).get('webdomain', '')
        origwebdomain = webdomain
        probes = conf.get('general', {}).get('probes', ['https_v4_siterm_2xx',
                                                        'icmp_v4', 'icmp_v6'])
        if webdomain.startswith('https://'):
            webdomain = webdomain[8:]
        if not webdomain:
            return
        if webdomain.startswith('127.0.0.1'):
            return
        sites = conf.get('general', {}).get('sites', [])
        if not sites:
            return
        ipv6_addr = getIPv6Address(webdomain.split(':')[0])
        for site in sites:
            # 1. Query for State of all Services registered to FE
            tmpEntry = copy.deepcopy(STATE_SCRAPE)
            tmpEntry['job_name'] = self._genName('%s_STATE' % site)
            tmpEntry['static_configs'][0]['targets'].append(webdomain)
            tmpEntry['metrics_path'] = "/%s/sitefe/json/frontend/metrics" % site
            tmpEntry['relabel_configs'][0]['replacement'] = site
            tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
            self.default['scrape_configs'].append(tmpEntry)
            # 2. Query Endpoint and get TLS/Certificate information of Service
            if 'https_v4_siterm_2xx' in probes:
                tmpEntry = copy.deepcopy(HTTPS_SCRAPE)
                tmpEntry['job_name'] = self._genName('%s_HTTPS_V4' % site)
                tmpEntry['static_configs'][0]['targets'].append(origwebdomain)
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
                tmpEntry['params']['module'][0] = 'https_v4_siterm_2xx'
                self.default['scrape_configs'].append(tmpEntry)
            if 'https_v6_siterm_2xx' in probes and not ipv6_addr.startswith('::ffff'):
                # Check that it has IPv6
                tmpEntry = copy.deepcopy(HTTPS_SCRAPE)
                tmpEntry['job_name'] = self._genName('%s_HTTPS_V6' % site)
                tmpEntry['static_configs'][0]['targets'].append(origwebdomain)
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
                tmpEntry['params']['module'][0] = 'https_v6_siterm_2xx'
                self.default['scrape_configs'].append(tmpEntry)
            # 3. Add ICMP Check for FE
            if 'icmp_v4' in probes:
                tmpEntry = copy.deepcopy(ICMP_SCRAPE)
                tmpEntry['job_name'] = self._genName('%s_ICMP_V4' % site)
                tmpEntry['static_configs'][0]['targets'].append(webdomain.split(':')[0])
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
                tmpEntry['params']['module'][0] = 'icmp_v4'
                self.default['scrape_configs'].append(tmpEntry)
            if 'icmp_v6' in probes and not ipv6_addr.startswith('::ffff'):
                tmpEntry = copy.deepcopy(ICMP_SCRAPE)
                tmpEntry['job_name'] = self._genName('%s_ICMP_V6' % site)
                tmpEntry['static_configs'][0]['targets'].append(webdomain.split(':')[0])
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
                tmpEntry['params']['module'][0] = 'icmp_v6'
                self.default['scrape_configs'].append(tmpEntry)
            # 4. Check if agent config has node_exporter defined
            if conf.get('general', {}).get('node_exporter', ''):
                tmpEntry = copy.deepcopy(NODE_EXPORTER_SCRAPE)
                tmpEntry['job_name'] = self._genName('%s_NODE' % site)
                tmpEntry['static_configs'][0]['targets'].append(conf['general']['node_exporter'])
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
                self.default['scrape_configs'].append(tmpEntry)
        return

    def _addAgent(self, dirname):
        confFile = os.path.join(dirname, 'main.yaml')
        if not os.path.isfile(confFile):
            return
        conf = loadYamlFile(confFile)
        nodeExporter = conf.get('general', {}).get('node_exporter', '')
        site = conf.get('general', {}).get('sitename', '')
        if site and nodeExporter:
            for sitename in site:
                tmpEntry = copy.deepcopy(NODE_EXPORTER_SCRAPE)
                tmpEntry['job_name'] = self._genName('%s_NODE' % sitename)
                tmpEntry['static_configs'][0]['targets'].append(nodeExporter)
                tmpEntry['relabel_configs'][0]['replacement'] = sitename
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM-Agent'
                self.default['scrape_configs'].append(tmpEntry)
        return

    def addNRM(self, fname):
        """Add All Network-RM Endpoints to Promeheus config file"""
        def checkIfParamMissing(endpoint):
            for key in ['hostname', 'port', 'url', 'name', 'software']:
                if key not in endpoint:
                    print('Endpoint definition does not have required param "%s". Will not add to prometheus' % key)
                    print(endpoint)
                    return True
            return False
        if not os.path.isfile(fname):
            return
        hosts = []
        nrmMapping = loadYamlFile(fname)
        for name, vals in nrmMapping.items():
            for endpoint in vals['endpoints']:
                if checkIfParamMissing(endpoint):
                    continue
                fullUrl = ""
                probes = endpoint.get('probes', ['https_v4_network_2xx', 'icmp_v4', 'icmp_v6'])
                if not endpoint['url'].startswith('/'):
                    endpoint['url'] = "/%s" % endpoint['url']
                # HTTPS PROBES
                ipv6_addr = getIPv6Address(endpoint['url'])
                for probe in ['https_v4_network_2xx', 'https_v6_network_2xx']:
                    if probe not in probes:
                        continue
                    if 'secure' in endpoint and endpoint['secure']:
                        vtype = probe[6:8].upper()
                        if vtype == 'V6' and ipv6_addr.startswith('::ffff'):
                            # There is no ipv6 address on host. continue
                            continue
                        tmpEntry = copy.deepcopy(HTTPS_SCRAPE_NRM)
                        tmpEntry['job_name'] = self._genName('%s_%s_HTTPS_%s' % (name, endpoint['name'], vtype))
                        tmpEntry['params']['module'][0] = probe
                        fullUrl = "https://%(hostname)s:%(port)s%(url)s" % endpoint
                        tmpEntry['static_configs'][0]['targets'].append(fullUrl)
                        tmpEntry['relabel_configs'][0]['replacement'] = name
                        tmpEntry['relabel_configs'][1]['replacement'] = endpoint['software']
                        tmpEntry['relabel_configs'][2]['replacement'] = endpoint['name']
                        self.default['scrape_configs'].append(tmpEntry)
                # HTTP Probes
                for probe in ['http_v4_network_2xx', 'http_v6_network_2xx']:
                    if probe not in probes:
                        continue
                    vtype = probe[5:7].upper()
                    if vtype == 'V6' and ipv6_addr.startswith('::ffff'):
                        # There is no ipv6 address on host. continue
                        continue
                    tmpEntry = copy.deepcopy(HTTPS_SCRAPE_NRM)
                    tmpEntry['job_name'] = self._genName('%s_%s_HTTP_%s' % (name, endpoint['name'], vtype))
                    tmpEntry['params']['module'][0] = probe
                    fullUrl = "http://%(hostname)s:%(port)s%(url)s" % endpoint
                    tmpEntry['static_configs'][0]['targets'].append(fullUrl)
                    tmpEntry['relabel_configs'][0]['replacement'] = name
                    tmpEntry['relabel_configs'][1]['replacement'] = endpoint['software']
                    tmpEntry['relabel_configs'][2]['replacement'] = endpoint['name']
                    self.default['scrape_configs'].append(tmpEntry)
                # 2. Add ICMP Check
                for probe in  ['icmp_v4', 'icmp_v6']:
                    if probe not in probes:
                        continue
                    if endpoint['hostname'] not in hosts:
                        vtype = probe[5:].upper()
                        if vtype == 'V6' and ipv6_addr.startswith('::ffff'):
                            # There is no ipv6 address on host. continue
                            continue
                        hosts.append(endpoint['hostname'])
                        tmpEntry = copy.deepcopy(ICMP_SCRAPE_NRM)
                        tmpEntry['job_name'] = self._genName('%s_%s_ICMP_%s' % (name, endpoint['name'], vtype))
                        tmpEntry['params']['module'][0] = probe
                        tmpEntry['static_configs'][0]['targets'].append(endpoint['hostname'])
                        tmpEntry['relabel_configs'][0]['replacement'] = name
                        tmpEntry['relabel_configs'][1]['replacement'] = endpoint['software']
                        tmpEntry['relabel_configs'][2]['replacement'] = endpoint['name']
                        self.default['scrape_configs'].append(tmpEntry)

    def looper(self, dirname):
        """Loop via all SiteRM configs"""
        mappingFile = os.path.join(dirname, 'mapping.yaml')
        if not os.path.isfile(mappingFile):
            return
        mapping = loadYamlFile(mappingFile)
        for _key, val in mapping.items():
            if val.get('type', '') == 'Agent' and val.get('config', ''):
                tmpD = os.path.join(dirname, val.get('config'))
                self._addAgent(tmpD)
            elif val.get('type', '') == 'FE' and val.get('config', ''):
                tmpD = os.path.join(dirname, val.get('config'))
                self._addFE(tmpD)
        return

    def dump(self):
        """Dump New prometheus yaml file from generated output"""
        with open('prometheus.yml', 'w', encoding='utf-8') as fd:
            ydump(self.default, fd)

def execute():
    """Main execute"""
    worker = PromModel()
    workdir = getSiteRMRepo()
    for dirName in os.listdir(workdir):
        siteConfDir = os.path.join(workdir, dirName)
        worker.looper(siteConfDir)
    removeDir(workdir)
    worker.addNRM('../configs/nsi-endpoints')
    worker.dump()

if __name__ == "__main__":
    execute()
