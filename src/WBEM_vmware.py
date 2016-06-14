#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" A program for extracting WBEM information from hosts """

import pywbem
import re
import enum
import logging
import atexit
import ssl
from pyVim import connect
from pyVmomi import vmodl

oLog = logging.getLogger(__name__)

# CONSTANTS
SSL_PROTO = ssl.PROTOCOL_SSLv23
SSL_VERIFY_MODE = ssl.CERT_NONE    # <--- XXX don't verify anything!
RE_DISK = re.compile(r'Disk Drive')
RE_DISK_DRIVE_CLASS = re.compile(r'DiskDrive$')
RE_PHYS_DISK_CLASS = re.compile(r'PhysicalDrive$')


# Helper function
def _dMergeDicts(*dict_args):
    '''
    Given any number of dicts, shallow copy and merge into a new dict,
    precedence goes to key value pairs in latter dicts.
    '''
    dRes = {}
    for d in dict_args:
        dRes.update(d)
    return dRes


def _dFilterNone(dFrom):
    """Given a dictionary, returns only key:value pairs where value is not None"""
    dData = dFrom.copy()
    lToDelete = []
    for k, v in dData.items():
        if v is None:
            lToDelete.append(k)
    # now make actual deletion of items
    for k in lToDelete:
        del dData[k]
    return dData


class WBEM_Exception(Exception):
    def __init__(self, *lArgs):
        super().__init__(lArgs)


class WBEM_Disk_Exception(WBEM_Exception):
    pass


class WBEM_Memory_Exception(WBEM_Exception):
    pass


class WBEM_CPU_Exception(WBEM_Exception):
    pass


class WBEM_System_Exception(WBEM_Exception):
    pass


class exVCenterError(Exception):
    def __init__(self, *lArgs):
        super().__init__(lArgs)


def _sGet_CIM_Ticket(sVCenterHost, sUser, sPass, sTargetHost, iPort=443):
    """Retrieves CIM session ticket from vCenter
    Parameters:
      sVCenterHost: VCenter hostname or IP,
      sUser, sPass: user credentials for vCenter,
      sTargetHost: ESXi host for CIM access
    Returns:
      Session ID (UUID) as a string
    """
    reIPv4_Format = re.compile(r'\d{1-3}.\d{1-3}.\d{1-3}.\d{1-3}')
    oSSL_Context = ssl.SSLContext(SSL_PROTO)
    oSSL_Context.verify_mode = SSL_VERIFY_MODE    # <--- don't verify anything!!!

    try:
        service_instance = connect.SmartConnect(host=sVCenterHost,
                                                user=sUser,
                                                pwd=sPass,
                                                port=iPort,
                                                sslContext=oSSL_Context)
        if not service_instance:
            sRet = ''
            raise exVCenterError(
                "Could not connect to the vCenter using specified username and password")
        atexit.register(connect.Disconnect, service_instance)
        oSContent = service_instance.RetrieveServiceContent()
        if reIPv4_Format.match(sTargetHost):
            oLog.debug('requesting access by IP')
            oHostObj = oSContent.searchIndex.FindByIp(ip=sTargetHost, vmSearch=False)
        else:
            oLog.debug('requesting access by FQDN')
            oHostObj = oSContent.searchIndex.FindByDnsName(dnsName=sTargetHost, vmSearch=False)
        if not oHostObj:
            sRet = ''
            raise exVCenterError("Cannot access a host <" + sTargetHost +
                                 "> object with given name/password")

        oTicket = oHostObj.AcquireCimServicesTicket()
        # print(oTicket)
        if oTicket:
            sRet = str(oTicket.sessionId)
        else:
            sRet = ''
            raise exVCenterError('Cannot receive a ticket from VCenter')
    except vmodl.MethodFault as e:
        sRet = ''
        raise exVCenterError('VMODL fault: ' + e.msg)
    return sRet


class enCtrl(enum.Enum):
    LSI = 1
    EMULEX = 2
    QLOGIC = 3
    SMARTARRAY = 4


