#!/usr/bin/env python3
import os
import re
import time
import logging
import subprocess
import traceback
from logging import StreamHandler
from datetime import datetime
from prometheus_client import Gauge, CollectorRegistry, generate_latest

def getStreamLogger(logLevel='DEBUG'):
    """ Get Stream Logger """
    levels = {'FATAL': logging.FATAL,
              'ERROR': logging.ERROR,
              'WARNING': logging.WARNING,
              'INFO': logging.INFO,
              'DEBUG': logging.DEBUG}
    logger = logging.getLogger()
    handler = StreamHandler()
    formatter = logging.Formatter("%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s",
                                  datefmt="%a, %d %b %Y %H:%M:%S")
    handler.setFormatter(formatter)
    if not logger.handlers:
        logger.addHandler(handler)
    logger.setLevel(levels[logLevel])
    return logger

class XRootDCache:
    """ XRootD Cache Worker """
    def __init__(self, logger):
        self.logger = logger
        self.prevdatetime = None
        self.currdatetime = None
        self.params = self._getParams()
        self.workdir = self.params['XRD_WORKDIR']
        self.lfn = None
        self.registry = None
        self.gauge = None
        self.runtimeGauge = None

    def __cleanRegistry(self):
        """Get new/clean prometheus registry."""
        self.registry = CollectorRegistry()

    def __cleanGauge(self):
        """Get new/clean prometheus gauge."""
        self.gauge = Gauge("xrootd_exit", "XRootD Exit Code",
                            ["hostname", "mode", "protocol", "name"],
                            registry=self.registry)
        self.runtimeGauge = Gauge("xrootd_runtime", "XRootD Command Runtime",
                                    ["hostname", "mode", "protocol", "name"],
                                    registry=self.registry)

    def _getLabels(self, hostname, mode, protocol):
        """Get Labels for Prometheus Gauge."""
        return {"hostname": hostname, "mode": mode,
                "protocol": protocol, "name": self.params['XRD_UNIQ_NAME']}

    def _getParams(self):
        out = {}
        # Mandatory ENV Variables
        for key in ['XRD_ENDPOINT', 'X509_USER_PROXY',
                    'XRD_WORKDIR', 'XRD_UNIQ_NAME',
                    'XRD_PATH']:
            out[key] = os.environ.get(key)
            if not out[key]:
                raise Exception(f'ENV Variable {key} not found. Fatal Error. Exiting.')
        for key in ['XRD_PROTOCOLS', 'XRD_MODES']:
            out[key] = os.environ.get(key)
            if ',' in out[key]:
                out[key] = out[key].split(',')
            else:
                out[key] = [out[key]]
        # Optional ENV Variables
        tmpKey = os.environ.get('XRD_UNIQ_WRITE')
        if tmpKey:
            out['XRD_UNIQ_WRITE'] = bool(tmpKey)
        else:
            out['XRD_UNIQ_WRITE'] = False
        # XRD_PROBE_METHOD controls how backend servers are discovered.
        # 'xrdmapc' (default): use the native XRootD manager query — works when
        #   the redirector exposes its subscriber list to external clients (e.g. Caltech).
        # 'curl': issue a HEAD request and parse the HTTP 307 Location header —
        #   used when xrdmapc is ACL-blocked (e.g. UCSD T2). Returns one backend
        #   per cycle (whichever the load-balancer chose at that moment).
        out['XRD_PROBE_METHOD'] = os.environ.get('XRD_PROBE_METHOD', 'xrdmapc').strip().lower()
        return out

    def _getLFN(self):
        """Get LFN"""
        currdate = self.currdatetime
        currLFN = '%s/%s/%s/%s/%s-cache-test-%s' % (self.params['XRD_PATH'],currdate.year,
                                                    currdate.month, currdate.day, currdate.hour,
                                                    self.params['XRD_UNIQ_NAME'].replace('.', '-').replace(':', '_'))
        if 'cache' in self.params['XRD_MODES']:
            currLFN = '%s/%s/%s/%s/%s-cache-test' % (self.params['XRD_PATH'], currdate.year,
                                                        currdate.month, currdate.day,
                                                        currdate.hour)
        if 'write' not in self.params['XRD_MODES'] and 'read' in self.params['XRD_MODES']:
            currLFN = '%s/%s/%s/%s/%s-cache-test' % (self.params['XRD_PATH'], currdate.year,
                                                        currdate.month, currdate.day,
                                                        currdate.hour)
        self.lfn = currLFN

    def _executeCmd(self, cmd, retries=2, retry_delay=5, log_failure_level=logging.CRITICAL):
        """Execute a shell command and return (stdout, stderr, exitCode, runtime).

        Retries up to `retries` additional times on non-zero exit, waiting
        `retry_delay * attempt` seconds between attempts (5 s, 10 s by default).
        stderr is always captured and returned so callers can inspect error output
        without needing to re-run the command or parse log files.

        log_failure_level: logging level used when the command exits non-zero.
        Pass logging.DEBUG for expected-to-fail calls (e.g. pre-delete before write)
        so they don't pollute the log with CRITICAL noise.
        """
        # Build the subprocess environment from the current pod environment, then
        # explicitly overlay BEARER_TOKEN / BEARER_TOKEN_FILE when present.
        # This ensures gfal2 can authenticate on the redirected leg of a transfer:
        # the XRootD redirector does not forward x509 credentials to the backend
        # origin server, but gfal2 re-sends the bearer token automatically on
        # every connection it opens, including redirected ones.
        subenv = os.environ.copy()
        for _var in ('BEARER_TOKEN', 'BEARER_TOKEN_FILE'):
            _val = os.environ.get(_var)
            if _val:
                subenv[_var] = _val

        stTime = int(time.time())
        stdout = b''
        stderr = b''
        exCode = 1

        for attempt in range(retries + 1):
            if attempt > 0:
                wait = retry_delay * attempt
                self.logger.warning(f'Retry {attempt}/{retries} for cmd: {cmd} (waiting {wait}s)')
                time.sleep(wait)
            self.logger.info(f'Call command {cmd}')
            result = subprocess.run(cmd, shell=True, env=subenv,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout = result.stdout
            stderr = result.stderr
            exCode = result.returncode
            if exCode == 0:
                self.logger.debug(f'Got Exit: {exCode}, Cmd: {cmd}')
                break
            self.logger.log(log_failure_level,
                            f'Got Exit: {exCode}, Cmd: {cmd}, Stderr: {stderr.decode(errors="replace").strip()}')

        endTime = int(time.time())
        totalRuntime = endTime - stTime
        return stdout, stderr, exCode, totalRuntime

    def _writeFile(self, protocol, hostname):
        """Write File to XRootD"""
        uniqname = hostname.replace('.', '-').replace(':', '_')
        lfn = f"{self.lfn}-{uniqname}-{protocol}"
        # Pre-delete the destination before writing to avoid gfal-copy -f
        # failing with HTTP 404 on the implicit unlink call when the file does
        # not yet exist (observed on UCSD T2 DTNs with some gfal2 versions).
        # Exit code is intentionally ignored — a 404/2 here is expected and fine.
        # log_failure_level=DEBUG so the inevitable exit-2 on the first cycle
        # of each hour doesn't flood the log with alarming CRITICAL lines.
        pre_rm_cmd = f"timeout 30 gfal-rm {protocol}://{hostname}/{lfn}"
        self._executeCmd(pre_rm_cmd, retries=0, log_failure_level=logging.DEBUG)
        # -p: create parent directories if they don't exist.
        # -f: force overwrite — safe now because we pre-deleted above.
        cmd = f"timeout 30 gfal-copy -p -f {self.workdir}/xrd-cache-test {protocol}://{hostname}/{lfn}"
        _, _stderr, exitCode, runtime = self._executeCmd(cmd)
        self.gauge.labels(**self._getLabels(hostname, "write", protocol)).set(exitCode)
        self.runtimeGauge.labels(**self._getLabels(hostname, "write", protocol)).set(runtime)
        return exitCode

    def preparefiles(self):
        """ Prepare Files for xrdcp"""
        if 'write' not in self.params['XRD_MODES']:
            return []
        hostname = self.params['XRD_ENDPOINT']
        content = f"This is a test file for xrdcp, created at {self.currdatetime}"
        if os.path.isfile(f'{self.workdir}/xrd-cache-test'):
            os.remove(f'{self.workdir}/xrd-cache-test')
        with open(f'{self.workdir}/xrd-cache-test', 'w', encoding='utf-8') as fd:
            fd.write(content)
        # Make file size of 16MB
        with open(f'{self.workdir}/xrd-cache-test', 'a', encoding='utf-8') as fd:
            fd.truncate(16 * 1024 * 1024)  # 16MB in bytes
        exitCodes = []
        for protocol in self.params['XRD_PROTOCOLS']:
            exitCode = self._writeFile(protocol, hostname)
            exitCodes.append(exitCode)
        if 'read' in self.params['XRD_MODES']:
            uniqname = hostname.replace('.', '-').replace(':', '_')
            for protocol in self.params['XRD_PROTOCOLS']:
                cmd = f"timeout 30 gfal-copy -f {protocol}://{hostname}/{self.lfn}-{uniqname}-{protocol} /dev/null"
                _, _stderr, exitCode, runtime = self._executeCmd(cmd)
                self.gauge.labels(**self._getLabels(hostname, "read", protocol)).set(exitCode)
                self.runtimeGauge.labels(**self._getLabels(hostname, "read", protocol)).set(runtime)
        if not any(exitCodes):
            self.prevdatetime = self.currdatetime
        return exitCodes

    def _probeRedirectTarget(self, endpoint):
        """Probe the redirector's HTTPS endpoint with a HEAD request and extract
        the Location: header from the HTTP 307 response.

        Used when XRD_PROBE_METHOD=curl — i.e. when xrdmapc subscriber queries
        are ACL-blocked externally (e.g. UCSD T2). Returns the backend DTN that
        the load-balancer chose at this moment as 'host:port', ready to be used
        in gfal-copy URLs. Returns None on failure or missing Location header.
        """
        cmd = f"curl -skI --max-time 10 https://{endpoint}/"
        self.logger.info(f'Redirect probe: curl -skI https://{endpoint}/')
        result = subprocess.run(cmd, shell=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            self.logger.warning(f'Redirect probe failed for {endpoint} (exit {result.returncode})')
            return None
        for line in result.stdout.decode(errors='replace').splitlines():
            if line.lower().startswith('location:'):
                # e.g. "location: https://dtn-f011-05.t2.ucsd.edu:1094/"
                loc = line.split(':', 1)[1].strip()
                # Strip scheme and trailing slash → "dtn-f011-05.t2.ucsd.edu:1094"
                target = re.sub(r'^https?://', '', loc).rstrip('/')
                self.logger.info(f'Redirect probe: {endpoint} → {target}')
                return target
        self.logger.warning(f'Redirect probe: no Location header from {endpoint}')
        return None

    def _runBackendTests(self, host, sharedFsWrittenLFNs):
        """Run per-backend read/write/delete gfal tests against a single server.

        Shared by both the xrdmapc path (iterates over all Srv lines) and the
        curl path (single DTN returned by _probeRedirectTarget). Appends any
        written LFNs to sharedFsWrittenLFNs for post-loop cleanup.
        """
        uniqname = host.replace('.', '-').replace(':', '_')
        if self.params['XRD_UNIQ_WRITE']:
            # Nodes with separate (non-shared) storage.
            if 'write' in self.params['XRD_MODES']:
                for protocol in self.params['XRD_PROTOCOLS']:
                    self._writeFile(protocol, host)
            if 'read' in self.params['XRD_MODES']:
                for protocol in self.params['XRD_PROTOCOLS']:
                    cmd = f"timeout 30 gfal-copy -f {protocol}://{host}/{self.lfn}-{uniqname}-{protocol} /dev/null"
                    _, _stderr, exitCode, runtime = self._executeCmd(cmd)
                    self.gauge.labels(**self._getLabels(host, "read", protocol)).set(exitCode)
                    self.runtimeGauge.labels(**self._getLabels(host, "read", protocol)).set(runtime)
            if 'delete' in self.params['XRD_MODES']:
                for protocol in self.params['XRD_PROTOCOLS']:
                    cmd = f"timeout 30 gfal-rm -r {protocol}://{host}/{self.lfn}-{uniqname}-{protocol}"
                    _, _stderr, exitCode, runtime = self._executeCmd(cmd)
                    self.gauge.labels(**self._getLabels(host, "delete", protocol)).set(exitCode)
                    self.runtimeGauge.labels(**self._getLabels(host, "delete", protocol)).set(runtime)
        else:
            # Shared FS (NFS/network mount): preparefiles() already wrote one file
            # per protocol via the redirector, visible to all servers on shared FS.
            redirector_uniqname = self.params['XRD_ENDPOINT'].replace('.', '-').replace(':', '_')
            # Step 1 — read the redirector-written file directly from this server.
            if 'read' in self.params['XRD_MODES']:
                for protocol in self.params['XRD_PROTOCOLS']:
                    lfn = f"{self.lfn}-{redirector_uniqname}-{protocol}"
                    cmd = f"timeout 30 gfal-copy -f {protocol}://{host}/{lfn} /dev/null"
                    _, _stderr, exitCode, runtime = self._executeCmd(cmd)
                    self.gauge.labels(**self._getLabels(host, "read", protocol)).set(exitCode)
                    self.runtimeGauge.labels(**self._getLabels(host, "read", protocol)).set(runtime)
            # Step 2 — write a unique file directly to this server; track for cleanup.
            if 'write' in self.params['XRD_MODES']:
                for protocol in self.params['XRD_PROTOCOLS']:
                    self._writeFile(protocol, host)
                    sharedFsWrittenLFNs.append(f"{self.lfn}-{uniqname}-{protocol}")

    def main(self):
        """ Main Method"""
        self.__cleanRegistry()
        self.__cleanGauge()
        self.currdatetime = datetime.utcnow()
        self._getLFN()
        self.preparefiles()
        # placeholder so the xrdmapc block below compiles — reassigned there
        mngrexitCode = 0
        retOutput = b''
        mngrruntime = 0

        # Shared-FS: accumulate per-server LFNs written during the loop so they
        # can all be deleted via the redirector after every server has been tested.
        sharedFsWrittenLFNs = []

        # -----------------------------------------------------------------------
        # Backend discovery: xrdmapc or curl depending on XRD_PROBE_METHOD.
        # -----------------------------------------------------------------------
        probe_method = self.params['XRD_PROBE_METHOD']
        # mngrOK sentinel values:
        #   0   — discovery succeeded and at least one backend was tested
        #   2   — exit 0 but no Srv lines (ACL-blocked / socket error in output)
        #   100 — hard failure (xrdmapc non-zero exit, or curl probe found no target)
        mngrOK = 100

        if probe_method == 'curl':
            # UCSD T2 style: xrdmapc subscriber query is ACL-blocked externally.
            # Use a single HTTP HEAD to discover whichever DTN the redirector
            # load-balances to this cycle. Coverage accumulates over many cycles.
            target = self._probeRedirectTarget(self.params['XRD_ENDPOINT'])
            if target:
                mngrOK = 0
                self._runBackendTests(target, sharedFsWrittenLFNs)
            else:
                mngrOK = 100
                self.logger.warning(
                    f"curl probe returned no target for {self.params['XRD_ENDPOINT']}; "
                    f"skipping per-backend tests")
        else:
            # Default: xrdmapc — works when the redirector exposes its subscriber
            # list to external clients (e.g. T2_US_Caltech).
            cmd = f"timeout 30 xrdmapc --list all {self.params['XRD_ENDPOINT']}"
            self.logger.info('Calling %s', cmd)
            retOutput, _stderr, mngrexitCode, mngrruntime = self._executeCmd(cmd, retries=0)
            if mngrexitCode:
                # Hard failure: xrdmapc itself returned a non-zero exit code.
                self.gauge.labels(**self._getLabels(self.params['XRD_ENDPOINT'], "xrdmapc", "xrootd")).set(mngrexitCode)
                self.runtimeGauge.labels(**self._getLabels(self.params['XRD_ENDPOINT'], "xrdmapc", "xrootd")).set(mngrruntime)
                return
            # Exit 0 but may have no Srv lines (ACL-blocked). Start at 2 and
            # promote to 0 only when the first Srv line is found.
            mngrOK = 2
            self.logger.info(f"Returned out from Redirector: {retOutput}")
            for line in retOutput.decode("utf-8").split('\n'):
                if not line:
                    break
                line = line.strip()
                if line.startswith('Srv '):
                    host = line.split()[1]
                else:
                    self.logger.debug(f"Skipping line: {line}")
                    continue
                mngrOK = 0
                self._runBackendTests(host, sharedFsWrittenLFNs)
            if mngrOK == 2:
                self.logger.info(
                    f"xrdmapc: exit 0 but no Srv lines found for "
                    f"{self.params['XRD_ENDPOINT']} — ACL-blocked or socket error in output. "
                    f"Consider setting XRD_PROBE_METHOD=curl for this endpoint.")

        # Step 3 (shared FS only) — after ALL servers have been tested, delete every
        # written file via the redirector: the redirector-written files from preparefiles()
        # plus all per-server-written files accumulated in sharedFsWrittenLFNs.
        # Uses davs:// for all deletes — root:// is not allowlisted for gfal calls;
        # one protocol is sufficient since the files are plain bytes on shared storage
        # (the protocol suffix is just part of the filename).
        if not self.params['XRD_UNIQ_WRITE'] and 'delete' in self.params['XRD_MODES']:
            redirector_uniqname = self.params['XRD_ENDPOINT'].replace('.', '-').replace(':', '_')
            redir_lfns = [f"{self.lfn}-{redirector_uniqname}-{protocol}"
                          for protocol in self.params['XRD_PROTOCOLS']]
            all_lfns = redir_lfns + sharedFsWrittenLFNs
            self.logger.info(f"Shared FS cleanup: deleting {len(all_lfns)} file(s) via redirector")
            for lfn in all_lfns:
                cmd = f"timeout 30 gfal-rm davs://{self.params['XRD_ENDPOINT']}/{lfn}"
                _, _stderr, exitCode, runtime = self._executeCmd(cmd)
                self.gauge.labels(**self._getLabels(self.params['XRD_ENDPOINT'], "delete", "davs")).set(exitCode)
                self.runtimeGauge.labels(**self._getLabels(self.params['XRD_ENDPOINT'], "delete", "davs")).set(runtime)

        self.gauge.labels(**self._getLabels(self.params['XRD_ENDPOINT'], "xrdmapc", "xrootd")).set(mngrOK)
        self.runtimeGauge.labels(**self._getLabels(self.params['XRD_ENDPOINT'], "xrdmapc", "xrootd")).set(mngrruntime)

    def execute(self):
        """Execute Main Program.

        Returns a dict: {"runtime": int, "success": bool}.
        success=False means main() raised an unexpected exception — metrics may
        be incomplete for that cycle but the outer loop continues running.
        """
        startTime = int(time.time())
        self.logger.info('Running Main')
        success = True
        endTime = startTime
        try:
            self.main()
            endTime = int(time.time())
            totalRuntime = endTime - startTime
            self.runtimeGauge.labels(**self._getLabels('MAIN_PROGRAM', "main", "xrootd")).set(totalRuntime)
            data = generate_latest(self.registry)
            with open(f'{self.workdir}/xrootd-metrics', 'wb') as fd:
                fd.write(data)
        except Exception:
            endTime = int(time.time())
            totalRuntime = endTime - startTime
            success = False
            self.logger.critical(
                f"Unhandled exception in main() after {totalRuntime}s — "
                f"metrics may be incomplete for this cycle:\n{traceback.format_exc()}")
        totalRuntime = endTime - startTime
        self.logger.info('StartTime: %s, EndTime: %s, Runtime: %s', startTime, endTime, totalRuntime)
        return {"runtime": totalRuntime, "success": success}


if __name__ == "__main__":
    LOGGER = getStreamLogger()
    xcacheWorker = XRootDCache(LOGGER)
    while True:
        result = xcacheWorker.execute()
        if not result["success"]:
            LOGGER.warning("Cycle completed with an unhandled error — check logs above for details")
        sleepTime = int(300 - result["runtime"])
        if sleepTime > 0:
            LOGGER.info("Sleeping %s seconds", sleepTime)
            time.sleep(int(sleepTime))
