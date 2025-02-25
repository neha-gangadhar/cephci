"""
This test module is used to deploy a stretch cluster on the given set of nodes with location attributes added
during deployment via spec file.

Note that all the daemons need to be co-located on the cluster. Esp mon + OSD daemons for the data sites
Issue:
    There won't be any clear site mentioned for the mons daemons to be added, if they are in the data sites,
     and do not contain OSDs. Currently, there is no intelligence to smartly add the mon crush locations.
     ( if none already provided with mon deployments, or otherwise specified )

Example of the cluster taken from the MDR deployment guide for ODF.
ref: https://access.redhat.com/documentation/en-us/red_hat_openshift_data_foundation/4.12/html/
configuring_openshift_data_foundation_disaster_recovery_for_openshift_workloads/metro-dr-solution#hardware_requirements

"""

import re
import time

from ceph.ceph_admin import CephAdmin
from ceph.rados.core_workflows import RadosOrchestrator
from tests.rados.monitor_configurations import MonElectionStrategies
from tests.rados.stretch_cluster import (
    setup_crush_rule,
    setup_crush_rule_with_no_affinity,
    wait_for_clean_pg_sets,
)
from utility.log import Log

log = Log(__name__)


def run(ceph_cluster, **kw):
    """
    enables connectivity mode and deploys stretch cluster with arbiter mon node
    Args:
        ceph_cluster (ceph.ceph.Ceph): ceph cluster
    """

    log.info("Deploying stretch cluster with arbiter mon node")
    log.info(run.__doc__)
    config = kw.get("config")
    cephadm = CephAdmin(cluster=ceph_cluster, **config)
    rados_obj = RadosOrchestrator(node=cephadm)
    mon_obj = MonElectionStrategies(rados_obj=rados_obj)
    client_node = ceph_cluster.get_nodes(role="client")[0]
    stretch_rule_name = config.get("stretch_rule_name", "stretch_rule")
    no_affinity_crush_rule = config.get("no_affinity", False)
    tiebreaker_mon_site_name = config.get("tiebreaker_mon_site_name", "arbiter")

    log.debug("Running pre-checks to deploy Stretch mode on the cluster")

    # Check-1 : Only 2 data sites permitted on cluster
    # getting the CRUSH buckets added into the cluster
    cmd = "ceph osd tree"
    buckets = rados_obj.run_ceph_command(cmd)
    dc_buckets = [d for d in buckets["nodes"] if d.get("type") == "datacenter"]
    if len(dc_buckets) > 2:
        log.error(
            f"There can only be two data sites in the stretch mode. Buckets present in cluster: {dc_buckets}"
        )
        raise Exception("Stretch mode cannot be enabled")
    dc_1 = dc_buckets.pop()
    dc_2 = dc_buckets.pop()

    # Check-2: 5 mons should be present on the cluster
    # Getting the mon daemons configured on the cluster
    cmd = "ceph mon dump"
    out = rados_obj.run_ceph_command(cmd)
    mons_dict = out["mons"]
    log.debug(f"Mons present on the cluster : {[mon for mon in mons_dict]}")
    if len(mons_dict) != 5:
        log.error(
            f"Stretch Cluster needs 5 mons to be present on the cluster. 2 for each DC & 1 for arbiter node"
            f"Currently configured on cluster : {len(mons_dict)}"
        )
        raise Exception("Stretch mode cannot be enabled")

    # Check-3: Both sites should be of equal weights
    log.debug(
        "Checking if thw weights of two DC's are same."
        " Currently, Stretch mode requires the site weights to be same."
    )
    cmd = "ceph osd tree"
    tree_op, _ = client_node.exec_command(cmd=cmd, sudo=True)
    datacenters = re.findall(r"\n(-\d+)\s+([\d.]+)\s+datacenter\s+(\w+)", tree_op)
    weights = [(weight, name) for _, weight, name in datacenters]
    for dc in weights:
        log.debug(f"Datacenter: {dc[0]}, Weight: {dc[1]}")
    if weights[0][0] != weights[1][0]:
        log.error(
            "The two site weights are not same. Currently, Stretch mode requires the site weights to be same."
        )
        raise Exception("Stretch mode cannot be enabled")

    # Check-4: Only replicated pools are allowed on the cluster with default rule
    # Finding any stray EC pools that might have been left on cluster
    pool_dump = rados_obj.run_ceph_command(cmd="ceph osd dump")
    for entry in pool_dump["pools"]:
        if entry["type"] != 1 and entry["crush_rule"] != 0:
            log.error(
                f"A non-replicated pool found : {entry['pool_name']}, With non-default Crush Rule"
                "Currently Stretch mode supports only Replicated pools."
            )
            raise Exception("Stretch mode cannot be enabled")

    log.info(
        "Completed Pre-Checks on the cluster, able to deploy Stretch mode on the cluster"
    )

    # Setting up CRUSH rules on the cluster
    if no_affinity_crush_rule:
        # Setting up CRUSH rules on the cluster without read affinity
        if not setup_crush_rule_with_no_affinity(
            node=client_node,
            rule_name=stretch_rule_name,
        ):
            log.error("Failed to Add crush rules in the crush map")
            raise Exception("Stretch mode deployment Failed")
    else:
        if not setup_crush_rule(
            node=client_node,
            rule_name=stretch_rule_name,
            site1=dc_1["name"],
            site2=dc_2["name"],
        ):
            log.error("Failed to Add crush rules in the crush map")
            raise Exception("Stretch mode deployment Failed")

    # Sleeping for 5 sec for the crush rules to be active
    time.sleep(5)

    # Waiting for Cluster to be active + Clean post Crush changes.
    wait_for_clean_pg_sets(rados_obj)

    # Setting up the election strategy on the cluster to connectivity
    if not mon_obj.set_election_strategy(mode="connectivity"):
        log.error("could not set election strategy to connectivity mode")
        raise Exception("Stretch mode deployment Failed")

    # Sleeping for 2 seconds after strategy update.
    time.sleep(2)

    # Checking updated election strategy in mon map
    strategy = mon_obj.get_election_strategy()
    if strategy != 3:
        log.error(
            f"cluster created election strategy other than connectivity, i.e {strategy}"
        )
        raise Exception("Stretch mode deployment Failed")
    log.info("Enabled connectivity mode on the cluster")

    def set_mon_crush_location(dc) -> bool:
        """
        Method to set the CRUSH location for the mon daemons
        Args:
            dc: datacenter bucket dump collected from ceph osd crush tree
        Returns:
            (bool) True -> Pass | False -> Fail
        """
        log.debug(f"setting up mon daemons from site : {dc['name']} ")
        for crush_id in dc["children"]:
            for entry in buckets["nodes"]:
                if entry.get("id") == crush_id:
                    if entry["name"] in [mon["name"] for mon in mons_dict]:
                        cmd = f"ceph mon set_location {entry['name']} datacenter={dc['name']}"
                        try:
                            rados_obj.run_ceph_command(cmd)
                        except Exception:
                            log.error(
                                f"Failed to set location on mon : {entry['name']}"
                            )
                            return False
        log.debug(
            "Iterated through all the children of the passed bucket and set the location attributes"
        )
        return True

    # Setting up the mon locations on the cluster
    if not (set_mon_crush_location(dc=dc_1) & set_mon_crush_location(dc=dc_2)):
        log.error("Failed to set Crush locations to the Data Site mon daemons")
        raise Exception("Stretch mode deployment Failed")

    # Set-up locations on the Data sites. now 4 mons should be with location attributes added.
    mon_dump_cmd = "ceph mon dump"
    mons = rados_obj.run_ceph_command(mon_dump_cmd)
    if len([mon for mon in mons["mons"] if mon["crush_location"] == "{}"]) != 1:
        log.error(
            f"Cluster does not have 4 data site mons with location attributes added"
            f"Added hosts : {[entry['name'] for entry in mons['mons'] if entry.get('crush_location') != '{}']}"
        )
        raise Exception("Stretch mode deployment Failed")

    # Directly selecting tiebreaker mon as we have confirmed only 1 mon exists without location
    tiebreaker_mon = [
        entry["name"] for entry in mons["mons"] if entry.get("crush_location") == "{}"
    ][0]
    # Setting up CRUSH location on the final Arbiter mon
    arbiter_cmd = (
        f"ceph mon set_location {tiebreaker_mon} datacenter={tiebreaker_mon_site_name}"
    )
    try:
        rados_obj.run_ceph_command(arbiter_cmd)

        # Verifying is tiebreaker mon is set with CRUSH locations successfully via mon dump
        mons = rados_obj.run_ceph_command(mon_dump_cmd)
        for mon in mons["mons"]:
            if mon["name"] == tiebreaker_mon:
                if mon["crush_location"] == "{}":
                    log.error("Location attribute not added on the arbiter mon")
                    raise Exception("Stretch mode deployment Failed")
                break
    except Exception:
        log.error(f"Failed to set location on mon : {entry['name']}")
        raise Exception("Stretch mode deployment Failed")

    log.info("Set-up CRUSH location attributes on all the mon daemons")

    # Enabling Stretch mode on the cluster
    # Enabling the stretch cluster mode
    log.info(f"tiebreaker node provided: {tiebreaker_mon}")
    cmd = (
        f"ceph mon enable_stretch_mode {tiebreaker_mon} {stretch_rule_name} datacenter"
    )
    try:
        cephadm.shell([cmd])
    except Exception as err:
        log.error(
            f"Error while enabling stretch rule on the datacenter. Command : {cmd}"
        )
        log.error(err)
        raise Exception("Stretch mode deployment Failed")
    time.sleep(5)

    # Checking the stretch mode status
    stretch_details = rados_obj.get_stretch_mode_dump()
    if not stretch_details["stretch_mode_enabled"]:
        log.error(
            f"Stretch mode not enabled on the cluster. Details : {stretch_details}"
        )
        raise Exception("Stretch mode Post deployment tests Failed")

    # wait for PG's to settle down with new crush rules after deployment of stretch mode
    if not wait_for_clean_pg_sets(rados_obj):
        log.error(
            "Cluster did not reach active + Clean state post deployment of stretch mode"
        )
        raise Exception("Stretch mode Post deployment tests Failed")

    # Checking if the pools have been updated/ can be created with the new crush rules
    pool_name = "test_crush_pool"
    if not rados_obj.create_pool(pool_name="test_crush_pool"):
        log.error(f"Failed to create pool: {pool_name} post stretch mode deployment")
        raise Exception("Stretch mode Post deployment tests Failed")

    acting_set = rados_obj.get_pg_acting_set(pool_name=pool_name)
    if len(acting_set) != 4:
        log.error(
            f"There are {len(acting_set)} OSD's in PG. OSDs: {acting_set}. Stretch cluster requires 4"
        )
        raise Exception("Stretch mode Post deployment tests Failed")
    log.info(
        f"Acting set : {acting_set} Consists of 4 OSD's per PG. test pool : {pool_name}"
    )

    log.info("Stretch mode deployed successfully")
    return 0
