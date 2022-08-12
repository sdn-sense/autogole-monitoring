import os
import json
import tempfile
import shutil
from datetime import date
from yaml import safe_load as yload
from git import Repo
from grafana_client import GrafanaApi
from slack_sdk import WebClient

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

def readFile(fname):
    """Read file into string"""
    with open(fname, 'r', encoding='utf-8') as fd:
        data = fd.read().rstrip("\n")
    return data

def writeFile(fname, data):
    """Write data to file"""
    with open(fname, 'w', encoding='utf-8') as fd:
        fd.write(data)

class SlackAPI():
    """Slack API to create/update/get channels info"""
    def __init__(self, config):
        self.client = WebClient(token=config['slack_token'])
        self.channels = {'ByName': {}, 'ByID': {}}
        self.__testClient()
        self.getChannels()

    def __testClient(self):
        self.client.api_test()

    def getChannels(self):
        """Get All Channels from Slack"""
        self.channels = {'ByName': {}, 'ByID': {}}
        channels = self.client.conversations_list()
        for item in channels.data['channels']:
            self.channels['ByName'].setdefault(item['name'], item)
            self.channels['ByID'].setdefault(item['id'], item)

    def getChannelByID(self, channel_id):
        """Get Channel by ID"""
        return self.channels['ByID'].get(channel_id, {})

    def getChannelByName(self, name):
        """Get Channel by Name"""
        return self.channels['ByName'].get(name, {})

    def createChannel(self, name):
        """Create New Channel"""
        if not self.getChannelByName(name):
            self.client.conversations_create(name=name)
            self.getChannels()
        return self.getChannelByName(name)

    def setPurpose(self, channel_id, purpose):
        """Set Channel Purpose"""
        channelConfig = self.getChannelByID(channel_id)
        if channelConfig['purpose']['value'] != purpose:
            self.client.conversations_setPurpose(channel=channel_id, purpose=purpose)
            self.getChannels()


class GrafanaUpdate():
    """Autogole SENSE Grafana Alerts/Folders/Dashboards auto updater"""
    def __init__(self, config):
        self.config = config
        self.grafanaapi = GrafanaApi.from_url(
                                     url=self.config['api_url'],
                                     credential=self.config['api_key'])
        self.dashboards = {}
        self.alerts = {}
        self.folders = {}
        self.loadAll()

    def loadAll(self):
        """Load all Dashboards, Alerts, Folders"""
        self.getDashboards()
        self.getAlerts()
        self.getFolders()

    def getOrganizationConfig(self):
        """Get organization config preferences"""
        return self.grafanaapi.organizations.organization_preference_get()

    def updateOrganizationConfig(self, newconfig):
        """Update organization config"""
        return self.grafanaapi.organizations.organization_preference_update(
            theme=newconfig['theme'],
            home_dashboard_id=newconfig['homeDashboardId'],
            timezone=newconfig['timezone']
        )

    def getDashboards(self):
        """Get dashboards from Grafana"""
        self.dashboards = {}
        for item in self.grafanaapi.search.search_dashboards():
            self.dashboards[item['title']] = item

    def getDashboardByTitle(self, title):
        """Get dashboard by Title"""
        if title in self.dashboards:
            return self.dashboards[title]
        return {}

    def addNewDashboard(self, dashbJson):
        """Add new dashboard"""
        return self.grafanaapi.dashboard.update_dashboard(dashbJson)

    def createFolder(self, title):
        """Create Folder"""
        self.getFolders()
        if title in self.folders:
            return self.folders[title]
        return self.grafanaapi.folder.create_folder(title)

    def getFolders(self):
        """Get all folders"""
        for item in self.grafanaapi.folder.get_all_folders():
            self.folders[item['title']] = item

    def getFolderID(self, name):
        """Get folder ID by Name. Default None"""
        if name in self.folders:
            return self.folders[name]['id']
        if name != 'General':
            # General is default and not returned by Grafana API
            print("Folder %s is not configured." % name)
        return None

    def getAlerts(self):
        """Get all alerts"""
        self.alerts = {}
        for item in self.grafanaapi.notifications.get_channels():
            self.alerts[item['name']] = item

    def getAlertID(self, name):
        """Get Alert ID by Name. Default is UNCONFIGURED_WEBHOOK"""
        if name in self.alerts:
            return self.alerts[name]['uid']
        print("Return unconfigured webhook for %s" % name)
        return self.alerts['UNCONFIGURED_WEBHOOK']['uid']

    def createAlerts(self):
        """Create Alerts in Grafana"""
        def createSlackAlert(key, vals):
            if 'url' not in vals or 'recipient' not in vals:
                print('Alert channel wrongly configured for %s. missing url or recipient' % key)
                return {}
            out = {'type': '', 'frequency': '', 'sendReminder': False,
                   'isDefault': False, 'settings': {'url': '', 'recipient': ''}}
            out['name'] = key
            out['type'] = vals.get('type', 'slack')
            out['frequency'] = vals.get('frequency', '')
            out['sendReminder'] = bool(vals.get('sendReminder', 'False'))
            out['settings']['url'] = vals['url']
            out['settings']['recipient'] = vals['recipient']
            return out
        def createEmailAlert(key, vals):
            if 'addresses' not in vals:
                print('Alert channel wrongly configured for %s. missing url or recipient' % key)
                return {}
            out = {'type': '', 'frequency': '', 'sendReminder': False,
                   'isDefault': False, 'settings': {'singleEmail': '', 'addresses': ''}}
            out['name'] = key
            out['type'] = vals.get('type', 'email')
            out['frequency'] = vals.get('frequency', '')
            out['sendReminder'] = bool(vals.get('sendReminder', 'False'))
            out['settings']['singleEmail'] = bool(vals.get('singleEmail', 'False'))
            out['settings']['addresses'] = vals['addresses']
            return out

        self.getAlerts()
        for key, vals in self.config['alert_channels'].items():
            if key in self.alerts:
                # This alert already in place. TODO: In future support update.
                continue
            if 'type' in vals and vals['type'] == 'slack':
                out = createSlackAlert(key, vals)
            elif 'type' in vals and vals['type'] == 'email':
                out = createEmailAlert(key, vals)
            if out:
                self.grafanaapi.notifications.create_channel(out)
        self.getAlerts()

