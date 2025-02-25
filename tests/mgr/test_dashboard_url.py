import re

from cli.cephadm.cephadm import CephAdm
from cli.exceptions import OperationFailedError, ResourceNotFoundError
from cli.utilities.utils import (
    get_running_containers,
    get_service_id,
    restart_container,
)
from utility.log import Log

log = Log(__name__)


def check_dashboard_url(ceph_cluster):
    """
    Get dashboard URL from mgr service and check if dashboard URL does not
    bind to host.containers.internal
    """
    url = ceph_cluster.get_mgr_services().get("dashboard")
    if not url:
        raise ResourceNotFoundError("Dashboard not configured")

    if "host.containers.internal" in url:
        log.error(f"Dashboard URL {url} is not binding to IP of hostname")
        return False

    log.info(f"Dashboard URL is {url}")
    return True


def run(ceph_cluster, **kw):
    """
    Check if the Ceph dashboard URL consists of host IP and
    does not bind to host.containers.internal before and after
    container restart and mgr failover is performed
    """
    # Get mgr node
    node = ceph_cluster.get_nodes(role="mgr")[0]

    # Check if dashboard URL does not bind to host.containers.internal
    check_dashboard_url(ceph_cluster)
    log.info("Dashboard URL does not bind to host.containers.internal")

    # Get container id for mgr daemon
    container_id = get_running_containers(
        sudo=True, node=node, format="{{.ID}}", expr="name=mgr"
    )[0]

    # Restart container for mgr daemon
    restart_container(node, container_id)

    # Check if dashboard URL does not bind to host.containers.internal
    check_dashboard_url(ceph_cluster)
    log.info("Dashboard URL does not bind to host.containers.internal")

    # Refresh the ceph orch ps command
    CephAdm(node).ceph.orch.ps(refresh="True")

    # Fail mgr service
    regex = r"(?<=@mgr\.)[\w.-]+(?=\.service)"
    out = get_service_id(node, "mgr")[0]
    mgr_service = re.findall(regex, out)[0]
    if CephAdm(node).ceph.mgr.fail(mgr=mgr_service):
        raise OperationFailedError("Failed to execute `ceph mgr fail` command")

    # Check if dashboard URL does not bind to host.containers.internal
    check_dashboard_url(ceph_cluster)
    log.info("Dashboard URL does not bind to host.containers.internal")
    return 0
