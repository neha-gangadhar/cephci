from cli import Cli
from cli.utilities.utils import build_cmd_from_args

from .crush import Crush
from .pool import Pool


class Osd(Cli):
    """This module provides CLI interface for OSD related operations"""

    def __init__(self, nodes, base_cmd):
        super(Osd, self).__init__(nodes)
        self.base_cmd = f"{base_cmd} osd"
        self.pool = Pool(nodes, self.base_cmd)
        self.crush = Crush(nodes, self.base_cmd)

    def lspools(self):
        """
        To list cluster’s pools
        """
        cmd = f"{self.base_cmd} lspools"
        out = self.execute(sudo=True, cmd=cmd)
        if isinstance(out, tuple):
            return out[0].strip()
        return out

    def set(self, flag):
        """Set osd flag

        Args:
            flag (str): osd flag
        """
        cmd = f"{self.base_cmd} set {flag}"
        return self.execute(sudo=True, long_running=True, cmd=cmd)

    def unset(self, flag):
        """Set osd flag

        Args:
            flag (str): osd flag
        """
        cmd = f"{self.base_cmd} unset {flag}"
        return self.execute(sudo=True, long_running=True, cmd=cmd)

    def tree(self, epoch=None, states=None, **kw):
        """To list osd tree
        Args:
            epoch (int): state time
            states (str): osd state
            kw (dict): execute command parameters
        """
        cmd = f"{self.base_cmd} tree"
        if epoch:
            cmd += f" {epoch}"
        if states:
            cmd += f" {states}"
        cmd += build_cmd_from_args(**kw)
        return self.execute(sudo=True, cmd=cmd)
