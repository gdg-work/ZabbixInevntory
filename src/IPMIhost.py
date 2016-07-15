#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""A module for server with IPMI interface. 'FRU' command will be given to host, results will be parsed"""
from subprocess import check_output, CalledProcessError, STDOUT
from string import printable
from enum import Enum
import logging

# from local import IPMI_TOOL

IPMI_TOOL = '/usr/bin/ipmitool'

oLog = logging.getLogger(__name__)


def StripWSNulls(s):
    """strip any whitespace and NULL characters from both ends of a string"""
    s = ''.join([l  for l in s if l in printable])
    s = s.strip()
    # print('StripWSNulls: result is {}'.format(s))
    return s


class FruTypeEnum(Enum):
    CPU = 1
    MEMORY = 2
    EXPANSION_CARD = 3
    STORAGE = 4
    POWER = 5


def _oIpmiHostFromDict(dData):
    return IPMIhost(dData['ip'], dData['user'], dData['pass'])


class IPMIhost:
    """main class for the module"""

    def __init__(self, sBmcHost, sUser, sPass):
        """log-in to BMC, execute a 'fru' command and parse results"""
        self.lFruList = []
        self.sCmdTemplate = "{0} -H {1} -U {2} -P {3} -L USER ".format(IPMI_TOOL, sBmcHost, sUser, sPass)
        self.sCmdTemplate += "{}"
        lFruCmd = self.sCmdTemplate.format("fru").split(' ')
        # print(lFruCmd)

        try:
            sData = ''
            sData = check_output(lFruCmd, stderr=STDOUT, universal_newlines=True, shell=False)
        except CalledProcessError as e:
            if sData:
                oLog.debug("Standart output: " + sData)
            else:
                # oLog.error("Command output: " + e.output)
                sOut = e.output
                if "Address lookup for" in sOut and "failed" in sOut:
                    oLog.error("Error calling IPMIutil ({})".format(str(e)))
                    oLog.error("Command output: " + e.output)
                    raise e
                elif "FRU" in sOut:
                    oLog.info("Non-zero return status from IPMItool")
                    sData = e.output
                else:
                    oLog.error("Error calling IPMIutil ({})".format(str(e)))
                    oLog.error("Command output: " + e.output)
                    raise e

        # ========== debug
        # with open('/tmp/fru.out', 'r') as fIn:
        #     sData = fIn.read()
        # ---------- end debug

        lDeviceDescriptions = sData.split("\n\n")
        for sDesc in lDeviceDescriptions:
            if self.__bHasData(sDesc):
                if self.__enClassifyFRU(sDesc) is FruTypeEnum.MEMORY:
                    self.lFruList.append(IpmiMemoryFRU(sDesc))
                else:
                    # no other devices yet
                    pass
        return

    def __bHasData(self, sDesc):
        """Does this description have any data or it is header-only?"""
        lDesc = sDesc.split("\n")
        if len(lDesc) == 1:
            return False
        elif 'Device not present' in lDesc[1][:20]:
            return False
        else:
            return True

    def __enClassifyFRU(self, sDesc):
        """what class this FRU belongs to?. Returns enumeration"""
        lDesc = sDesc.split("\n")
        sDevName = lDesc[0].split(':')[1].strip()
        if 'DIMM' in sDevName:
            return FruTypeEnum.MEMORY
        if 'Power Supply' in sDevName:
            return FruTypeEnum.POWER
        else:
            return None

    def _loFruList(self):
        """returns a copy of internal FRU list"""
        return list(self.lFruList)


class IpmiFRU:
    def __init__(self, sDesc):
        self.dData = {}
        lDescLines = sDesc.split("\n")
        self.sDevName = lDescLines[0].split(':')[1].strip()
        for sLine in lDescLines[1:]:
            sName, sValue = [StripWSNulls(a) for a in sLine.split(':')]
            self.dData[sName] = sValue
        # now select interesting fields from dData and store these fields in the object
        self.dAttrs = {}
        self.dAttrs['pn'] = self.dData.get('Part Number')
        self.dAttrs['sn'] = self.dData.get('Serial Number')
        self.dAttrs['vendor'] = self.dData.get('Manufacturer')
        self.dAttrs['man.date'] = self.dData.get('Manufacture Date')
        return

    @property
    def name(self):
        return self.sDevName

    @property
    def sn(self):
        return self.dAttrs.get('sn')

    @property
    def pn(self):
        return self.dAttrs.get('pn')

    @property
    def type(self):
        return self.dAttrs.get('type')

    @property
    def vendor(self):
        return self.dAttrs.get('vendor')

    @property
    def mfgdate(self):
        return self.dAttrs.get('man.date')


class IpmiMemoryFRU(IpmiFRU):
    def __init__(self, sDesc):
        super().__init__(sDesc)
        if "Memory Type" in self.dData:
            # this is a RAM module
            self.dAttrs['type'] = self.dData.get('Memory Type')
            self.dAttrs['capacity'] = self.dData.get('Memory size')
        # print(self.dAttrs)
        return

    @property
    def cap(self):
        return self.dAttrs.get('capacity')


# -- test section --
if __name__ == '__main__':
    # logging setup
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)

    from access import vmexch02mgt as tsrv
    oTest = IPMIhost(tsrv.ip, tsrv.user, tsrv.pwd)
    for sAttr in ['pn', 'sn', 'type', 'capacity']:
        print([a.dAttrs[sAttr] for a in oTest._loFruList()])
