# pip3 install xmltodict
import xml
import base64
import gzip
import pprint
import xmltodict
import requests
from yaml import safe_load as yload

cert = ('/root/networkrm-cert.pem', '/root/networkrm-key.pem')
bundle = '/root/bundle-ca.pem'

def xml_to_json(msgIn):
    """Xml to Json"""
    try:
        return xmltodict.parse(msgIn)
    except xml.parsers.expat.ExpatError as ex:
        print(ex)
        print(msgIn)
        return {}

def decode_msg(msg):
    """Decode base64 gziped message"""
    base64_bytes = msg.encode('utf-8')
    message_bytes = base64.b64decode(base64_bytes)
    return gzip.decompress(message_bytes)

def loadYamlFile(fname):
    """Load yaml file"""
    with open(fname, 'r', encoding='utf-8') as fd:
        return yload(fd.read())

def getData(url):
    """Get data from url"""
    print('GETDATA-------', url)
    try:
        response = requests.get(url, cert=cert, verify=bundle, timeout=5)
        return response, 0
    except requests.exceptions.ConnectionError as ex:
        print('Getdata-error-verify-bundle', ex)
    try:
        response = requests.get(url, cert=cert, verify=False, timeout=5)
        return response, 1
    except requests.exceptions.ConnectionError as ex:
        print('Getdata-error-verify-false', ex)
    try:
        response = requests.get(url, timeout=5)
        return response, 2
    except requests.exceptions.ConnectionError as ex:
        print('Getdata-error-nocert-key', ex)
    return {}, -1

class allNSIEndpoints():
    """All NSI Endpoints Class"""
    def __init__(self):
        self.out = {}

    def parseIndividualData(self, dataIn, nsa):
        """Parse individual data of entry"""
        self.out.setdefault(nsa, {'soap': [], 'url': [], 'location': {}})
        for _, val in dataIn.items():
            if 'interface' not in val:
                continue
            if 'location' in val:
                print(nsa, val['location'])
                self.out[nsa]['location'] = val['location']
            for item in val.get('interface', []):
                url = item.get('href', '')
                utype = item.get('type', '')
                if url and utype:
                    if utype.endswith('soap'):
                        if url not in self.out[nsa]['soap']:
                            print('SOAP', url)
                            self.out[nsa]['soap'].append(url)
                        else:
                            print('SOAP_DEFINED_ALREADY', url)
                    elif url not in self.out[nsa]['url']:
                        print('URL', url)
                        self.out[nsa]['url'].append(url)
                    else:
                        print('URL_DEFINED_ALREADY', url)

    def parseDDS(self, ddsurl):
        """Parse DDS and get all URLs from it"""
        print('DDSURL', ddsurl)
        print('-'*50)
        response, respType = getData(ddsurl)
        if not response:
            return
        data = xml_to_json(response.text)
        print('Debug DDS Output START')
        print(response.text)
        print(data)
        print('Debug DDS Output End')
        # TODO: It should not depend on ns2 as it can change.
        for entry in data.get('ns2:collection', {}).get('ns2:documents', {}).get('ns2:document', []):
            nsa = entry.get('nsa', '')
            base64msg = entry.get('content', {}).get('#text', '')
            if not nsa or not base64msg:
                print('WARNING. This entry does not have either NSA, or base64msg defined. %s'  % entry)
                continue
            decodedmsg = decode_msg(base64msg)
            decodedjson = xml_to_json(decodedmsg)
            self.parseIndividualData(decodedjson, nsa)
        print('-'*50)

    def execute(self):
        """Main execute - query all NSI Endpoints and get URLS"""
        config = loadYamlFile('../configs/nsi-endpoints')
        for ddsurl in config['ddsUrl']:
            self.parseDDS(ddsurl)
        for key, val in config['discovery'].items():
            if val and val.get('urls', []):
                self.out.setdefault(key, {'soap': [], 'url': [], 'location': {}})
                for url in val['urls']:
                    if url not in self.out[key]['url']:
                        print('CONFIG', url)
                        self.out[key]['url'].append(url)
        for key, vals in self.out.items():
            print(key, vals)
            if key not in config['discovery']:
                print('MISSING SITENAME!!! %s' % key)
                print('='*50)
            for url in vals.get('url'):
                response, respType = getData(url)
                if not response:
                    continue
                data = xml_to_json(response.text)
                if not data:
                    continue
                self.parseIndividualData(data, key)

if __name__ == "__main__":
    nsiclass = allNSIEndpoints()
    nsiclass.execute()
    pprint.pprint(nsiclass.out)

