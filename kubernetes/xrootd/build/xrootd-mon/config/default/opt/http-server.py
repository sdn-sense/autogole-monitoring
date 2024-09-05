import os.path

from flask import Flask
app = Flask(__name__)

@app.route('/')
def hello_world():
    return 'Hello! Go to /metrics to see the metrics.'

@app.route('/metrics')
def metrics():
    if os.path.isfile('/srv/xrootd-metrics'):
        with open('/srv/xrootd-metrics', 'rb') as fd:
            content = fd.read()
        return content.decode('utf-8')
    raise FileNotFoundError('/srv/xrootd-metrics')
