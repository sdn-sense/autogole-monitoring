#!/usr/bin/env python3
"""Get token for SiteRM Frontend to be used in Autogole monitoring."""
import os
import time
import tempfile
import shutil
import traceback
from yaml import safe_load as yload

from git import InvalidGitRepositoryError, NoSuchPathError, Repo

from sense.client.siterm.frontend_api import FrontendApi


os.environ["SITERM_TOKEN_EXPIRY_SKEW"] = "300" # 5 mins before

def loadYamlFile(fname):
    """Load Yaml file"""
    with open(fname, 'r', encoding='utf-8') as fd:
        return yload(fd.read())

def getSiteRMRepo(dirpath):
    """Get SiteRM Config Repo"""
    gitUrl = "https://github.com/sdn-sense/rm-configs"
    if not gitUrl.endswith(".git"):
        gitUrl += ".git"
    try:
        repo = Repo(dirpath)
        repo.git.fetch("--all")

    except (InvalidGitRepositoryError, NoSuchPathError):
        if os.path.exists(dirpath):
            shutil.rmtree(dirpath)
        os.makedirs(dirpath, exist_ok=True)
        repo = Repo.clone_from(gitUrl, dirpath)
    except Exception as ex:
        print(f"Full traceback: {traceback.format_exc()}")
        raise ex
    return dirpath


def saveToken(sitename, token):
    """Save token to file"""
    savedir = os.environ.get("SITERM_TOKEN_DIR")
    if not savedir:
        print("SITERM_TOKEN_DIR environment variable is not set.")
        return
    tokenfile = os.path.join(savedir, f"oidc-{sitename.lower()}.token")
    # if file exists, read content
    if os.path.isfile(tokenfile):
        with open(tokenfile, 'r', encoding='utf-8') as f:
            existing_token = f.read().strip()
        if existing_token == token:
            return
    with open(tokenfile, 'w', encoding='utf-8') as f:
        f.write(token)

def getToken(sitename):
    """Get token for a given site"""
    try:
        api = FrontendApi()
        api.get_alive(sitename=sitename)
        token = api.client._token_cache.get(sitename, {}).get("access_token")
        saveToken(sitename, token)
    except Exception as ex:
        print(f"Error getting token for {sitename}: {ex}")


def execute(dirpath):
    """Loop Git repo and all sites"""
    workdir = getSiteRMRepo(dirpath)
    print(workdir)
    for dirName in os.listdir(workdir):
        siteConfDir = os.path.join(workdir, dirName)
        if os.path.isfile(os.path.join(siteConfDir, "disabled")):
            continue
        mappingFile = os.path.join(siteConfDir, 'mapping.yaml')
        if not os.path.isfile(mappingFile):
            continue
        mapping = loadYamlFile(mappingFile)
        for _key, val in mapping.items():
            if val.get('type', '') == 'FE' and val.get('config', ''):
                tmpD = os.path.join(siteConfDir, val.get('config'))
                confFile = os.path.join(tmpD, 'main.yaml')
                if not os.path.isfile(confFile):
                    continue
                conf = loadYamlFile(confFile)
                oidc = conf.get('general', {}).get('oidc', False)
                if oidc:
                    print(f"Getting token for {dirName}")
                    getToken(dirName)
                else:
                    print(f"OIDC not enabled for {dirName}")

if __name__ == "__main__":
    tmpdir = tempfile.mkdtemp()
    while True:
        execute(tmpdir)
        print("Run finished. Sleeping for 60 seconds.")
        time.sleep(30)
