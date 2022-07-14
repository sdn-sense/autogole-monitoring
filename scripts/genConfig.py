#!/usr/bin/env python3
import os
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
                'tls_config': {'cert_file': '/etc/prometheus/cert.pem',
                               'key_file': '/etc/prometheus/privkey.pem',
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
                'params':{'module': ['https_v4_siterm_2xx']},
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
ICMP_SCRAPE = {'job_name': 'WILLBEREPLACEDBYCODE',
                'metrics_path': '/probe',
                'params':{'module': ['icmp_v4']},
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
# In case it is https, it will use https_v4_network_2xx module of blackbox
# In case it is http, it will use http_v4_network_2xx module of blackbox
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


# ICMP - Will ping FE endpoint and get RTT
ICMP_SCRAPE_NRM = {'job_name': 'WILLBEREPLACEDBYCODE',
                   'metrics_path': '/probe',
                   'params':{'module': ['icmp_v4']},
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
    print(dirPath)
    return dirPath

def removeDir(dirPath):
    """Remove Dir"""
    shutil.rmtree(dirPath)

def loadYamlFile(fname):
    """Load Yaml file"""
    with open(fname, 'r', encoding='utf-8') as fd:
        return yload(fd.read())

class PromModel():
    """Class for generating Prometheus config file"""
    def __init__(self,):
        self.default = loadYamlFile('default-config.yml')
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
        if webdomain.startswith('https://'):
            webdomain = webdomain[8:]
        if not webdomain:
            return
        if webdomain.startswith('127.0.0.1'):
            return
        sites = conf.get('general', {}).get('sites', [])
        if not sites:
            return
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
            tmpEntry = copy.deepcopy(HTTPS_SCRAPE)
            tmpEntry['job_name'] = self._genName('%s_HTTPS' % site)
            tmpEntry['static_configs'][0]['targets'].append(origwebdomain)
            tmpEntry['relabel_configs'][0]['replacement'] = site
            tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
            self.default['scrape_configs'].append(tmpEntry)
            # 3. Add ICMP Check for FE
            tmpEntry = copy.deepcopy(ICMP_SCRAPE)
            tmpEntry['job_name'] = self._genName('%s_ICMP' % site)
            tmpEntry['static_configs'][0]['targets'].append(webdomain.split(':')[0])
            tmpEntry['relabel_configs'][0]['replacement'] = site
            tmpEntry['relabel_configs'][1]['replacement'] = 'SiteRM'
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
        site = conf.get('general', {}).get('siteName', '')
        if site and nodeExporter:
            tmpEntry = copy.deepcopy(NODE_EXPORTER_SCRAPE)
            tmpEntry['job_name'] = self._genName('%s_NODE' % site)
            tmpEntry['static_configs'][0]['targets'].append(nodeExporter)
            tmpEntry['relabel_configs'][0]['replacement'] = site
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
        nrmMapping = loadYamlFile(fname)
        for name, vals in nrmMapping.items():
            for endpoint in vals['endpoints']:
                if checkIfParamMissing(endpoint):
                    continue
                # 1. Add HTTPS/HTTP Scan
                tmpEntry = copy.deepcopy(HTTPS_SCRAPE_NRM)
                fullUrl = ""
                if 'secure' in endpoint and endpoint['secure']:
                    tmpEntry['job_name'] = self._genName('%s_%s_HTTPS' % (name, endpoint['name']))
                    tmpEntry['params']['module'][0] = 'https_v4_network_2xx'
                    fullUrl = "https://%(hostname)s:%(port)s/%(url)s" % endpoint
                else:
                    tmpEntry['job_name'] = self._genName('%s_%s_HTTP' % (name, endpoint['name']))
                    tmpEntry['params']['module'][0] = 'http_v4_network_2xx'
                    fullUrl = "http://%(hostname)s:%(port)s/%(url)s" % endpoint
                tmpEntry['static_configs'][0]['targets'].append(fullUrl)
                tmpEntry['relabel_configs'][0]['replacement'] = name
                tmpEntry['relabel_configs'][1]['replacement'] = endpoint['software']
                tmpEntry['relabel_configs'][2]['replacement'] = endpoint['name']
                self.default['scrape_configs'].append(tmpEntry)
                # 2. Add ICMP Check
                tmpEntry = copy.deepcopy(ICMP_SCRAPE_NRM)
                tmpEntry['job_name'] = self._genName('%s_%s_ICMP' % (name, endpoint['name']))
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
    worker.addNRM('../configs/nsa-endpoints')
    worker.dump()

if __name__ == "__main__":
    execute()
