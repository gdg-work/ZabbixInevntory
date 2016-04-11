#!/usr/bin/env python3

from pyzabbix.api import ZabbixAPI, ZabbixAPIException
from pyzabbix.sender import ZabbixSender, ZabbixMetric
import logging
import re

oLog = logging.getLogger(__name__)

# Constants
RE_DISK = re.compile('^Drive Disk \d{2,3}$')


# exceptions
class ZabInterfaceException(Exception):
    def __init__(self, sData):
        self.sData = sData

    def __str__(self): return self.sData


class DisksToZabbix:
    def __init__(self, sArrayName, sZabbixIP, iZabbixPort, sZabUser, sZabPwd):
        """
        sArrayName is a name of array (HOST.HOST) IN ZABBIX
        *Zab* are parameters for Zabbix connection.
        """
        oLog.debug("Entered DisksToZabbix.__init__")
        self.sZabbixURL = 'http://' + sZabbixIP + "/zabbix"
        self.sZabbixIP = sZabbixIP
        self.iZabbixPort = iZabbixPort
        self.dApplicationNamesToIds = {}
        self.sArrayName = sArrayName
        self.sHostID = ''
        self.dOperations = {'type': self._oPrepareDiskType,
                            'model': self._oPrepareDiskModel,
                            'SN': self._oPrepareDiskSN,
                            'position': self._oPrepareDiskPosition,
                            'RPM': self._oPrepareDiskRPM,
                            'size': self._oPrepareDiskSize}
        try:
            self.oZapi = ZabbixAPI(url=self.sZabbixURL, user=sZabUser, password=sZabPwd)
            self.oZSend = ZabbixSender(zabbix_server=self.sZabbixIP, zabbix_port=self.iZabbixPort)
            lsHosts = self.oZapi.host.get(filter={"host": sArrayName})
            if len(lsHosts) > 0:
                # Just use the first host
                self.sHostID = str(lsHosts[0]['hostid'])
                oLog.debug("host ID of array: {}".format(self.sHostID))
            else:
                oLog.error("Invalid or non-existent host in Zabbix")
                raise ZabInterfaceException("This host isn't known to Zabbix")
        except ZabbixAPIException as e:
            oLog.error("Cannot connect to Zabbix API")
            oLog.error(str(e))
            raise ZabInterfaceException
        return

    def __fillApplications__(self):
        # receive the list of applications
        ldApplications = self.oZapi.do_request('application.get', {'hostids': self.sHostID})
        if len(ldApplications['result']) == 0:
            # the host exists but didn't return anything, just continue
            oLog.info("Array with ID {0} and name {1} doesn't answer".format(self.sHostID, self.sArrayName))
        oLog.debug("Defined applications on host {0}: {1}".format(self.sHostID, ldApplications['result']))
        # now filter the apps list for disks
        ldDiskApps = [d for d in ldApplications['result'] if RE_DISK.match(d['name'])]
        oLog.debug("Filtered applications on host {0}: \n {1}".format(
                   self.sArrayName, ',\n'.join([str(a['name']) for a in ldDiskApps])))
        for dApp in ldDiskApps:
            self.dApplicationNamesToIds[dApp['name']] = dApp['applicationid']
        return

    def _oPrepareDiskData(self, sAppName, sParameter, iValue):
        """Send disk RPM speed to Zabbix. lApplications must be already filled"""
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
                oRet = ZabbixMetric(host=self.sArrayName, key=sKey, value=iValue)
                oLog.debug("_oPrepareDiskData: Data for sending to Zabbix: {}".format(str(oRet)))
            except IndexError:
                oLog.info("Can't receive item named '{}' from array".format(dFilter['name']))
                oRet = None
        except KeyError:
            oLog.info('Unknown application name')
            oRet = None
        return oRet

    def _oPrepareDiskRPM(self, sAppName, iValue):
        return self._oPrepareDiskData(sAppName, 'RPM', iValue)

    def _oPrepareDiskSize(self, sAppName, iValue):
        return self._oPrepareDiskData(sAppName, 'Size', iValue)

    def _oPrepareDiskType(self, sAppName, sValue):
        return self._oPrepareDiskData(sAppName, 'Type', sValue)

    def _oPrepareDiskModel(self, sAppName, sValue):
        return self._oPrepareDiskData(sAppName, 'Model', sValue)

    def _oPrepareDiskSN(self, sAppName, sValue):
        return self._oPrepareDiskData(sAppName, 'Serial Number', sValue)

    def _oPrepareDiskPosition(self, sAppName, sValue):
        return self._oPrepareDiskData(sAppName, 'Position', sValue)

    def sendDiskInfoToZabbix(self, sArrayName, ldDisksInfo):
        """send data to Zabbix via API"""
        loMetrics = []
        oLog.debug('sendDiskInfoToZabbix: data to send: {}'.format(str(ldDisksInfo)))
        for dDiskInfo in ldDisksInfo:
            sAppName = 'Drive ' + dDiskInfo['name']
            oLog.debug('App {} found!'.format(sAppName))
            for sName, oValue in dDiskInfo.items():
                try:
                    loMetrics.append(self.dOperations[sName](sAppName, oValue))
                except KeyError:
                    # unknown names passed
                    oLog.info('Skipped unknown disk information item named {} with value {}'.format(sName, str(oValue)))
                    pass
        if loMetrics:
            self.oZSend.send(loMetrics)
        return

if __name__ == "__main__":
    # set up logging
    # oLog.setLevel(logging.DEBUG)
    # oConHdr = logging.StreamHandler()
    # oConHdr.setLevel(logging.DEBUG)
    # oLog.addHandler(oConHdr)
    # testing
    pass

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4