class Worker(GrafanaUpdate, SlackAPI):
    """Inheritance Worker"""
    def __init__(self, config):
        GrafanaUpdate.__init__(self, config)
        SlackAPI.__init__(self, config)

def insertDashboardParams(sitename, software, dashbJson, worker):
    """Insert dashboard params, like UID, ID, Version, Folder"""
    dashbJson = json.loads(dashbJson)
    # 1. Get dashboard UID, ID, Version from Grafana
    dashboard = worker.getDashboardByTitle(sitename)
    if dashboard:
        if 'uid' in dashboard:
            dashbJson['uid'] = dashboard['uid']
        if 'id' in dashboard:
            dashbJson['id'] = dashboard['id']
        if 'version' in dashboard:
            dashbJson['version'] = str(int(dashboard['version']) + 1)
        else:
            dashbJson['version'] = 1
    dashbJson = {'dashboard': dashbJson, 'overwrite': True}
    folderID = worker.getFolderID(software)
    if folderID:
        dashbJson['folderId'] = folderID
    dashbJson['message'] = "Script changes made on %s. See git for changes" % date.today()
    return dashbJson


def addDefaultDashboards(worker):
    """Add Default dashboards"""
    orgConfig = worker.getOrganizationConfig()
    for dashbFile, dashName in {'general-all-status.json': 'All Status (Variable)',
                               'general-full-dtn-monitoring.json': 'Full DTN Monitoring (Variable)',
                               'general-home.json': 'Home'}.items():
        dashbJson = readFile('../grafana-templates/dashboards/%s' % dashbFile)
        dashbJson = dashbJson.replace('REPLACEME_FOLDERID_SiteRM', str(worker.getFolderID('SiteRM')))
        dashbJson = dashbJson.replace('REPLACEME_FOLDERID_NSI', str(worker.getFolderID('NSI')))
        dashbJson = insertDashboardParams(dashName, 'General', dashbJson, worker)
        dashUpl = worker.addNewDashboard(dashbJson)
        if dashName == 'Home' and orgConfig['homeDashboardId'] != dashUpl['id']:
            orgConfig['homeDashboardId'] = dashUpl['id']
            worker.updateOrganizationConfig(orgConfig)

