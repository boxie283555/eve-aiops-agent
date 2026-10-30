import xml.etree.ElementTree as ET
import napalm_panos as panos
from .environment import top_environment


class PANOSLabDriver(panos.PANOSDriver):
    def get_environment(self):
        # PAN-OS resources reports management-plane CPU/memory, not dataplane load.
        self.device.op(cmd="<show><system><resources></resources></system></show>")
        root = ET.fromstring(self.device.xml_root())
        result = root.find("result")
        if result is None:
            raise ValueError("PAN-OS did not return resource counters")
        return top_environment("".join(result.itertext()))
