#!/usr/bin/env python3
"""Generate Kubernetes Yaml files for XrootD Monitoring"""
import os
import tempfile
from yaml import safe_load as yload
from git import Repo


def getTemplate():
    """Get Kube Template"""
    with open('template', 'r', encoding='utf-8') as fd:
        return fd.read()

def getSiteRMRepo():
    """Get SiteRM Config Repo"""
    gitUrl = "https://github.com/sdn-sense/rm-configs"
    dirPath = tempfile.mkdtemp()

    Repo.clone_from(gitUrl, dirPath)
    return dirPath

def loadYamlFile(fname):
    """Load Yaml file"""
    with open(fname, 'r', encoding='utf-8') as fd:
        return yload(fd.read())

def writeKubeTemplate(template, filename):
    """Write Kube Template to file"""
    if os.path.isfile(f"yamls/{filename}"):
        os.remove(f"yamls/{filename}")
    with open(f"yamls/{filename}", 'w', encoding='utf-8') as fd:
        fd.write(template)

def createYaml(template, redir, sitename):
    """Create Yaml file"""
    uniqname = f"{redir.split('.')[0]}-{sitename.replace('_', '-').lower()}"
    template = template.replace('REPLACEME_UNIQ_NAME', uniqname)
    template = template.replace('REPLACEME_HOSTNAME_PORT', redir)
    template = template.replace('REPLACEME_HOSTNAME', redir.split(':')[0])
    print(template)
    print(uniqname, redir)
    writeKubeTemplate(template, f"{uniqname}.yaml")

def loopConf(dirname):
    """Loop through the configuration files"""
    confFile = os.path.join(dirname, 'main.yaml')
    if not os.path.isfile(confFile):
        return
    conf = loadYamlFile(confFile)
    sites = conf.get('general', {}).get('sites', [])
    template = getTemplate()
    for site in sites:
        # Get XrootD Metadata information
        xdata = conf.get(site, {}).get('metadata', {}).get('xrootd', {})
        for iprange, redir in xdata.items():
            print(iprange, redir)
            createYaml(template, redir, site)

def worker(dirname):
    """Worker function"""
    mappingFile = os.path.join(dirname, 'mapping.yaml')
    if not os.path.isfile(mappingFile):
        return
    mapping = loadYamlFile(mappingFile)
    for _key, val in mapping.items():
        if val.get('type', '') == 'FE' and val.get('config', ''):
            tmpD = os.path.join(dirname, val.get('config'))
            loopConf(tmpD)

def main():
    """Main function"""
    workdir = getSiteRMRepo()
    for dirName in os.listdir(workdir):
        siteConfDir = os.path.join(workdir, dirName)
        worker(siteConfDir)

if __name__ == "__main__":
    main()
