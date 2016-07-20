#!/usr/bin/env python3

from logging import getLogger
from pyzabbix.api import ZabbixAPI, ZabbixAPIException
from pyzabbix.sender import ZabbixSender, ZabbixMetric
from enum import Enum
from uuid import uuid4
from copy import copy   # copy of Python objects
import re
import json

oLog = getLogger(__name__)

# Constants
RE_DISK = re.compile(r'^Drive\s+')
RE_ENCLOSURE = re.compile(r'^DiskShelf\s+')
RE_CONTROLLER = re.compile(r'^Controller\s+')
RE_SYSTEM =     re.compile(r'^System\s*$')
RE_NODE =       re.compile(r'^Node\s')
RE_SWITCH =     re.compile(r'^Switch\s')
RE_UPS =        re.compile(r'^UPS\s')
RE_DIMM =       re.compile(r'^DIMM\s')
RE_CF =         re.compile(r'^Compact Flash ')


# THE simplest function, can take any number of arguments
def _NullFunction(*args):
    return None


# exceptions
class ZabInterfaceException(Exception):
    def __init__(self, sData):
        self.sData = sData

    def __str__(self):
        return self.sData


def _sListOfStringsToJSON(lsStrings):
    """Converts list of strings to JSON data for Zabbix"""
    ID = '{#ID}'
    lRetList = [{ID: n} for n in lsStrings]
    dRetDict = {"data": lRetList}
    return json.dumps(dRetDict)


def _sMkKey(*args):
    """Utility function: make string suitable for key"""
    sPreKey = "_".join(args)
    # now check if sPreKey is a valid identifier (Pythonic way):
    if sPreKey.isidentifier():
        return sPreKey
    else:
        # we need to replace all non-alnum chars in sPreKey by something
        sRes = ''
        for cChar in sPreKey:
            if cChar.isalnum():
                sRes += cChar
            elif cChar in '-_.':
                sRes += cChar
            elif cChar == ' ':
                sRes += '_'
            else:
                sRes += '.' + hex(ord(cChar))[2:] + '.'
        # print('_sMkKey: result is <' + sRes + '>')
        return sRes


