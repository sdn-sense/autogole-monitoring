"""Simple http server to serve metrics from file"""
import os.path
from flask import Flask, Response

app = Flask(__name__)

@app.route('/')
def hello_world():
    """Main page hello world"""
    return 'Hello! Go to /metrics to see the metrics.'

@app.route('/metrics')
def metrics():
    """Return metrics from file"""
    if os.path.isfile('/srv/xrootd-metrics'):
        with open('/srv/xrootd-metrics', 'rb') as fd:
            content = fd.read()
        return Response(content, status=200, content_type='text/plain; version=0.0.4')
    return Response("File not found", status=404, content_type='text/plain')
