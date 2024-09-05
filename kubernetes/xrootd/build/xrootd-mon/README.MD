# Tests:

```
# XRootD Endpoint
XRD_ENDPOINT="xrootd-redir.ultralight.org:1094"
# X509 User Proxy
X509_USER_PROXY="/tmp/x509up_u0"
# XRootD Protocols supported
XRD_PROTOCOLS="root"
# XRootD Modes supported (read, write, cache)
XRD_MODES="read"
# Tmp directory for xrootd files
XRD_WORKDIR="/srv/"
# XRootD Path to use on tests (should be a directory)
# It will add /{year}/{month}/{day}/ to the path
XRD_PATH=/store/temp/user/jbalcas.kube-xrootd-test
# XRootD Unique Name (just a label for prometheus)
XRD_UNIQ_NAME="xrootd-redir.ultralight.org"
# XRootD Unique Write (True - Write to all endpoints under redirector)
XRD_UNIQ_WRITE=True/False
```


XRD_ENDPOINT="xrootd-redir-stageout.ultralight.org:1095"
X509_USER_PROXY="/tmp/x509up_u0"
XRD_PROTOCOLS="root,davs,https"
XRD_MODES="read,write"
XRD_WORKDIR="/srv/"

XRD_ENDPOINT="xcache.ultralight.org:2040"
X509_USER_PROXY="/tmp/x509up_u0"
XRD_PROTOCOLS="root"
XRD_MODES="cache"
XRD_WORKDIR="/srv/"

XRD_ENDPOINT="xcache-nvme.ultralight.org:1094"
X509_USER_PROXY="/tmp/x509up_u0"
XRD_PROTOCOLS="root"
XRD_MODES="cache"
XRD_WORKDIR="/srv/"

# Create fake files for xcache (only needed if you test an xcache endpoint)
```
import os
from datetime import datetime, timedelta

for i in range(0,10000):
    currdate = datetime.utcnow() + timedelta(hours=i)
    content = f"This is a test file for xrdcp, created at {currdate}"
    fname = '/storage/cms/store/user/jbalcas/cache-tests/%s/%s/%s/' % (currdate.year, currdate.month, currdate.day)
    if not os.path.isdir(fname):
        os.makedirs(fname)
        print(f'dirCreated, {fname}')
    fname += "%s-cache-test" % currdate.hour
    print(fname)
    if os.path.isfile(fname):
        continue
    with open(fname, 'w', encoding='utf-8') as fd:
        fd.write(content)
```