def _sFindDisksNameSpace2(oWBEM_Conn):
    """ Returns a namespace with disk controller's data """
    dDiskControllerREs = {enCtrl.LSI: re.compile(r'^lsi/')}
    lElems = oWBEM_Conn.EnumerateInstanceNames(namespace='root/interop', ClassName='CIM_RegisteredProfile')
    enController = None
    sDiskNS = ''
    for oEl in lElems:
        # ищем диск, узнаём имя контроллера
        if RE_DISK.search(oEl['InstanceID']):
            sKeyString = str(oEl.values()[0]).split(':')[0]   # returned value is a LIST with one element
            if sKeyString == 'LSIESG':
                enController = enCtrl.LSI

                # пока не знаю других контроллеров
    if enController is enCtrl.LSI:
        # working with LSI controller
        for oNameSpace in oWBEM_Conn.EnumerateInstanceNames(ClassName='CIM_Namespace',
                                                            namespace='root/interop'):
            sNSName = oNameSpace.get('Name')
            if dDiskControllerREs[enCtrl.LSI].match(sNSName):
                sDiskNS = sNSName
                break
        oLog.debug('Final namespace name: ' + sDiskNS)
    else:
        # unknown controller
        oLog.debug("*ERR* Unknown disk controller")
        sDiskNS = None
    return sDiskNS


def __ldGetDiskParametersFromWBEM__(oConnection, sNS):
    lsClasses = oConnection.EnumerateClassNames(namespace=sNS)
    if ('CIM_ManagedElement' not in lsClasses) or ('CIM_Component' not in lsClasses):
        raise WBEM_Exception('No ManagedElement in class list, wrong server?')
    # check if we have some HDDs. A disk drive is an instance of class CIM_ManagedElement
    sDiskClass = ''
    sPhysDiskClass = ''
    lOtherClasses = []
    loMEs = oConnection.EnumerateInstanceNames(namespace=sNS, ClassName='CIM_ManagedElement')
    for oCIM_Class in loMEs:
        sClassName = oCIM_Class.classname
        if RE_DISK_DRIVE_CLASS.search(sClassName):    # XXX may be it is LSI-Specific
            sDiskClass = sClassName
            oLog.debug('Disk class found: ' + sClassName)
        elif RE_PHYS_DISK_CLASS.search(sClassName):   # XXX LSI-Specific ?
            sPhysDiskClass = sClassName
            oLog.debug('Phys class found: ' + sClassName)
        else:
            # debug
            lOtherClasses.append(sClassName)
            continue
    ldDiskData = []
    # debug
    if sDiskClass == '' and sPhysDiskClass == '':
        oLog.debug("== No disk classes found, but I found some other: ==\n{}".format(
            "\n".join(lOtherClasses)))
    else:
        lDDrives = oConnection.EnumerateInstances(namespace=sNS, ClassName=sDiskClass)
        lPDisks = oConnection.EnumerateInstances(namespace=sNS, ClassName=sPhysDiskClass)
        assert (len(lDDrives) == len(lPDisks))
        for oDsk, oPhy in zip(lDDrives, lPDisks):
            # check if Tags of both objects are the same
            assert oDsk['Tag'] == oPhy['Tag']
            dData = _dMergeDicts(_dFilterNone(dict(oDsk)), _dFilterNone(dict(oPhy)))
            ldDiskData.append(dData)
    return ldDiskData


# def _ldConnectAndReportDisks(sHost, sUser, sPass, iPort=5989):
#     sUrl = 'https://{}:{}'.format(sHost, iPort)
#     try:
#         oConnection = pywbem.WBEMConnection(sUrl, creds=(sUser, sPass), no_verification=True)
#         sDiskNS = _sFindDisksNameSpace2(oConnection)
#         if sDiskNS[0:4] == 'lsi/':   # LSI Disk
#             ldParameters = _ldGetDiskParametersFromWBEM(oConnection, sDiskNS)
#         else:
#             ldParameters = []
#     except pywbem.ConnectionError:
#         ldParameters = []
#         raise WBEM_Disk_Exception('Cannot connect to WBEM on host {} and port {}'.format(sHost, iPort))
#     return ldParameters


