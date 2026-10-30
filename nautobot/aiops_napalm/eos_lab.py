import napalm.eos as eos
from .environment import top_environment


class EOSLabDriver(eos.EOSDriver):
    def get_environment(self):
        version = self._run_commands(["show version"])[0]
        if version.get("modelName", "").lower() not in {
            "veos",
            "veos-lab",
            "ceoslab",
            "ceos-lab",
        }:
            return super().get_environment()
        # Virtual EOS has no physical power supplies. Core NAPALM 5.0 misses
        # the vEOS-lab model name and indexes the absent powerSupplies field.
        output = self._run_commands(["show processes top once"], encoding="text")[0][
            "output"
        ]
        return top_environment(output)