class GeneralZabbix:
    def __init__(self, sHostName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        """
        sHostName is a name of host or array (HOST.HOST) IN ZABBIX
        *Zab* are parameters for Zabbix connection.
        """
        oLog.debug("Entered GeneralZabbix.__init__")
        self.sZabbixURL = 'http://' + sZabbixIP + "/zabbix"
        self.sZabbixIP = sZabbixIP
        self.iZabbixPort = iZabbixPort
        self.dApplicationNamesToIds = {}
        self.sHostName = sHostName
        # self.sArrayName = sHostName
        self.sHostID = ''
        try:
            self.oZapi = ZabbixAPI(url=self.sZabbixURL, user=sZabUser, password=sZabPwd)
            self.oZSend = ZabbixSender(zabbix_server=self.sZabbixIP, zabbix_port=self.iZabbixPort)
            lsHosts = self.oZapi.host.get(filter={"host": sHostName})
            if len(lsHosts) > 0:
                # Just use the first host
                self.sHostID = str(lsHosts[0]['hostid'])
                oLog.debug("host ID of host {1}: {0}".format(self.sHostID, self.sHostName))
            else:
                oLog.error("Invalid or non-existent host in Zabbix")
                raise ZabInterfaceException("This host '{}' isn't known to Zabbix".format(sHostName))
        except ZabbixAPIException as e:
            oLog.error("Cannot connect to Zabbix API")
            oLog.error(str(e))
            raise ZabInterfaceException
        return

    def __fillApplications__(self, reFilter=None):
        # receive the list of applications
        ldApplications = self.oZapi.do_request('application.get', {'hostids': self.sHostID})
        ldAppResult = ldApplications['result']
        dBuf = {}
        if len(ldAppResult) == 0:
            # the host exists but didn't return anything, just continue
            oLog.info("Array with ID {0} and name {1} doesn't answer".format(self.sHostID, self.sHostName))
        else:
            # oLog.debug("Applications on host {0}: {1}".format(self.sArrayName, ldAppResult))
            # now filter the apps list for this host
            for dApp in ldAppResult:
                sAppName = dApp['name']
                if reFilter:
                    if reFilter.match(sAppName):
                        dBuf[sAppName] = dApp['applicationid']
                        # oLog.debug('__fillApplications__: found app {}'.format(sAppName))
                    else:
                        # oLog.debug("__fillApplications__: skipped app {}".format(sAppName))
                        pass
                else:
                    dBuf[sAppName] = dApp['applicationid']
            self.dApplicationNamesToIds = dBuf
        return

    def _oPrepareZabMetric(self, sAppName, sParameter, iValue):
        """Prepare ZabbixMetric instance for sending to a Zabbix server"""
        dFilter = {'name': sAppName + ' ' + sParameter}
        try:
            iAppID = self.dApplicationNamesToIds[sAppName]
            dItem2Get = {'hostids': self.sHostID,
                         'applicationids': iAppID,
                         'filter': dFilter,
                         'sort': 'name'}
            dResult = self.oZapi.do_request('item.get', dItem2Get)
            try:
                sKey = dResult['result'][0]['key_']
                # now we have key, so we can prepare data to Zabbix
                oRet = ZabbixMetric(host=self.sHostName, key=sKey, value=iValue)
            except IndexError:
                oLog.info("Can't receive item named '{}' from Zabbix with item.get".format(dFilter['name']))
                oRet = None
        except KeyError:
            oLog.info('Unknown application name "{}"'.format(sAppName))
            # oLog.info('Known apps: ' + str(self.dApplicationNamesToIds))
            oRet = None
        return oRet

    def _SendMetrics(self, loMetrics):
        """Send only non-empty metrics to Zabbix"""
        if loMetrics:
            # skip empty objects from loMetrics
            loMetrics = [o for o in loMetrics if o is not None]
            self.oZSend.send(loMetrics)
        else:
            pass   # dont send empty metrics
        return


class DisksToZabbix(GeneralZabbix):
    def __init__(self, sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        """
        sArrayName is a name of array (HOST.HOST) IN ZABBIX
        *Zab* are parameters for Zabbix connection.
        """
        # oLog.debug("Entered DisksToZabbix.__init__")
        super().__init__(sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd)
        self.dOperations = {'type':   self._oPrepareDiskType,
                            'name':   _NullFunction,
                            'model':  self._oPrepareDiskModel,
                            'SN':     self._oPrepareDiskSN,
                            'sn':     self._oPrepareDiskSN,
                            'position': self._oPrepareDiskPosition,
                            'RPM':    self._oPrepareDiskRPM,
                            'disk-rpm': self._oPrepareDiskRPM,
                            'size':   self._oPrepareDiskSize}
        self.__fillApplications__(RE_DISK)
        return

    # def __fillApplications__(self): <-- moved to superclass

    def _oPrepareDiskRPM(self, sAppName, iValue):
        return self._oPrepareZabMetric(sAppName, 'RPM', iValue)

    def _oPrepareDiskSize(self, sAppName, iValue):
        return self._oPrepareZabMetric(sAppName, 'Size', iValue)

    def _oPrepareDiskType(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Type', sValue)

    def _oPrepareDiskModel(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Model', sValue)

    def _oPrepareDiskSN(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Serial Number', sValue)

    def _oPrepareDiskPosition(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Position', sValue)

    def _SendInfoToZabbix(self, sArrayName, ldDisksInfo):
        """send data to Zabbix via API"""
        loMetrics = []
        # oLog.debug('sendDiskInfoToZabbix: data to send: {}'.format(str(ldDisksInfo)))
        for dDiskInfo in ldDisksInfo:
            sAppName = 'Drive ' + dDiskInfo['name']
            for sName, oValue in dDiskInfo.items():
                try:
                    loMetrics.append(self.dOperations[sName](sAppName, oValue))
                except KeyError:
                    # unknown names passed
                    oLog.info('Skipped unknown disk information item named {} with value {}'.format(
                        sName, str(oValue)))
                    pass
        self._SendMetrics(loMetrics)
        return


class EnclosureToZabbix(GeneralZabbix):
    def __init__(self, sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        super().__init__(sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd)
        self.dOperations = {"name":         _NullFunction,
                            "sn":           self._oPrepareEnclSN,
                            "type":         self._oPrepareEnclType,
                            "model":        self._oPrepareEnclModel,
                            "disks":        self._oPrepareEnclDisksAmount,
                            "disk-slots":   self._oPrepareEnclSlotsAmount,
                            "ps-amount":    self._oPrepareEnclPSAmount}
        self.__fillApplications__(RE_ENCLOSURE)
        return

    # def __fillApplications__(self): <-- Moved to superclass

    def _oPrepareEnclSN(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Serial Number', sValue)

    def _oPrepareEnclType(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Type', sValue)

    def _oPrepareEnclModel(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Model', sValue)

    def _oPrepareEnclDisksAmount(self, sAppName, iValue):
        return self._oPrepareZabMetric(sAppName, 'Number of Occupied Slots', iValue)

    def _oPrepareEnclSlotsAmount(self, sAppName, iValue):
        return self._oPrepareZabMetric(sAppName, 'Number of Disk Slots', iValue)

    def _oPrepareEnclPSAmount(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Number of Power Supplies', sValue)

    def _SendInfoToZabbix(self, sArrayName, ldEnclosuresInfo):
        """send data to Zabbix via API"""
        loMetrics = []
        # oLog.debug('sendEnclInfoToZabbix: data to send: {}'.format(str(ldEnclosuresInfo)))
        for dShelfInfo in ldEnclosuresInfo:
            # oLog.debug('_SendEnclInfoToZabbix -- shelf info dict is {}'.format(dShelfInfo))
            sAppName = 'DiskShelf ' + dShelfInfo['name']
            for sName, oValue in dShelfInfo.items():
                try:
                    loMetrics.append(self.dOperations[sName](sAppName, oValue))
                except KeyError:
                    # unknown names passed
                    oLog.info('Skipped unknown DE information item named {} with value {}'.format(
                        sName, str(oValue)))
                    pass
        self._SendMetrics(loMetrics)
        return


class NodeToZabbix(GeneralZabbix):
    def __init__(self, sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        super().__init__(sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd)
        self.dOperations = {"name":         _NullFunction,
                            "sn":           self._oPrepareNodeSN,
                            "model":        self._oPrepareNodeModel,
                            "ps-amount":    self._oPrepareNodePSAmount,
                            "fc-ports":     self._oPrepareNodeFCPorts,
                            "eth-ports":    self._oPrepareNodeEthPorts,
                            "type":         self._oPrepareNodeType,
                            "ps-amount":    self._oPrepareNodePwrSupplies,
                            "disks":        self._oPrepareNodeDisksAmount,
                            "memory":       self._oPrepareNodeRAM_GB,
                            "disk-bays":    self._oPrepareNodeDiskBays}
        self.__fillApplications__(RE_NODE)
        return

    def _oPrepareNodeSN(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Serial Number', sValue)

    def _oPrepareNodeModel(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Model', sValue)

    def _oPrepareNodeType(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Type', sValue)

    def _oPrepareNodeDisksAmount(self, sAppName, iValue):
        return self._oPrepareZabMetric(sAppName, 'Disks Amount', iValue)

    def _oPrepareNodeDiskBays(self, sAppName, iValue):
        return self._oPrepareZabMetric(sAppName, '# Disk Bays', iValue)

    def _oPrepareNodePwrSupplies(self, sAppName, iValue):
        return self._oPrepareZabMetric(sAppName, '# Pwr Supplies', iValue)

    def _oPrepareNodeFCPorts(self, sAppName, iValue):
        return self._oPrepareZabMetric(sAppName, 'FC Ports', iValue)

    def _oPrepareNodeEthPorts(self, sAppName, iValue):
        return self._oPrepareZabMetric(sAppName, 'Ethernet Ports', iValue)

    def _oPrepareNodePSAmount(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, '# Power Supplies', sValue)

    def _oPrepareNodeRAM_GB(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Memory', sValue)

    def _SendInfoToZabbix(self, sArrayName, ldNodesInfo):
        """send data to Zabbix via API"""
        loMetrics = []
        for dNodeInfo in ldNodesInfo:
            sAppName = 'Node ' + dNodeInfo['name']
            for sName, oValue in dNodeInfo.items():
                try:
                    loMetrics.append(self.dOperations[sName](sAppName, oValue))
                except KeyError:
                    # unknown names passed
                    oLog.info('Skipped unknown Node information item named {} with value {}'.format(
                        sName, str(oValue)))
                    pass
        self._SendMetrics(loMetrics)
        return


class SwitchToZabbix(GeneralZabbix):
    def __init__(self, sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        super().__init__(sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd)
        self.dOperations = {"name":         _NullFunction,
                            "sn":           self._oPrepareSwitchSN}
        self.__fillApplications__(RE_SWITCH)
        return

    def _oPrepareSwitchSN(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Serial Number', sValue)

    def _SendInfoToZabbix(self, sArrayName, ldSwitchInfo):
        """send data to Zabbix via API"""
        loMetrics = []
        for dSwitchInfo in ldSwitchInfo:
            sAppName = 'Switch ' + dSwitchInfo['name']
            for sName, oValue in dSwitchInfo.items():
                try:
                    loMetrics.append(self.dOperations[sName](sAppName, oValue))
                except KeyError:
                    # unknown names passed
                    oLog.info('Skipped unknown Switch information item named {} with value {}'.format(
                        sName, str(oValue)))
                    pass
        self._SendMetrics(loMetrics)
        return


class DIMMsToZabbix(GeneralZabbix):
    def __init__(self, sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        super().__init__(sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd)
        self.dOperations = {"name":     _NullFunction,
                            "sn":       self._oPrepareSN,
                            "size":     self._oPrepareSize,
                            "position": self._oPreparePosition,
                            "model":    self._oPrepareModel}
        self.__fillApplications__(RE_DIMM)
        return

    def _oPrepareSN(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Serial Number', sValue)

    def _oPrepareModel(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Model', sValue)

    def _oPrepareSize(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Size', str(int(sValue) / 1024))   # convert to GBs

    def _oPreparePosition(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Position', sValue)

    def _SendInfoToZabbix(self, sArrayName, ldInfo):
        """send data to Zabbix via API"""
        loMetrics = []
        oLog.debug('DIMMsToZabbix: _SendInfoToZabbix: info list is ' + str(ldInfo))
        for dInfo in ldInfo:
            sAppName = 'DIMM ' + dInfo['name']
            for sName, oValue in dInfo.items():
                try:
                    loMetrics.append(self.dOperations[sName](sAppName, oValue))
                except KeyError:
                    # unknown names passed
                    oLog.info('Skipped unknown DIMM information item named {} with value {}'.format(
                        sName, str(oValue)))
                    pass
        self._SendMetrics(loMetrics)
        return


class CFtoZabbix(GeneralZabbix):
    def __init__(self, sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        super().__init__(sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd)
        self.dOperations = {"name":     _NullFunction,
                            "sn":       self._oPrepareSN,
                            "position": self._oPreparePosition,
                            "model":    self._oPrepareModel}
        self.__fillApplications__(RE_CF)
        return

    def _oPrepareSN(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Serial', sValue)

    def _oPrepareModel(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Model', sValue)

    def _oPrepareSize(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Size', sValue)

    def _oPreparePosition(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Module', sValue)

    def _SendInfoToZabbix(self, sArrayName, ldInfo):
        """send data to Zabbix via API"""
        loMetrics = []
        oLog.debug('CFtoZabbix: _SendInfoToZabbix: info list is ' + str(ldInfo))
        for dInfo in ldInfo:
            sAppName = 'Compact Flash ' + dInfo['name']
            for sName, oValue in dInfo.items():
                try:
                    loMetrics.append(self.dOperations[sName](sAppName, oValue))
                except KeyError:
                    # unknown names passed
                    oLog.info('Skipped unknown CF information item named {} with value {}'.format(
                        sName, str(oValue)))
                    pass
        self._SendMetrics(loMetrics)
        return


class UPSesToZabbix(GeneralZabbix):
    def __init__(self, sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        super().__init__(sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd)
        self.dOperations = {"name":         _NullFunction,
                            "mfgdate":      self._oPrepareMDate,
                            "sn":           self._oPrepareSN}
        self.__fillApplications__(RE_UPS)
        return

    def _oPrepareSN(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Serial Number', sValue)

    def _oPrepareMDate(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Production Date', sValue)

    def _SendInfoToZabbix(self, sArrayName, ldInfo):
        """send data to Zabbix via API"""
        loMetrics = []
        for dInfo in ldInfo:
            sAppName = 'UPS ' + dInfo['name']
            for sName, oValue in dInfo.items():
                try:
                    loMetrics.append(self.dOperations[sName](sAppName, oValue))
                except KeyError:
                    # unknown names passed
                    oLog.info('Skipped unknown UPS information item named {} with value {}'.format(
                        sName, str(oValue)))
                    pass
        self._SendMetrics(loMetrics)
        return


class CtrlsToZabbix(GeneralZabbix):
    """Disk controllers to Zabbix interface"""
    def __init__(self, sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        super().__init__(sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd)
        self.dOperations = {"name":       _NullFunction,
                            "sn":         self._oPrepareCtrlSN,
                            "type":       self._oPrepareCtrlType,
                            "model":      self._oPrepareCtrlModel,
                            "cpu-cores":  self._oPrepareCPUCores,
                            "cpu":        self._oPrepareCPUCores,
                            # "port-names": self._oPreparePortNames,
                            "pci":        self._oPreparePCICards,
                            "ram":        self._oPrepareRAMInfo,
                            "port-count": self._oPrepareHostPortNum,    # alternate name
                            "ports":      self._oPrepareHostPortNum}
        self.__fillApplications__(RE_CONTROLLER)
        return

    # def __fillApplications__(self):  <-- Moved to superclass

    def _oPrepareCtrlSN(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Serial Number', sValue)

    def _oPrepareCtrlType(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Type', sValue)

    def _oPrepareCtrlModel(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Model', sValue)

    def _oPrepareCPUCores(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'CPU', sValue)

    def _oPreparePortNames(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Port names', sValue)

    def _oPrepareHostPortNum(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Number of Host Ports', sValue)

    def _oPreparePCICards(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'PCI', sValue)

    def _oPrepareRAMInfo(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'DIMM', sValue)

    def _SendInfoToZabbix(self, sArrayName, ldCtrlsInfo):
        """send data to Zabbix via API"""
        loMetrics = []
        # oLog.debug('sendCtrlsToZabbix: data to send: {}'.format(str(ldCtrlsInfo)))
        for dCtrl in ldCtrlsInfo:
            # oLog.debug('_SendCtrlsInfoToZabbix -- controllers info dict is {}'.format(dCtrl))
            sAppName = 'Controller ' + dCtrl['name']
            for sName, oValue in dCtrl.items():
                try:
                    loMetrics.append(self.dOperations[sName](sAppName, oValue))
                except KeyError:
                    # unknown names passed
                    oLog.info('Skipped unknown controller information item named {} with value {}'.format(
                        sName, str(oValue)))
                    pass
        self._SendMetrics(loMetrics)
        return


class ArrayToZabbix(GeneralZabbix):
    """Class makes an interface between disk arrays' classes and Zabbix templates"""
    def __init__(self, sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        super().__init__(sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd)
        self.dOperations = {
            "name":       _NullFunction,
            "sn":         self._oPrepareArraySN,
            "type":       self._oPrepareArrayType,
            "disks":      self._oPrepareArrayDisks,
            "ctrls":      self._oPrepareArrayControllers,
            "nodes":      self._oPrepareArrayNodes,
            "shelves":    self._oPrepareArrayShelves,
            "wwn":        self._oPrepareArrayWWN,
            "ps-amount":  self._oPreparePwrSuppliesAmount,
            "model":      self._oPrepareArrayModel,
            "memory":     self._oPrepareArrayMemory,
            "fc-ports":   self._oPrepareFCPorts,
            "eth-ports":  self._oPrepareNICs}
        self.__fillApplications__(RE_SYSTEM)
        return

    def _oPrepareArraySN(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Serial Number', sValue)

    def _oPrepareFCPorts(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, '# FC Ports', sValue)

    def _oPrepareNICs(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, '# NICs', sValue)

    def _oPrepareArrayType(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Type', sValue)

    def _oPrepareArrayWWN(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'WWN', sValue)

    def _oPrepareArrayModel(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Model', sValue)

    def _oPrepareArrayDisks(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Number of Drives', sValue)

    def _oPrepareArrayShelves(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Number of Drive Shelves', sValue)

    def _oPreparePwrSuppliesAmount(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Number of Power Supplies', sValue)

    def _oPrepareArrayControllers(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Number of Controllers', sValue)

    def _oPrepareArrayNodes(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Number of Nodes', sValue)

    def _oPrepareArrayMemory(self, sAppName, sValue):
        return self._oPrepareZabMetric(sAppName, 'Total RAM', sValue)

    def _SendInfoToZabbix(self, sArrayName, dArrInfo):
        """send ARRAY data to Zabbix via API"""
        loMetrics = []
        sAppName = 'System'
        for sName, oValue in dArrInfo.items():
            try:
                oResult = self.dOperations[sName](sAppName, oValue)
                if not(oResult is None):
                    loMetrics.append(oResult)
            except KeyError:
                # unknown names passed
                oLog.info('Skipped unknown ARRAY information item named {} with value {}'.format(
                    sName, str(oValue)))
                pass
        oLog.debug('_SendArrayToZabbix: metrics are {}'.format(str(loMetrics)))
        self._SendMetrics(loMetrics)
        return


# "New" Zabbix classes for servers, mapping Zabbix entities to Python objects

class MyZabbixException(Exception):
    def __init__(self, s):
        self.sMsg = s

    def __repr__(self):
        return self.sMsg


class ZabbixHost:
    """Zabbix object: host"""
    def __init__(self, sName, oZabAPI):
        """initialize empty object with a given name"""
        self.oAPI = oZabAPI
        self.sName = sName
        # try to find a host by name. This name must be unique
        lHosts = self.oAPI.host.get(filter={'host': sName})
        if len(lHosts) == 1:
            # exists, unique
            self.iHostID = int(lHosts[0]['hostid'])
            pass
        elif len(lHosts) > 1:
            # exists, non-unique
            raise MyZabbixException('Non-unique host name')
        else:
            # doesn't exist
            raise MyZabbixException('Host is unknown to Zabbix')
        # get list of applications from host
        self.dAppIds = {}
        self.dApps = {}
        self.dItemNames = {}
        return

    def _bHasApplication(self, sAppName):
        sAppName = sAppName.lower()
        if sAppName in self.dAppIds:
            bRet = True
        else:
            ldApplications = self.oAPI.do_request('application.get', {'hostids': str(self.iHostID)})
            # print("Defined applications on host {0}:\n{1}".format(self.iHostID, "\n".join(
            #      [str(a) for a in ldApplications['result']])))
            for dApp in ldApplications['result']:
                sNewName = dApp['name'].lower()
                sNewID = dApp['applicationid']
                self.dAppIds[sNewName] = dApp['applicationid']
                self.dApps[sNewID] = ZabbixApplication(sNewName, self, dApp)
            bRet = (sAppName in self.dAppIds)
        return bRet

    def _bHasItem(self, sItemName, iAppID=0):
        bRet = False
        sItemName = sItemName.lower()
        if sItemName in self.dItemNames:
            bRet = True
        else:
            # try to refresh dItemNames
            dGetItem = {'hostids': self.iHostID,
                        'search': {'name': sItemName}}
            dItems = self.oAPI.do_request('item.get', dGetItem)
            oLog.debug('_bHasItem: result of item.get() is {}'.format(str(dItems)))
            if len(dItems['result']) > 0:
                # matching item(s) found
                for dItemDict in dItems['result']:
                    # dItemDict = dItems['result'][0]
                    oLog.debug("Item found: {}".format(str(dItemDict)))
                    sName = dItemDict['name'].lower()
                    dItemDict['key'] = dItemDict.get('key_')    # from the Zabbix parameter 'key_'
                    self.dItemNames[sName] = ZabbixItem(sName, self, dItemDict)
                bRet = True
            else:
                # No item found
                bRet = False
            oLog.debug('_bHasItem: dItemNames after call is: ' + str(self.dItemNames))
        return bRet

    def _oAddApp(self, sAppName):
        # sAppName = sAppName.lower()
        if self._bHasApplication(sAppName):
            # already have this app
            oApp = self._oGetApp(sAppName)
        else:
            oApp = ZabbixApplication(sAppName, self)
            oApp._NewApp(sAppName)
            self.dAppIds[sAppName] = oApp._iGetID
            self.dApps[oApp._iGetID] = oApp
        return oApp

    def _oAddItem(self, sItemName, sAppName='', dParams=None):
        # sItemName = sItemName.lower()
        if self._bHasItem(sItemName):
            # already have that item
            oItem = self._oGetItem(sItemName)
            oLog.debug('Already have that item, returned {}'.format(str(oItem)))
        else:
            # need to create item
            oItem = ZabbixItem(sItemName, self, dParams)
            self.dItemNames[sItemName] = oItem
            if sAppName != '':
                oApp = self._oGetApp(sAppName)
                oItem._LinkWithApp(oApp)
            oItem._NewZbxItem()
            oLog.debug('Created a new item, returned {}'.format(str(oItem)))
        return oItem

    def __repr__(self):
        sRet = "Host name: {}, ID: {}. Defined apps:\n".format(self.sName, self.iHostID)
        sRet += "\n".join([a.__repr__() for a in self.dApps.values() if a is not None])
        return sRet

    @property
    def id(self):
        return self.iHostID

    def _iGetHostID(self):
        return self.iHostID

    @property
    def name(self):
        return self.sName

    def _sName(self):
        return self.sName

    def _oGetItem(self, sItemName):
        """returns item object by name"""
        sItemName = sItemName.lower()
        if self._bHasItem(sItemName):
            return self.dItemNames.get(sItemName, None)
        else:
            return None

    def _oGetApp(self, sAppName):
        sAppName = sAppName.lower()
        if self._bHasApplication(sAppName):
            return self.dApps[self.dAppIds[sAppName]]
        else:
            return None


class ZabbixApplication:
    def __init__(self, sName, oHost, dParams=None):
        self.oHost = oHost
        self.lRelatedItems = []
        self.sName = sName
        if dParams is not None:
            self.iID = int(dParams.get('applicationid'))
        else:
            self.iID = -1       # <-- Special value XXX
        return

    def _NewApp(self, sAppName):
        """creates a new application on host if there are no such an application"""
        if not self.oHost._bHasApplication(sAppName):
            # create an app on the server
            dNewApp = {'hostid': str(self.oHost._iGetHostID()), 'name': sAppName}
            oRes = self.oHost.oAPI.do_request('application.create', dNewApp)
            if (oRes['result'] is not None):
                self.iID = int(oRes['result']['applicationids'][0])
            else:
                raise MyZabbixException('Non-successful application creation')
        return

    def _AddItem(self, oItem):
        if not(oItem in self.lRelatedItems):
            self.lRelatedItems.append(oItem)
        return

    def _sGetName(self):
        return self.sName

    def _iGetID(self):
        return self.iID

    def __repr__(self):
        sRet = "Zbx App: Host ID: {0}, app ID: {1}, name: {2}".format(
            self.oHost._iGetHostID(), self.iID, self.sName)
        sRet += "\n".join([i.__repr__() for i in self.lRelatedItems])
        return sRet

    def _loRelatedItems(self):
        return list(self.lRelatedItems)


class TriggerSeverity(Enum):
    NotClass = 0
    INFO = 1
    WARNING = 2
    AVERAGE = 3
    HIGH = 4
    DISASTER = 5


def _enStrToSeverity(sSev=''):
    sSev = sSev.strip().casefold()
    if sSev == '':
        return TriggerSeverity.NotClass
    elif sSev == 'info':
        return TriggerSeverity.INFO
    elif sSev == 'warning':
        return TriggerSeverity.WARNING
    elif sSev == 'average':
        return TriggerSeverity.AVERAGE
    elif sSev == 'high':
        return TriggerSeverity.HIGH
    elif sSev == 'disaster':
        return TriggerSeverity.DISASTER
    else:
        return TriggerSeverity.NotClass


class ZabbixItem:
    def __init__(self, sName, oHost, dDict=None):
        self.sName = sName
        self.oHost = oHost
        self.iHostID = oHost._iGetHostID()
        self.lRelatedApps = []
        self.lTriggers = []
        self.sKey = None
        if dDict is not None:
            self.iID = int(dDict.get('itemid', 0))
            self.sUnits = dDict.get('units', '')
            # types: 0: numeric float; 1: character; 2: log; 3: numeric unsigned; 4: text.
            # see https://www.zabbix.com/documentation/3.0/manual/api/reference/item/object#item
            self.iValType = int(dDict.get('value_type', 1))
            # update type: see documentation.  2 is a Zabbix trapper item
            self.iUpdType = int(dDict.get('type', 2))
            self.sKey = dDict.get('key') or dDict.get('key_')    # unique key
            self.iDelay = int(dDict.get('delay', 0))
            self.sDescription = dDict.get('description', '')
        else:
            # fill some fields
            self.sUnits = ''
            self.iValType = 1
            self.iUpdType = 2
            self.iDelay = 86400     # 1 day
        if self.sKey is None:
            self.sKey = str(uuid4())
        return

    def _bExists(self, oHost):
        return oHost._bHasItem(self.sName)

    @property
    def name(self):
        return self.sName

    @property
    def id(self):
        return self.iID

    @property
    def host(self):
        return copy(self.oHost)

    @property
    def key(self):
        return self.sKey

    def _sGetName(self):
        return self.sName

    def _LinkWithApp(self, oApp):
        if not(oApp in self.lRelatedApps):
            self.lRelatedApps.append(oApp)
        return

    def _NewZbxItem(self):
        lAppIDs = []
        # self.sKey = sKey
        for oApp in self.lRelatedApps:
            lAppIDs.append(oApp._iGetID())
        dNewItem = {'hostid': self.oHost.iHostID,
                    'applications': lAppIDs,
                    'name': self.sName,
                    'key_': self.sKey,
                    # required fields for an item
                    'type': self.iUpdType,
                    'value_type': self.iValType,
                    'delay': self.iDelay,
                    'units': self.sUnits,
                    # optional fields
                    'description': self.sDescription
                    }
        try:
            dRes = self.oHost.oAPI.do_request('item.create', dNewItem)
        except ZabbixAPIException as e:
            # error: cannot create item
            raise MyZabbixException('_NewZbxItem: Cannot create an item, error {}'.format(e))
        oLog.debug("_NewZbxItem: operation result is \n{}".format(
            ["{}{}\n".format(str(k), str(v)) for k, v in dRes.items()]))
        self.iID = dRes.get('itemid')
        return

    def _SendValue(self, oValue, oZabSender):
        oLog.debug('Entered _SendValue, params are: {}, {}'.format(oValue, str(oZabSender)))
        if self.iValType == 0:        # numeric (float)
            oValue = float(oValue)
        elif self.iValType in [1, 2, 4]:      # character
            oValue = str(oValue)
        elif self.iValType == 3:      # unsigned int
            oValue = int(oValue)
        oData2Send = ZabbixMetric(host=self.oHost._sName(), key=self.sKey, value=oValue)
        try:
            if not oZabSender.send([oData2Send]):
                # unsuccessful data sending
                oLog.error('Zabbix Sender failed')
        except ConnectionRefusedError:
            oLog.error('Cannot send data to Zabbix server: connection refused')
            oLog.info('Host: {} item name: {}, value: {}'.format(self.oHost._sName(), self.sName, oValue))
            # and just pass
        return

    def __repr__(self):
        return ("Item: name {0}, key {1}, update type: {2}".format(self.sName, self.sKey, self.iUpdType))

    def _AddTrigger(self, sTriggerName):
        self.lTriggers.append(sTriggerName)
        return


class TriggerFactory:
    """Factory of triggers. Check the presence and construct the Zabbix trigger"""

    def __init__(self):
        self.ddTriggersList = {}   # Global array of triggers
        # Global array of triggers is a 2-level dictionary where 1st level keys are hostnames,
        # and 2nd level keys are items' keys.  Values are list of triggers  linked with these
        # hosts and keys
        return

    def _bTriggerExist(self, oHost, oItem, sTrigName):
        """check if trigger with given name alredy exists on given host and item"""
        bResult = False
        oLog.debug("*DBG* ddTriggersList is {}".format(str(self.ddTriggersList)))
        if sTrigName in self.ddTriggersList.get(oHost.name, {}).get(oItem.key, []):
            oLog.debug("*DBG* Trigger named {1} exist on host {0}".format(oHost.name, sTrigName))
            bResult = True
        else:
            dParams = {'host': oHost.sName, 'itemids': [oItem.id]}
            dRes = oHost.oAPI.do_request('trigger.get', dParams)
            oLog.debug('_bTriggerExist: Result of trigger.get: ' + str(dRes))
            for dOneRes in dRes['result']:
                bResult = bResult or ('triggerid' in dOneRes)
                # there should be not much results, so no 'continue' optimization here
            if bResult:
                self._RegisterTrigger(oHost, oItem, sTrigName)
        return bResult

    def _RegisterTrigger(self, oHost, oItem, sTrigName):
        """Add a trigger name to list value of self.ddTriggersList[host][item]
        Parameters:
        1) Host object
        2) Item object
        3) Trigger name
        """
        if not (oHost.name in self.ddTriggersList):
            oLog.debug("*DBG* _RegisterTrigger: unknown host {}".format(oHost.name))
            self.ddTriggersList[oHost.name] = {}
        if oItem.key not in self.ddTriggersList[oHost.name]:
            oLog.debug("*DBG* _RegisterTrigger: unknown item {}".format(oItem.name))
            self.ddTriggersList[oHost.name][oItem.key] = []
        oLog.debug("*DBG* Registered trigger named {0} for host {1} and item {2}".format(
            sTrigName, oHost.name, oItem.name))
        self.ddTriggersList[oHost.name][oItem.key].append(sTrigName)
        oItem._AddTrigger(sTrigName)
        return

    def _AddChangeTrigger(self, oItem, sTriggerName='', sSeverity='warning'):
        if sTriggerName == '':
            sTriggerName = oItem.name + " Changed"
        if not self._bTriggerExist(oItem.host, oItem, sTriggerName):
            sHostName = oItem.host.name
            sExpr = '{' + "{}:{}".format(sHostName, oItem.key) + '.diff()}=1'
            oLog.debug('Expression: ' + sExpr)
            enSeverity = _enStrToSeverity(sSeverity)
            dNewTrigger = {'hostid': oItem.host.id,
                           'description': sTriggerName,
                           'expression': sExpr,
                           'priority': enSeverity.value,
                           'status': 0,  # enabled
                           }
            dRes = {}
            try:
                dRes = oItem.host.oAPI.do_request('trigger.create', dNewTrigger)
                oLog.debug("_AddChangeTrigger: operation result is \n{}".format(
                    ["{}{}\n".format(str(k), str(v)) for k, v in dRes.items()]))
                self._RegisterTrigger(oItem.host, oItem, sTriggerName)
            except ZabbixAPIException as e:
                raise MyZabbixException('_AddChangeTrigger: Cannot create an trigger, error {}'.format(e))
        else:
            oLog.debug('Trigger {} already exists on host {} and itemid {}'.format(
                sTriggerName, oItem.host.name, oItem.id))
        return

    def _AddNoDataTrigger(self, oItem, sTriggerName, sSeverity='warning', iPeriod=24):
        """This trigger fires when no data is received over the given time period. Parameters:
        1) Zabbix item (object of ZabbixItem class)
        2) Trigger Name
        3) Desired severity (object of TriggerSeverity class)
        4) Time period to check data presence for (HOURS)
        Returns nothing, raises MyZabbixException on error
        """
        if sTriggerName == '':
            sTriggerName = self.sName + "No data received"
        if not self._bTriggerExist(oItem.host, oItem, sTriggerName):
            iSec = int(iPeriod * 3600)   # from hours to seconds
            sExpr = '{' + '{0}:{1}.nodata({2})'.format(oItem.host.name, oItem.key, iSec) + '}=1'
            oLog.debug("NoData trigger expression:  " + sExpr)

            oLog.debug('_AddNoDataTrigger: Expression: ' + sExpr)
            enSeverity = _enStrToSeverity(sSeverity)
            dNewTrigger = {'hostid': oItem.host.id,
                           'description': sTriggerName,
                           'expression': sExpr,
                           'priority': enSeverity.value,
                           'status': 0,  # enabled
                           }
            dRes = {}
            try:
                dRes = oItem.host.oAPI.do_request('trigger.create', dNewTrigger)
                oLog.debug("_AddNoDataTrigger: operation result is \n{}".format(
                    ["{}{}\n".format(str(k), str(v)) for k, v in dRes.items()]))
                self._RegisterTrigger(oItem.host, oItem, sTriggerName)
            except ZabbixAPIException as e:
                raise MyZabbixException('_AddChangeTrigger: Cannot create an trigger, error {}'.format(e))
        else:
            oLog.debug('Trigger {} already exists on host {} and itemid {}'.format(
                sTriggerName, oItem.host.name, oItem.id))
        return

# --

if __name__ == "__main__":
    # set up logging
    # oLog.setLevel(logging.DEBUG)
    # oConHdr = logging.StreamHandler()
    # oConHdr.setLevel(logging.DEBUG)
    # oLog.addHandler(oConHdr)
    # testing
    pass

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
