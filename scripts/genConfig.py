#!/usr/bin/env python3
"""Generate Prometheus Config file from SiteRM Configs and Network-RM Configs"""
import os
import socket
import copy
import tempfile
import shutil
from urllib.parse import urlparse
from yaml import safe_load as yload
from yaml import safe_dump as ydump
from git import Repo
from nrm import allNSIEndpoints

# STATE - Will Query FE (/<SITENAME>/sitefe/json/frontend/metrics)
# and get all services states of that FE and Agent's registered to it.
# It is also used for Apache scrape for SiteRM endpoints. It uses the following exporter:
# https://github.com/Lusitaniae/apache_exporter?tab=readme-ov-file
STATE_SCRAPE = {'job_name': 'WILLBEREPLACEDBYCODE',
                'scrape_interval': '30s',
                'static_configs': [{'targets': []}],
                'scheme': 'https',
                'metrics_path': 'WILLBEREPLACEDBYCODE',
                'tls_config': {'cert_file': '/etc/tls/tls.crt',
                               'key_file': '/etc/tls/tls.key',
                               'insecure_skip_verify': True},
                'relabel_configs': [{'source_labels': ['__address__'],
                                     'target_label': 'sitename',
                                     'replacement': 'WILLBEREPLACEDBYCODE'},
                                    {'source_labels': ['__address__'],
                                     'target_label': 'software',
                                     'replacement': 'WILLBEREPLACEDBYCODE'},
                                    {'source_labels': ['__address__'],
                                     'target_label': 'latitude',
                                     'replacement': 'WILLBEREPLACEDBYCODE'},
                                    {'source_labels': ['__address__'],
                                     'target_label': 'longitude',
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
                                    'target_label': 'latitude',
                                    'replacement': 'WILLBEREPLACEDBYCODE'},
                                   {'source_labels': ['__address__'],
                                    'target_label': 'longitude',
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
                                    'target_label': 'latitude',
                                    'replacement': 'WILLBEREPLACEDBYCODE'},
                                   {'source_labels': ['__address__'],
                                    'target_label': 'longitude',
                                    'replacement': 'WILLBEREPLACEDBYCODE'},
                                   {'source_labels': ['__address__'],
                                    'target_label': 'ipversion',
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

# Agent might also have prometheus_federate defined in general section
# general:
#   prometheus_federate:
#   prometheus_query: '{instance="k8s-gen4-07.ultralight.org", service="node-exporter"}'
# Which in that case it will use that to scrape metrics
PROMETHEUS_FEDERATE_SCRAPE = {'job_name': 'WILLBEREPLACEDBYCODE',
                              'honor_labels': True,
                              'metrics_path': '/federate', # Default path (might be replaced if url diff https://url/federatenew)
                              'scheme': 'http', # Default scheme (might be replaced if url diff, e.g. https://...)
                              'static_configs': [{'targets': []}],
                              'params': {'match[]': []},  # Match will be added based on instance
                              'relabel_configs': [{'source_labels': ['__address__'],
                                             'target_label': 'sitename',
                                             'replacement': 'WILLBEREPLACEDBYCODE'},
                                            {'source_labels': ['__address__'],
                                             'target_label': 'software',
                                             'replacement': 'WILLBEREPLACEDBYCODE'}]}

# XrootD Metadata scrape template
XROOTD_SCRAPE = {'job_name': 'WILLBEREPLACEDBYCODE',
                'scrape_interval': '60s',
                'static_configs': [{'targets': []}],
                'scheme': 'http',
                'metrics_path': 'WILLBEREPLACEDBYCODE',
                'relabel_configs': [{'source_labels': ['__address__'],
                                     'target_label': 'sitename',
                                     'replacement': 'WILLBEREPLACEDBYCODE'},
                                    {'source_labels': ['__address__'],
                                     'target_label': 'endpoint',
                                     'replacement': 'WILLBEREPLACEDBYCODE'},
                                    {'source_labels': ['__address__'],
                                     'target_label': 'software',
                                     'replacement': 'WILLBEREPLACEDBYCODE'}]}

# VPP Exporter - Will query VPP Exporter endpoint and get VPP Metrics
VPP_EXPORTER_SCRAPE = {'job_name': 'WILLBEREPLACEDBYCODE',
                       'scrape_interval': '60s',
                       'static_configs': [{'targets': []}],
                       'metrics_path': 'WILLBEREPLACEDBYCODE',
                       'relabel_configs': [{'source_labels': ['__address__'],
                                            'target_label': 'sitename',
                                            'replacement': 'WILLBEREPLACEDBYCODE'},
                                           {'source_labels': ['__address__'],
                                            'target_label': 'endpoint',
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
                                       'target_label': 'latitude',
                                       'replacement': 'WILLBEREPLACEDBYCODE'},
                                      {'source_labels': ['__address__'],
                                       'target_label': 'longitude',
                                       'replacement': 'WILLBEREPLACEDBYCODE'},
                                      {'source_labels': ['__address__'],
                                       'target_label': '__param_target'},
                                      {'source_labels': ['__param_target'],
                                       'target_label': 'instance'},
                                      {'target_label': '__address__',
                                       'replacement': 'prometheus-blackbox-exporter-service:9115'}]}

# ICMP - Will ping NRM endpoint and get RTT
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
                                       'target_label': 'latitude',
                                       'replacement': 'WILLBEREPLACEDBYCODE'},
                                      {'source_labels': ['__address__'],
                                       'target_label': 'longitude',
                                       'replacement': 'WILLBEREPLACEDBYCODE'},
                                      {'source_labels': ['__address__'],
                                       'target_label': 'ipversion',
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
    """Get IPv6 address. If not available, return None"""
    try:
        return socket.getaddrinfo(inHostname, None, socket.AF_INET6)[0][4][0]
    except socket.gaierror:
        return None

def getIPv4Address(inHostname):
    """Get IPv4 address. If not available, return None"""
    try:
        return socket.getaddrinfo(inHostname, None, socket.AF_INET)[0][4][0]
    except socket.gaierror:
        return None

class PromModel():
    """Class for generating Prometheus config file"""
    def __init__(self,):
        self.default = loadYamlFile('default-prometheus-config.yml')
        self.jobs = []
        self.xrootdPresent = []

    def _genName(self, tmpName):
        tmpName = tmpName.replace(' ', '_')
        if tmpName not in self.jobs:
            self.jobs.append(tmpName)
            return tmpName
        for i in range(0,100):
            # Can we have more than 100 DTNs/FEs for single site?
            nName = f"{tmpName}_{i}"
            if nName not in self.jobs:
                self.jobs.append(nName)
                return nName
        return tmpName

    def _addXrootD(self, dirname):
        """Add XrootD Metadata to Prometheus config"""
        confFile = os.path.join(dirname, 'main.yaml')
        if not os.path.isfile(confFile):
            return
        conf = loadYamlFile(confFile)
        sites = conf.get('general', {}).get('sites', [])
        for site in sites:
            # Get XrootD Metadata information
            xdata = conf.get(site, {}).get('metadata', {}).get('xrootd', {})
            for _iprange, redir in xdata.items():
                if redir in self.xrootdPresent:
                    continue
                self.xrootdPresent.append(redir)
                url = f"{redir.split('.')[0]}-{site.replace('_', '-').lower()}.nrp-nautilus.io"
                tmpEntry = copy.deepcopy(XROOTD_SCRAPE)
                tmpEntry['job_name'] = self._genName(f'{site}_XROOTD')
                tmpEntry['metrics_path'] = "/metrics"
                tmpEntry['static_configs'][0]['targets'].append(url)
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = redir.split('.')[0]
                tmpEntry['relabel_configs'][2]['replacement'] = 'XRootD'
                self.default['scrape_configs'].append(tmpEntry)

    def _addVPP(self, dirname):
        confFile = os.path.join(dirname, 'main.yaml')
        if not os.path.isfile(confFile):
            return
        conf = loadYamlFile(confFile)
        sites = conf.get('general', {}).get('sites', [])
        for site in sites:
            # Get XrootD Metadata information
            vppurl = conf.get(site, {}).get('vpp_exporter', {})
            if not vppurl:
                continue
            tmpEntry = copy.deepcopy(VPP_EXPORTER_SCRAPE)

            tmpEntry['job_name'] = self._genName(f'{site}_VPP')
            tmpEntry['metrics_path'] = "/metrics"
            tmpEntry['static_configs'][0]['targets'].append(vppurl)
            tmpEntry['relabel_configs'][0]['replacement'] = site
            tmpEntry['relabel_configs'][1]['replacement'] = vppurl
            tmpEntry['relabel_configs'][2]['replacement'] = 'VPP'
            self.default['scrape_configs'].append(tmpEntry)


    def _addFE(self, dirname):
        confFile = os.path.join(dirname, 'main.yaml')
        if not os.path.isfile(confFile):
            return
        conf = loadYamlFile(confFile)
        webdomain = conf.get('general', {}).get('webdomain', '')
        origwebdomain = webdomain.strip('/')
        probes = conf.get('general', {}).get('probes', ['https_v4_siterm_2xx', 'https_v6_siterm_2xx',
                                                        'icmp_v4', 'icmp_v6'])
        fastapi = conf.get('general', {}).get('fastapi', False)
        if webdomain.startswith('https://'):
            webdomain = webdomain[8:]
        if not webdomain:
            return
        if webdomain.startswith('127.0.0.1'):
            return
        sites = conf.get('general', {}).get('sites', [])
        if not sites:
            # This is to cover old configurations, where sitename is a list
            # TODO: Remove after 1.50 release. (Aug 2025)
            sites = conf.get('general', {}).get('sitename', [])
            if isinstance(sites, str):
                sites = [sites]
        if not sites:
            return
        ipv6_addr = getIPv6Address(webdomain.split(':')[0])
        ipv4_addr = getIPv4Address(webdomain.split(':')[0])
        for site in sites:
            lat, lng = conf.get(site, {}).get('latitude', '0.00'), conf.get(site, {}).get('longitude', '0.00')
            # 1. Query for State of all Services registered to FE
            tmpEntry = copy.deepcopy(STATE_SCRAPE)
            tmpEntry['job_name'] = self._genName(f'{site}_STATE')
            tmpEntry['static_configs'][0]['targets'].append(webdomain)
            if not fastapi:
                tmpEntry['metrics_path'] = f"/{site}/sitefe/json/frontend/metrics"
            else:
                tmpEntry['metrics_path'] = f"/api/{site}/monitoring/prometheus/metrics"
            tmpEntry['relabel_configs'][0]['replacement'] = site
            tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
            tmpEntry['relabel_configs'][2]['replacement'] = lat
            tmpEntry['relabel_configs'][3]['replacement'] = lng
            self.default['scrape_configs'].append(tmpEntry)
            # 2. Scrape apache HTTP Status information from SiteRM Endpoints
            tmpEntry = copy.deepcopy(STATE_SCRAPE)
            tmpEntry['job_name'] = self._genName(f'{site}_STATEHTTP')
            tmpEntry['static_configs'][0]['targets'].append(webdomain)
            tmpEntry['metrics_path'] = "/siterm-http-status"
            tmpEntry['relabel_configs'][0]['replacement'] = site
            tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM-HTTP'
            tmpEntry['relabel_configs'][2]['replacement'] = lat
            tmpEntry['relabel_configs'][3]['replacement'] = lng
            self.default['scrape_configs'].append(tmpEntry)
            # 3. Query Endpoint and get TLS/Certificate information of Service
            if 'https_v4_siterm_2xx' in probes and ipv4_addr:
                # Query models api and get model and timing (output ignored)
                tmpEntry = copy.deepcopy(HTTPS_SCRAPE)
                tmpEntry['job_name'] = self._genName(f'{site}_MODEL_V4')
                if not fastapi:
                    tmpEntry['static_configs'][0]['targets'].append(f'{origwebdomain}/{site}/sitefe/v1/models?current=true&summary=false&encode=false')
                else:
                    tmpEntry['static_configs'][0]['targets'].append(f'{origwebdomain}/api/{site}/models?current=true&summary=false&encode=false')
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
                tmpEntry['relabel_configs'][2]['replacement'] = lat
                tmpEntry['relabel_configs'][3]['replacement'] = lng
                tmpEntry['params']['module'][0] = 'https_v4_siterm_2xx'
                self.default['scrape_configs'].append(tmpEntry)
            if 'https_v6_siterm_2xx' in probes and ipv6_addr:
                # Check that it has IPv6
                # Query models api and get model and timing (output ignored)
                tmpEntry = copy.deepcopy(HTTPS_SCRAPE)
                tmpEntry['job_name'] = self._genName(f'{site}_MODEL_V6')
                if not fastapi:
                    tmpEntry['static_configs'][0]['targets'].append(f'{origwebdomain}/{site}/sitefe/v1/models?current=true&summary=false&encode=false')
                else:
                    tmpEntry['static_configs'][0]['targets'].append(f'{origwebdomain}/api/{site}/models?current=true&summary=false&encode=false')
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
                tmpEntry['relabel_configs'][2]['replacement'] = lat
                tmpEntry['relabel_configs'][3]['replacement'] = lng
                tmpEntry['params']['module'][0] = 'https_v6_siterm_2xx'
                self.default['scrape_configs'].append(tmpEntry)
            # 4. Add ICMP Check for FE
            if 'icmp_v4' in probes and ipv4_addr:
                tmpEntry = copy.deepcopy(ICMP_SCRAPE)
                tmpEntry['job_name'] = self._genName(f'{site}_ICMP_V4')
                tmpEntry['static_configs'][0]['targets'].append(webdomain.split(':')[0])
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
                tmpEntry['relabel_configs'][2]['replacement'] = lat
                tmpEntry['relabel_configs'][3]['replacement'] = lng
                tmpEntry['relabel_configs'][4]['replacement'] = 'v4'
                tmpEntry['params']['module'][0] = 'icmp_v4'
                self.default['scrape_configs'].append(tmpEntry)
            if 'icmp_v6' in probes and ipv6_addr:
                tmpEntry = copy.deepcopy(ICMP_SCRAPE)
                tmpEntry['job_name'] = self._genName(f'{site}_ICMP_V6')
                tmpEntry['static_configs'][0]['targets'].append(webdomain.split(':')[0])
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
                tmpEntry['relabel_configs'][2]['replacement'] = lat
                tmpEntry['relabel_configs'][3]['replacement'] = lng
                tmpEntry['relabel_configs'][4]['replacement'] = 'v6'
                tmpEntry['params']['module'][0] = 'icmp_v6'
                self.default['scrape_configs'].append(tmpEntry)
            # 5. Check if fe config has node_exporter defined
            if conf.get('general', {}).get('node_exporter', ''):
                tmpEntry = copy.deepcopy(NODE_EXPORTER_SCRAPE)
                tmpEntry['job_name'] = self._genName(f'{site}_NODE')
                tmpEntry['static_configs'][0]['targets'].append(conf['general']['node_exporter'])
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
                self.default['scrape_configs'].append(tmpEntry)
            # Add external snmp for all network devices;
            devices = conf.get(site, {}).get('switch', [])
            for device in devices:
                # Get Switch config
                externalsnmp = conf.get(device, {}).get('external_snmp', '')
                if not externalsnmp:
                    continue
                tmpEntry = copy.deepcopy(STATE_SCRAPE)
                parsedUrl = urlparse(externalsnmp)
                tmpEntry['job_name'] = self._genName(f'{site}_NSISNMPMon')
                tmpEntry['static_configs'][0]['targets'].append(parsedUrl.netloc)
                tmpEntry['metrics_path'] = parsedUrl.path
                tmpEntry['relabel_configs'][0]['replacement'] = site
                tmpEntry['relabel_configs'][1]['replacement'] = 'NSI-SNMPMon'
                tmpEntry['relabel_configs'][2]['replacement'] = lat
                tmpEntry['relabel_configs'][3]['replacement'] = lng
                self.default['scrape_configs'].append(tmpEntry)

        return

    def _addAgent(self, dirname):
        confFile = os.path.join(dirname, 'main.yaml')
        print(f'Load {confFile}')
        if not os.path.isfile(confFile):
            print('File does not exist. Continue')
            return
        conf = loadYamlFile(confFile)
        if not conf:
            print(f'Failed to load {confFile}. Empty return')
            return
        nodeExporter = conf.get('general', {}).get('node_exporter', '')
        promFederate = conf.get('general', {}).get('prometheus_federate', '')
        promQuery = conf.get('general', {}).get('prometheus_query', '')
        site = conf.get('general', {}).get('sitename', '')
        # This is to cover old configurations, where sitename is a list
        # TODO: Remove after 1.50 release. (Aug 2025)
        if isinstance(site, str):
            site = [site]
        if site and nodeExporter:
            for sitename in site:
                tmpEntry = copy.deepcopy(NODE_EXPORTER_SCRAPE)
                tmpEntry['job_name'] = self._genName(f'{sitename}_NODE')
                tmpEntry['static_configs'][0]['targets'].append(nodeExporter)
                tmpEntry['relabel_configs'][0]['replacement'] = sitename
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM-Agent'
                self.default['scrape_configs'].append(tmpEntry)
        elif site and promFederate and promQuery:
            for sitename in site:
                parsedurl = urlparse(promFederate)
                tmpEntry = copy.deepcopy(PROMETHEUS_FEDERATE_SCRAPE)
                tmpEntry['job_name'] = self._genName(f'{sitename}_PROMFED')
                tmpEntry['metrics_path'] = parsedurl.path
                tmpEntry['scheme'] = parsedurl.scheme
                tmpEntry['params']['match[]'] = [promQuery]
                tmpEntry['static_configs'][0]['targets'].append(parsedurl.netloc)
                tmpEntry['relabel_configs'][0]['replacement'] = sitename
                tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM-Agent'
                self.default['scrape_configs'].append(tmpEntry)
        else:
            print(f'No Node Exporter or Prometheus Federate defined in Agent Config. {confFile} {site}')
        return

    def addNRM(self, fname):
        """Add All Network-RM Endpoints to Promeheus config file"""
        if not os.path.isfile(fname):
            return
        hosts = {}
        nsiendpoints = allNSIEndpoints()
        nsiendpoints.execute()
        nrmconfig = loadYamlFile(fname)
        for name, vals in nsiendpoints.out.items():
            hosts.setdefault(name, [])
            if 'url' not in vals:
                continue
            if not nrmconfig.get('discovery', {}).get(name, {}).get('sitename', ''):
                print(name, vals)
                print('SITENAME NOT DEFINED. IGNORE ENTRIES ABOVE!')
                continue
            probes = nrmconfig['probes']
            if 'probes' in nrmconfig.get('discovery', {}).get(name, {}):
                probes = nrmconfig['discovery'][name]['probes']
            lat, lng = vals.get('location', {}).get('latitude', '0.00'), vals.get('location', {}).get('longitude', '0.00')
            if lat == '0.00':
                lat = nrmconfig.get('discovery', {}).get(name, {}).get('location', {}).get('latitude', '0.00')
            if lng == '0.00':
                lng = nrmconfig.get('discovery', {}).get(name, {}).get('location', {}).get('longitude', '0.00')
            for url in vals['url']:
                parsedurl = urlparse(url)
                site = nrmconfig['discovery'][name]['sitename']
                if parsedurl.scheme == 'https' and 'https_v4_network_2xx' in probes:
                    tmpEntry = copy.deepcopy(HTTPS_SCRAPE_NRM)
                    tmpEntry['job_name'] = self._genName(f'{site}_HTTPS')
                    tmpEntry['params']['module'][0] = 'https_v4_network_2xx'
                    tmpEntry['static_configs'][0]['targets'].append(url)
                    tmpEntry['relabel_configs'][0]['replacement'] = site
                    tmpEntry['relabel_configs'][1]['replacement'] = 'NetworkRM'  # Any way to get it automated?
                    tmpEntry['relabel_configs'][2]['replacement'] = lat
                    tmpEntry['relabel_configs'][3]['replacement'] = lng
                    self.default['scrape_configs'].append(tmpEntry)
                elif parsedurl.scheme == 'http' and 'https_v4_network_2xx' in nrmconfig['probes']:
                    tmpEntry = copy.deepcopy(HTTPS_SCRAPE_NRM)
                    tmpEntry['job_name'] = self._genName(f'{site}_HTTPS')
                    tmpEntry['params']['module'][0] = 'https_v4_network_2xx'
                    tmpEntry['static_configs'][0]['targets'].append(url)
                    tmpEntry['relabel_configs'][0]['replacement'] = site
                    tmpEntry['relabel_configs'][1]['replacement'] = 'NetworkRM'  # Any way to get it automated?
                    tmpEntry['relabel_configs'][2]['replacement'] = lat
                    tmpEntry['relabel_configs'][3]['replacement'] = lng
                    self.default['scrape_configs'].append(tmpEntry)
                if parsedurl.hostname not in hosts[name] and 'icmp_v4' in probes:
                    hosts[name].append(parsedurl.hostname)
                    tmpEntry = copy.deepcopy(ICMP_SCRAPE_NRM)
                    tmpEntry['job_name'] = self._genName(f'{site}_ICMP')
                    tmpEntry['params']['module'][0] = 'icmp_v4'
                    tmpEntry['static_configs'][0]['targets'].append(parsedurl.hostname)
                    tmpEntry['relabel_configs'][0]['replacement'] = site
                    tmpEntry['relabel_configs'][1]['replacement'] = 'NetworkRM'  # Any way to get it automated?
                    tmpEntry['relabel_configs'][2]['replacement'] = lat
                    tmpEntry['relabel_configs'][3]['replacement'] = lng
                    tmpEntry['relabel_configs'][4]['replacement'] = 'v4'
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
                self._addXrootD(tmpD)
                self._addVPP(tmpD)

    def dump(self):
        """Dump New prometheus yaml file from generated output"""
        with open('prometheus.yml', 'w', encoding='utf-8') as fd:
            ydump(self.default, fd)
        # Also need to dump file for rt mon (with 10s interval)
        prometheus_config = copy.deepcopy(self.default)
        prometheus_config['global']['scrape_interval'] = '10s'
        for job in prometheus_config['scrape_configs']:
            job['scrape_interval'] = '10s'
        with open('prometheus-rt.yml', 'w', encoding='utf-8') as fd:
            ydump(prometheus_config, fd)

def execute():
    """Main execute"""
    worker = PromModel()
    workdir = getSiteRMRepo()
    for dirName in os.listdir(workdir):
        siteConfDir = os.path.join(workdir, dirName)
        worker.looper(siteConfDir)
    removeDir(workdir)
    #worker.addNRM('../configs/nsi-endpoints')
    worker.dump()

if __name__ == "__main__":
    execute()
