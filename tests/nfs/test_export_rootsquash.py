from nfs_operations import cleanup_cluster, setup_nfs_cluster

from cli.ceph.ceph import Ceph
from cli.exceptions import ConfigError, OperationFailedError
from cli.utilities.filesys import Mount, Unmount
from utility.log import Log

log = Log(__name__)


def get_file_owner(filepath, clients):
    out = clients[0].exec_command(sudo=True, cmd=f"ls -n {filepath}")
    uid = int(out[0].split()[2])
    gid = int(out[0].split()[3])
    if uid == 0 and gid == 0:
        log.info(f"The file '{filepath}' is created by the root user.")
        return "rootuser"
    elif uid == 4294967294 and gid == 4294967294:
        log.info(f"The file '{filepath}' is created by the squashed user.")
        return "squashuser"
    else:
        log.info(
            f"The file '{filepath}' is created by an unknown user with UID: {uid} and GID: {gid}."
        )
        return None


def run(ceph_cluster, **kw):
    """Test squash export option on NFS mount
    Args:
        **kw: Key/value pairs of configuration information to be used in the test.
    """
    config = kw.get("config")
    nfs_nodes = ceph_cluster.get_nodes("nfs")
    clients = ceph_cluster.get_nodes("client")

    port = config.get("port", "2049")
    version = config.get("nfs_version")
    no_clients = int(config.get("clients", "3"))
    nfs_name = "cephfs-nfs"
    nfs_mount = "/mnt/nfs"
    nfs_export = "/export"
    nfs_server_name = nfs_nodes[0].hostname
    fs_name = "cephfs"

    # Squashed export parameters
    nfs_export_squash = "/export_squash"
    nfs_squash_mount = "/mnt/nfs_squash"

    # If the setup doesn't have required number of clients, exit.
    if no_clients > len(clients):
        raise ConfigError("The test requires more clients than available")

    clients = clients[:no_clients]  # Select only the required number of clients

    try:
        # Setup nfs cluster
        setup_nfs_cluster(
            clients,
            nfs_server_name,
            port,
            version,
            nfs_name,
            nfs_mount,
            fs_name,
            nfs_export,
            fs_name,
        )

        # Create export with squash permission
        Ceph(clients).nfs.export.create(
            fs_name=fs_name,
            nfs_name=nfs_name,
            nfs_export=nfs_export_squash,
            fs=fs_name,
            squash="rootsquash",
        )

        # Mount the volume with rootsquash enable on client
        clients[0].create_dirs(dir_path=nfs_squash_mount, sudo=True)
        if Mount(clients[0]).nfs(
            mount=nfs_squash_mount,
            version=version,
            port=port,
            server=nfs_server_name,
            export=nfs_export_squash,
        ):
            raise OperationFailedError(f"Failed to mount nfs on {clients[0].hostname}")
        log.info("Mount succeeded on client")

        # Change the permission of mount dir
        clients[0].exec_command(sudo=True, cmd=f"chmod 777 {nfs_mount}/")

        # Create file on non squashed dir
        clients[0].exec_command(
            sudo=True,
            cmd=f"touch {nfs_mount}/file_nosquash",
        )

        # Check permission of file created by root user
        clients[0].exec_command(sudo=True, cmd=f"ls -n {nfs_mount}/file_nosquash")
        out = get_file_owner(f"{nfs_mount}/file_nosquash", clients)
        if "root" not in out:
            log.error("File is not created by root user")
            return 1
        log.info("File created by root user")

        # Create file on squashed dir
        clients[0].exec_command(
            sudo=True,
            cmd=f"touch {nfs_squash_mount}/file_squashed",
        )

        # Check permission of file created by squashed user
        clients[0].exec_command(
            sudo=True, cmd=f"ls -n {nfs_squash_mount}/file_squashed"
        )
        out = get_file_owner(f"{nfs_squash_mount}/file_squashed", clients)
        if "squashuser" not in out:
            log.error("File is not created by squashed user")
            return 1
        log.info("File created by squashed user")

    except Exception as e:
        log.error(f"Failed to validate export rootsquash: {e}")
        return 1

    finally:
        log.info("Cleaning up")
        # Cleaning up the squash export and mount dir
        log.info("Unmounting nfs-ganesha squash mount on client:")
        if Unmount(clients[0]).unmount(nfs_squash_mount):
            raise OperationFailedError(
                f"Failed to unmount nfs on {clients[0].hostname}"
            )
        log.info("Removing nfs-ganesha squash mount dir on client:")
        clients[0].exec_command(sudo=True, cmd=f"rm -rf  {nfs_squash_mount}")
        Ceph(clients[0]).nfs.export.delete(nfs_name, nfs_export_squash)

        # Cleaning up the remaining export and deleting the nfs cluster
        cleanup_cluster(clients[0], nfs_mount, nfs_name, nfs_export)
        log.info("Cleaning up successfull")
    return 0