class WBEM_Info:
    """super-class for WBEM connections and information collection"""
    def __init__(self, sHost, sUser, sPass, sVCenter='', iPort=5989):
        sUrl = 'https://{}:{}'.format(sHost, iPort)
        if sVCenter == '':
            # no VCenter, use direct WBEM with credentials supplied
            tsCreds = (sUser, sPass)
        else:
            # get a ticket from VCenter
            try:
                sTicket = _sGet_CIM_Ticket(sVCenter, sUser, sPass, sHost)
                oLog.info('Got vCenter ticket {}'.format(sTicket))
                tsCreds = (sTicket, sTicket)
            except exVCenterError as e:
                oLog.error("Error requesting ticket from vCenter" + str(e))
                tsCreds = None
        # now put a credentials to use, request connection from a server
        try:
            self.oConn = pywbem.WBEMConnection(sUrl, tsCreds, no_verification=True)
        except pywbem.ConnectionError:
            self.oConn = None
            raise WBEM_Exception(
                'Cannot connect to WBEM server {} with credentials supplied!'.format(sHost))
        return

    def __ldGetInfoFromWBEM__(self, sNS, sClass):
        """returns WBEM instances as a list of dictionaries with 'None'-valued keys removed"""
        lData = []
        for oInstance in self.oConn.EnumerateInstances(namespace=sNS, ClassName=sClass):
            dOut = {}
            for k, v in oInstance.items():
                if v is not None:
                    dOut[k] = v
            lData.append(dOut)
        return lData


class WBEM_Disks(WBEM_Info):
    def __init__(self, sHost, sUser, sPass, sVCenter='', iPort=5989):
        try:
            super().__init__(sHost, sUser, sPass, sVCenter, iPort)
            self.sDiskNS = _sFindDisksNameSpace2(self.oConn)
        except WBEM_Exception as e:
            raise WBEM_Memory_Exception(e)
        return

    def _ldReportDisks(self):
        try:
            if self.sDiskNS[0:4] == 'lsi/':   # LSI Disk
                oLog.debug('LSI controller found')
                ldParameters = __ldGetDiskParametersFromWBEM__(self.oConn, self.sDiskNS)
            else:
                ldParameters = []
                raise WBEM_Disk_Exception('Unknown disk controller')
        except WBEM_Exception as e:
            raise WBEM_Disk_Exception(e)
        return ldParameters


class WBEM_Memory(WBEM_Info):
    sMemNS = 'root/cimv2'
    MemClassName = ('CIM_PhysicalMemory')

    def __init__(self, sHost, sUser, sPass, sVCenter='', iPort=5989):
        try:
            super().__init__(sHost, sUser, sPass, sVCenter, iPort)
        except WBEM_Exception as e:
            raise WBEM_Memory_Exception(e)
        return

    def _ldGetInfo(self):
        ldData = self.__ldGetInfoFromWBEM__(self.sMemNS, self.MemClassName)
        return ldData


class WBEM_CPU(WBEM_Info):
    sMyNS = 'root/cimv2'
    MyClassName = ('CIM_Processor')

    def __init__(self, sHost, sUser, sPass, sVCenter='', iPort=5989):
        try:
            super().__init__(sHost, sUser, sPass, sVCenter, iPort)
        except WBEM_Exception as e:
            raise WBEM_CPU_Exception(e)
        return

    def _ldGetInfo(self):
        ldData = self.__ldGetInfoFromWBEM__(self.sMyNS, self.MyClassName)
        return ldData