def updateCreateChannels(sitename, software, dashbJson, worker):
    """Update/Create Channels on Slack."""
    # 1 create channel
    # Remove space, dots and replace with _ and lower it
    siteNorm = sitename.replace(' ', '_').replace('.', '_').lower()
    channelInfo = worker.createChannel(siteNorm)
    # 2 Generate purpose
    monUrl = "%s%s" % (worker.config['api_url'], dashbJson['url'])
    purpose = "Alert notifications for %s %s.\nMonitoring URL: <%s>" % (software, sitename, monUrl)
    worker.setPurpose(channelInfo['id'], purpose)
    # 3 Upload new purpose


def addDashboard(sitename, software, worker):
    """Add SiteRM Dashboards to Grafana"""
    # 0. Get dashboard default template
    print("Update dashboard for %s" % sitename)
    src = '../grafana-templates/dashboards/default-%s.json' % software
    if os.path.isfile('../grafana-templates/dashboards/overwrite-%s.json' % sitename):
        print('GRAFANA Template overwrite for %s' % sitename)
        src = '../grafana-templates/dashboards/overwrite-%s.json' % sitename
    dst = '../grafana-templates/deployed-dashboards/%s.json' % sitename
    dashbJson = readFile(src)
    # 1. Get Alarm IDs and replace REPLACEME_SITENAME, REPLACEME_SOFTWARE,
    # REPLACEME_NOTIFICATION_ALL, REPLACEME_NOTIFICATION_SITE
    notfAll = worker.getAlertID('SENSE-ALL-ALARMS')
    notfSite = worker.getAlertID(sitename)
    dashbJson = dashbJson.replace('REPLACEME_SITENAME', sitename)
    dashbJson = dashbJson.replace('REPLACEME_SOFTWARE', software)
    dashbJson = dashbJson.replace('REPLACEME_NOTIFICATION_ALL', notfAll)
    dashbJson = dashbJson.replace('REPLACEME_NOTIFICATION_SITE', notfSite)
    # Insert dashboard params, like UID, ID, Version, Folder
    dashbJson = insertDashboardParams(sitename, software, dashbJson, worker)
    # Write New dashboard to Grafana
    tmp = worker.addNewDashboard(dashbJson)
    updateCreateChannels(sitename, software, tmp, worker)
    # Save dashboard changes to local file.
    writeFile(dst, json.dumps(dashbJson, sort_keys=True,
                              indent=2, separators=(',', ': ')))

def updateSiteRM(dirname, worker):
    """Loop via all SiteRM configs"""
    mappingFile = os.path.join(dirname, 'mapping.yaml')
    if not os.path.isfile(mappingFile):
        return
    mapping = loadYamlFile(mappingFile)
    for _key, val in mapping.items():
        if val.get('type', '') == 'FE' and val.get('config', ''):
            tmpD = os.path.join(dirname, val.get('config'))
            confFile = os.path.join(tmpD, 'main.yaml')
            if not os.path.isfile(confFile):
                return
            conf = loadYamlFile(confFile)
            for sitename in conf['general']['sites']:
                addDashboard(sitename, 'SiteRM', worker)
    return

def updateNSA(fname, worker):
    """Add All Network-RM Endpoints to Promeheus config file"""
    nrmMapping = loadYamlFile(fname)
    for name, vals in nrmMapping.items():
        addDashboard(name, 'NSI', worker)


def run():
    """Main run"""
    config = loadYamlFile('config.yaml')
    worker = Worker(config)
    worker.createAlerts()
    worker.getDashboards()
    worker.createFolder('SiteRM')
    worker.createFolder('NSI')
    addDefaultDashboards(worker)
    workdir = getSiteRMRepo()
    for dirName in os.listdir(workdir):
        siteConfDir = os.path.join(workdir, dirName)
        updateSiteRM(siteConfDir, worker)
    updateNSA('../configs/nsi-endpoints', worker)

if __name__ == "__main__":
    run()
