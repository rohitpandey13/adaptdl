# Copyright 2020 Petuum, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from contextlib import contextmanager
from multiprocessing import Process, Event

import kubernetes

from mitmproxy.options import Options
from mitmproxy.proxy.config import ProxyConfig
from mitmproxy.proxy.server import ProxyServer
from mitmproxy.tools.dump import DumpMaster
from portpicker import pick_unused_port
from urllib.parse import urlparse


@contextmanager
def service_proxy(namespace, service, listen_host="127.0.0.1",
                  listen_port=None, verbose=False):
    """
    This is a context manager that runs a background proxy to a Kubernetes
    service, for the duration of the managed context. The local Kubernetes
    context should have access to the /proxy verb of the given service.

    For example,

    .. code-block:: python

       with service_proxy("default", "my-service", listen_port=8080) as addr:
           print(addr)  # Should print: 127.0.0.1:8080
           # In this block, access my-service using 127.0.0.1:8080.

    Arguments:
        namespace (str): namespace of the target Kubernetes service.
        service (str): name of the target Kubernetes service, in the form
            [https:]service_name[:port_name].
        listen_host (str): local address to bind the proxy.
        listen_port (int): local port to bind the proxy. If None, selects an
            arbitrary free port.
        verbose (bool): if True, prints extra logs from the background proxy.
    """
    listen_port = pick_unused_port() if listen_port is None else listen_port
    event = Event()
    child = Process(
        target=_run_proxy, name="mitmproxy",
        args=(event, namespace, service, listen_host, listen_port, verbose),
    )
    child.start()
    event.wait()  # Wait for child to bind the port.
    try:
        yield f"{listen_host}:{listen_port}"
    finally:
        child.terminate()
        child.join()


def _run_proxy(event, namespace, service, listen_host, listen_port, verbose):
    # Run mitmproxy as a reverse-proxy to the Kubernetes ApiServer under the
    # subpath /api/v1/namespaces/{namespace}/services/{service}/proxy. See
    # https://k8s.io/docs/tasks/administer-cluster/access-cluster-services.
    client = kubernetes.config.new_client_from_config()
    prefix = f"/api/v1/namespaces/{namespace}/services/{service}/proxy"
    options = Options(listen_host=listen_host, listen_port=listen_port,
                      mode=f"reverse:{client.configuration.host}")
    master = DumpMaster(options, with_termlog=verbose, with_dumper=verbose)
    options.keep_host_header = True
    options.ssl_insecure = True
    master.server = ProxyServer(ProxyConfig(options))
    master.addons.add(_Addon(client, prefix))
    event.set()  # Port is bound by this time, unblock parent process.
    master.run()


class _Addon(object):
    def __init__(self, client, prefix):
        self.client = client
        self.prefix = prefix

    def requestheaders(self, flow):
        # Modify outgoing request headers.
        flow.request.stream = True  # Stream for better performance.
        flow.request.path = self.prefix + flow.request.path
        self.client.update_params_for_auth(  # Inject Kubernetes authorization.
            flow.request.headers, flow.request.query, ["BearerToken"])

    def responseheaders(self, flow):
        # Modify incoming response headers.
        flow.response.stream = True  # Stream for better performance.
        if "location" in flow.response.headers:
            # Kubernetes ApiServer re-writes the Location header, undo it here.
            # See https://github.com/kubernetes/kubernetes/pull/52556.
            url = urlparse(flow.response.headers["location"])
            if url.path.startswith(self.prefix):
                url = url._replace(path=url.path[len(self.prefix):])
            flow.response.headers["location"] = url.geturl()