class WBEM_PCI_Adapters(WBEM_Info):
    sMyNS = 'root/cimv2'
    myClassNames = {'gen': 'OMC_Card', 'pci': 'VMware_PCIDevice',
                    'fc': 'IODM_FCAdapter', 'eth': 'VMware_EthernetPort'}

    def __init__(self, sHost, sUser, sPass, sVCenter='', iPort=5989):
        try:
            super().__init__(sHost, sUser, sPass, sVCenter, iPort)
        except WBEM_Exception as e:
            raise WBEM_CPU_Exception(e)
        return

    def _ldGetInfo(self):
        ldPCIData = self.__ldGetInfoFromWBEM__(self.sMyNS, self.myClassNames['pci'])
        oLog.debug("\n".join([str(d) for d in ldPCIData]))
        dAdaptersBySlot = {}
        # filter only devices on additional boards
        for dAdapter in ldPCIData:
            iSlotNo = dAdapter['PhysicalSlot']
            sDevID = dAdapter.get('DeviceID')
            if iSlotNo == 255:
                continue     # onboard adapter, not interesting
            else:
                dAdaptersBySlot[sDevID] = dAdapter
        # check FC adapters
        ldFCAdapters = self.__ldGetInfoFromWBEM__(self.sMyNS, self.myClassNames['fc'])
        oLog.debug('======== FC Adapters =======')
        oLog.debug("\n".join([str(d) for d in ldFCAdapters]))
        oLog.debug("======= PCI cards =======")
        oLog.debug("\n".join([str(d) for d in dAdaptersBySlot.values()]))
        oLog.debug('----------------------------')
        # now we have a dictionary of adapters by device ID with onboard adapters excluded.
        # let's form a list of dictionaries to return
        return dAdaptersBySlot.values()


class WBEM_System(WBEM_Info):
    sMyNS = 'root/cimv2'
    dMyClassNames = {'gen': 'OMC_UnitaryComputerSystem', 'spec': 'OMC_Chassis'}

    def __init__(self, sHost, sUser, sPass, sVCenter='', iPort=5989):
        try:
            super().__init__(sHost, sUser, sPass, sVCenter, iPort)
        except WBEM_Exception as e:
            raise WBEM_System_Exception(e)
        return

    def _dGetInfo(self):
        dRet = {}
        ldGen = self.__ldGetInfoFromWBEM__(self.sMyNS, self.dMyClassNames['gen'])
        ldSpec = self.__ldGetInfoFromWBEM__(self.sMyNS, self.dMyClassNames['spec'])
        # list ldGen usually have 1 element, and ldSpec two, we are interested in [0].
        for dSys in ldGen:
            assert(dSys['CreationClassName'] == 'OMC_UnitaryComputerSystem')
            # dSys['OtherIdentifyingInfo'] is a list of strings (1 element ? Always?)
            sInfoString = dSys.get('OtherIdentifyingInfo')[0]
            # format of this string: three fields separated by dashes
            sName, sModel, sSN = sInfoString.split('-')
            oLog.debug('Server {} mtm {}, sn {}'.format(sName, sModel, sSN))
            dRet['name'] = sName
            dRet['model'] = sModel
        for dSys in ldSpec:
            # we need a serial number, not UUID.
            if 'vendor' not in dRet and 'Manufacturer' in dSys:
                dRet['vendor'] = dSys.get('Manufacturer')
            if 'UUID' not in dSys['SerialNumber']:
                oLog.debug("Confirmation S/N: " + dSys['SerialNumber'])
                dRet['sn'] = dSys['SerialNumber']
        return dRet


if __name__ == "__main__":
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)

    sHostIP = '2demohs21.hostco.ru'    # vmsrv06.msk.protek.local'
    sUser = 'cimuser'          # 'zabbix'
    sPass = '123qweASD'      # 'A3hHr88man01'
    iPort = 5989

    # for d in ld:
    #     print("\n".join([str(t) for t in d.items()]))
    # oMem = WBEM_Memory(sHostIP, sUser, sPass, iPort)
    # print("\n".join([str(d.items()) for d in oMem._ldGetInfo()]))

    # oProc = WBEM_CPU(sHostIP, sUser, sPass, sVCenter='vcenter.hostco.ru')
    # print("\n".join([str(d.items()) for d in oProc._ldGetInfo()]))

    # sTicket = _sGet_CIM_Ticket('vcenter.hostco.ru', 'cimuser', '123qweASD', '2demohs21.hostco.ru')
    oAdapters = WBEM_PCI_Adapters(sHostIP, sUser, sPass, sVCenter='vcenter.hostco.ru')
    print("\n".join([str(d.items()) for d in oAdapters._ldGetInfo()]))

    # oSys = WBEM_System(sHostIP, sUser, sPass, sVCenter='vcenter.hostco.ru')
    # print("\n".join([str(d) for d in oSys._dGetInfo().items()]))

# vim: expandtab:tabstop=4:softtabstop=4:shiftwidth=4
