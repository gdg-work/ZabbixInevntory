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


class WBEM_PowerSupply_Exception(WBEM_Exception):
    pass


class WBEM_HBA_Exception(WBEM_Exception):
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


def _sFindDisksNameSpace(oWBEM_Conn):
    """ Returns a namespace with disk controller's data """
    dDiskControllerREs = {enCtrl.LSI: re.compile(r'^lsi/')}
    enController = None
    sDiskNS = ''
    try:
        lElems = oWBEM_Conn.EnumerateInstanceNames(ClassName='CIM_RegisteredProfile',
                                                   namespace='root/interop',)
    except pywbem.cim_operations.CIMError as e:
        # cannot enumerate registered profiles, something is really wrong
        sDiskNS = ''
        oLog.error('CIM error trying to find disk namespace')
        raise WBEM_Disk_Exception(e)

    for oEl in lElems:
        # search for controller name
        if RE_DISK.search(oEl['InstanceID']):
            sKeyString = str(oEl.values()[0]).split(':')[0]   # returned value is a LIST with one element
            if sKeyString == 'LSIESG':
                enController = enCtrl.LSI
                # пока не знаю других контроллеров
            else:
                oLog.info('Unknown disk controller, class=' + oEl.values()[0])
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
        oLog.info("*ERR* Unknown disk controller")
        sDiskNS = None
    return sDiskNS


def _ldGetDiskParametersFromWBEM(oConnection, sNS):
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
        oLog.info("== No disk classes found, but I found some other: ==\n{}".format(
            "\n".join(lOtherClasses)))
    else:
        try:
            lDDriveNames = oConnection.EnumerateInstanceNames(namespace=sNS, ClassName=sDiskClass)
            # oLog.debug('lDDriveNames: ' + str(lDDriveNames))
            lPDiskNames = oConnection.EnumerateInstanceNames(namespace=sNS, ClassName=sPhysDiskClass)
            # oLog.debug('lPDiskNames: ' + str(lPDiskNames))
        except pywbem.cim_operations.CIMError as e:
            raise WBEM_Exception(e)
        assert (len(lDDriveNames) == len(lPDiskNames))
        for oDskName, oPhyName in zip(lDDriveNames, lPDiskNames):
            try:
                oDsk = oConnection.GetInstance(oDskName)
                oLog.debug('oDsk object: ' + str(oDsk))
                oLog.debug('Getting PhysDisk based on oPhyName: ' + str(oPhyName))
                oPhy = oConnection.GetInstance(oPhyName)
                oLog.debug('oPhy object: ' + str(oPhy))
                assert oDsk['Tag'] == oPhy['Tag']
                dData = _dMergeDicts(_dFilterNone(dict(oDsk)), _dFilterNone(dict(oPhy)))
                ldDiskData.append(dData)
            except pywbem.cim_operations.CIMError as e:
                oLog.error("_ldGetDiskParametersFromWBEM: CIM error getting Drive/PhysDisk instances: {}".format(oPhyName.get('Tag')))
                # raise(WBEM_Exception(e))
            # check if Tags of both objects are the same
    return ldDiskData


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
                raise WBEM_Exception(
                    'Invalid authentication data for vCenter ticket to host {}'.format(sHost))
        # now put a credentials to use, request connection from a server
        try:
            self.oConn = pywbem.WBEMConnection(sUrl, tsCreds, no_verification=True)
        except pywbem.ConnectionError:
            self.oConn = None
            raise WBEM_Exception(
                'Cannot connect to WBEM server {} with credentials supplied!'.format(sHost))
        return


    def _ldGetInfoFromWBEM(self, sNS, sClass):
        """returns WBEM instances as a list of dictionaries with 'None'-valued keys removed"""
        lData = []
        try:
            lInstNames = self.oConn.EnumerateInstanceNames(namespace=sNS, ClassName=sClass)
        except Exception as e:
            oLog.error('CIM error in _ldGetInfoFromWBEM: ' + str(e))
            raise WBEM_Exception('Cannot receive information from WBEM in _ldGetInfoFromWBEM()')

        for oIN in lInstNames:
            dOut = {}
            try:
                oInstance = self.oConn.GetInstance(oIN)
            except Exception as e:
                raise WBEM_Exception('GetInstance failed in _ldGetInfoFromWBEM: {}'.format(str(e)))
            for k, v in oInstance.items():
                if v is not None:
                    dOut[k] = v
            lData.append(dOut)
        return lData

    def _loGetInstanceNames(self, sNS, sClass):
        """wrapper over pywbem.WBEMConnection.EnumerateInstances"""
        return self.oConn.EnumerateInstanceNames(namespace=sNS, ClassName=sClass)

    def _loGetInstances(self, sNS, sClass):
        """wrapper over pywbem.WBEMConnection.EnumerateInstances"""
        return self.oConn.EnumerateInstances(namespace=sNS, ClassName=sClass)

    def _oGetInstance(self, oInstName):
        """wrapper over pywbem.WBEMConnection.GetInstance"""
        return self.oConn.GetInstance(oInstName)

    def _loGetClassNames(self, sNS='root/interop', sClassName=None):
        if sClassName:
            return self.oConn.EnumerateClassNames(sNS, ClassName=sClassName)
        else:
            return self.oConn.EnumerateClassNames(sNS)


class WBEM_Disks(WBEM_Info):
    def __init__(self, sHost, sUser, sPass, sVCenter='', iPort=5989):
        try:
            super().__init__(sHost, sUser, sPass, sVCenter, iPort)
            self.sDiskNS = _sFindDisksNameSpace(self.oConn)
        except WBEM_Exception as e:
            raise WBEM_Disk_Exception(e)
        return

    def _ldReportDisks(self):
        if self.sDiskNS is not None:
            try:
                if self.sDiskNS[0:4] == 'lsi/':   # LSI Disk
                    oLog.debug('LSI controller found')
                    ldParameters = _ldGetDiskParametersFromWBEM(self.oConn, self.sDiskNS)
                else:
                    ldParameters = []
                    raise WBEM_Disk_Exception('Unknown disk controller')
            except WBEM_Exception as e:
                raise WBEM_Disk_Exception(e)
        else:
            ldParameters = []
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
        ldData = self._ldGetInfoFromWBEM(self.sMemNS, self.MemClassName)
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
        ldData = self._ldGetInfoFromWBEM(self.sMyNS, self.MyClassName)
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
        ldPCIData = self._ldGetInfoFromWBEM(self.sMyNS, self.myClassNames['pci'])
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
        ldFCAdapters = self._ldGetInfoFromWBEM(self.sMyNS, self.myClassNames['fc'])
        oLog.debug('======== FC Adapters =======')
        oLog.debug("\n".join([str(d) for d in ldFCAdapters]))
        oLog.debug("======= PCI cards =======")
        oLog.debug("\n".join([str(d) for d in dAdaptersBySlot.values()]))
        oLog.debug('----------------------------')
        # now we have a dictionary of adapters by device ID with onboard adapters excluded.
        # let's form a list of dictionaries to return
        return dAdaptersBySlot.values()


class WBEM_HBAs_Old(WBEM_Info):
    sMyNS = 'root/cimv2'
    sMyClass = 'IODM_FCAdapter'
    dAttrsToRet = {
        'SerialNumber': 'sn',
        'HostNodeName': 'wwn',
        'ModelDescription': 'type',
        'Vendor': 'vendor',
        'DeviceID': 'id',
        'Model': 'model'}

    def __init__(self, sHost, sUser, sPass, sVCenter='', iPort=5989):
        try:
            super().__init__(sHost, sUser, sPass, sVCenter, iPort)
        except WBEM_Exception as e:
            oLog.error('Error scanning HBAs: ' + str(e))
            raise WBEM_HBA_Exception(str(e))

    def __repr__(self):
        return "VMware HBA info access class: host={}".format(str(self.oConn))

    def _ldReportAdapters(self):
        ldRet = []
        try:
            loFCADaptersData = self._ldGetInfoFromWBEM(self.sMyNS, self.sMyClass)
        except WBEM_Exception as e:
            raise WBEM_HBA_Exception("Error reporting HBAs:" + str(e))
        # return only the relevant information
        for oHBAData in loFCADaptersData:
            dRet = {}
            for k, v in self.dAttrsToRet.items():
                dRet[v] = oHBAData.get(k)
            # PCI position is a special case
            dRet['pos'] = "PCI: {0}/{1}/{2}".format(
                oHBAData.get('PciBus'), oHBAData.get('PciSlot'), oHBAData.get('PciFunction'))
            ldRet.append(dRet)
        return ldRet


class WBEM_HBAs(WBEM_Info):
    sMyNS = 'root/cimv2'
    sMyClass = 'IODM_FCAdapter'
    dAttrsToRet = {
        'SerialNumber': 'sn',
        'HostNodeName': 'wwn',
        'ModelDescription': 'type',
        'Vendor': 'vendor',
        'DeviceID': 'id',
        'Model': 'model'}

    def __init__(self, sHost, sUser, sPass, sVCenter='', iPort=5989):
        try:
            super().__init__(sHost, sUser, sPass, sVCenter, iPort)
        except WBEM_Exception as e:
            oLog.error('Error scanning HBAs: ' + str(e))
            raise WBEM_HBA_Exception(str(e))

    def __repr__(self):
        return "VMware HBA info access class: host={}".format(str(self.oConn))

    def _ldReportAdapters(self):
        QLA_NS = 'qlogic/cimv2'
        # ldRet = []
        ldQLA_Data = []
        try:
            lsSubClasses1 = self._loGetClassNames(sNS='root/interop', sClassName='CIM_Namespace')
            # lsSubClasses1 is a list of strings (names)
            for sCN in lsSubClasses1:
                if 'HBA' in sCN and 'QLogic' in sCN:            # QLogic-specific
                    oLog.debug('QLogic HBA namespace found: ' + sCN)
                    # check if QLogic namespace is present in CIM_Namespace instances
                    loNSInstanceNames = self._loGetInstanceNames(sNS='root/interop', sClass='CIM_Namespace')
                    lsInstNames = [ns['Name'] for ns in loNSInstanceNames]
                    oLog.debug('CIM namespaces: {}'.format(str(lsInstNames)))
                    # filter QLogic NS
                    if QLA_NS in lsInstNames:
                        oLog.debug('QLogic-specific NS {} found!'.format(QLA_NS))
                        sQLA_ProdClass = self._loGetClassNames(QLA_NS, sClassName='CIM_Product')[0]
                        oLog.debug('QLA Product subclass name: ' + sQLA_ProdClass)
                        ldQLA_ProdData = self._ldGetInfoFromWBEM(QLA_NS, sQLA_ProdClass)
                        sQLA_PkgClass = self._loGetClassNames(QLA_NS, sClassName='CIM_PhysicalPackage')[0]
                        oLog.debug('QLA PhysicalPackage subclass name: ' + sQLA_PkgClass)
                        ldQLA_PkgData = self._ldGetInfoFromWBEM(QLA_NS, sQLA_PkgClass)
                        # combine dictionaries:
                        lDcts = zip(ldQLA_ProdData, ldQLA_PkgData)
                        ldQLA_Data = []
                        for a, b in lDcts:
                            ldQLA_Data.append(_dMergeDicts(a, b))

                        oLog.debug('QLA data: ' + str(ldQLA_Data))
                    else:
                        raise WBEM_HBA_Exception('Incorrect QLogic adapter namespace: ' +
                                                      'no "qlogic/cimv2" NS in defined NS')
                else:
                    pass   # silently pass all non-QLogic NS

        except WBEM_Exception as e:
            raise WBEM_HBA_Exception("Error reporting HBAs:" + str(e))
        return ldQLA_Data



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
        try:
            ldGen = self._ldGetInfoFromWBEM(self.sMyNS, self.dMyClassNames['gen'])
            ldSpec = self._ldGetInfoFromWBEM(self.sMyNS, self.dMyClassNames['spec'])
        except WBEM_Exception as e:
            raise WBEM_System_Exception(e)
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

    def _ssGetNameSpaces(self):
        """return a list of namespaces for this host"""
        sMyNS = 'root/interop'
        sMyClass = 'CIM_Namespace'
        sRet = set()
        loNS_Instances = self._loGetInstanceNames(sMyNS, sMyClass)
        for oInst in loNS_Instances:
            sRet.add(oInst['Name'])
        return list(sRet)

    def _sGetHBANameSpace(self):
        lNSs = self._ssGetNameSpaces()
        sHBA_NS = ''
        for sNSName in lNSs:
            if sNSName == 'root/cimv2':
                # we know that no HBAs ever exist in this namespace, and this NS is very large
                continue
            try:
                loInstNames = self._loGetInstanceNames(sNSName, 'CIM_PhysicalPackage')
                if loInstNames:
                    for oIN in loInstNames:
                        oLog.debug('\n--------- Instance Name: {} ---------'.format(oIN.classname))
                        # print(oIN)
                        lAssocNames = (self.oConn.AssociatorNames(
                            oIN,
                            ResultClass='CIM_PortController'))
                        for oAN in lAssocNames:
                            if 'HBA' in oAN.classname:
                                oLog.debug(self.oConn.GetInstance(oAN))
                                sHBA_NS = sNSName
                                oLog.debug(self.oConn.AssociatorNames(oAN))
            except pywbem.cim_operations.CIMError as e:
                # probably there is no such class
                pass
        return sHBA_NS

    def _loGetHBAInstances(self):
        """Return a list of HBA instances (subclass of class CIM_PhysicalPackage)"""
        lNSs = self._ssGetNameSpaces()
        lHBAs = list()
        for sNSName in lNSs:
            if sNSName == 'root/cimv2':
                # we know that no HBAs ever exist in this namespace, and this NS is very large
                continue
            try:
                loInstNames = self._loGetInstanceNames(sNSName, 'CIM_PhysicalPackage')
                for oIN in loInstNames:
                    # print(oIN)
                    lAssocNames = (self.oConn.AssociatorNames(
                        oIN, ResultClass='CIM_PortController'))
                    # oLog.debug('\n--------- Instance Name: {} ---------'.format(oIN.classname))
                    for oAN in lAssocNames:
                        lAssoc = self.oConn.AssociatorNames(
                            oAN, ResultClass='CIM_ProtocolControllerForDevice')

                        # oLog.debug("\n".join([o.__repr__() for o in lAssoc]))
                        bPortFound, bSysFound, bPkgFound = (False, False, False)
                        for oAssoc2Name in lAssoc:
                            bPortFound = bPortFound or 'FCPort' in oAssoc2Name.classname
                            bSysFound =  bSysFound  or 'ComputerSystem' in oAssoc2Name.classname
                            bPkgFound =  bPkgFound  or 'PhysicalPackage' in oAssoc2Name.classname
                        if bPkgFound and bSysFound and bPortFound:
                            oInstance = self.oConn.GetInstance(oIN)
                            lHBAs.append(oInstance)
            except pywbem.cim_operations.CIMError as e:
                # probably there is no such class
                pass
        return list(lHBAs)

    def _loGetIntegratedDiskControllers(self):
        """return a list of integrated disk controllers instances"""
        lNSs = self._ssGetNameSpaces()
        lRCs = []
        for sNSName in lNSs:
            if sNSName == 'root/cimv2':
                # we know that no HBAs ever exist in this namespace, and this NS is very large
                continue
            try:
                loInstNames = self._loGetInstanceNames(sNSName, 'CIM_PhysicalPackage')
                for oIN in loInstNames:
                    print(str(oIN))
                    # search for IntegratedRAIDChip class between results

            except pywbem.cim_operations.CIMError as e:
                # probably there is no such class
                pass
        return list(lRCs)


class WBEM_PowerSupplySet(WBEM_Info):
    """Set of one or more power supplies. Often this is a redundant set of PS for a server"""
    sMyNS = 'root/cimv2'
    sProfileClassName = 'OMC_RegisteredPowerSupplyProfile'
    sPwrSupplyClassName = 'OMC_PowerSupply'
    lMyClassNames = ['OMC_PowerSupplyRedundancySet',
                     'OMC_MemberOfPowerSupplyRedundancySet',
                     'OMC_PowerSupply']

    def __init__(self, sHost, sUser, sPass, sVCenter='', iPort=5989):
        try:
            super().__init__(sHost, sUser, sPass, sVCenter, iPort)
        except WBEM_Exception as e:
            raise WBEM_PowerSupply_Exception('WBEM_PowerSupplySet: error in __init__: ' + str(e))
        return

    def __lGetPwrSupplyNames(self):
        """internal function, returns list of Power Supply names"""
        lRet = []
        try:
            lPSProfiles = self._loGetInstanceNames(sClass=self.sProfileClassName, sNS='root/interop')
        except Exception as e:
            oLog.error('Cannot enumerate Power Supplies class, something is really wrong')
            raise WBEM_PowerSupply_Exception('Cannot enumerate PS class')
        # print(lPSProfiles)
        for oProfName in lPSProfiles:
            lPSs = self.oConn.AssociatorNames(oProfName, ResultClass=self.sPwrSupplyClassName)
            lRet += lPSs
        return lRet

    def _lGetPoweSupplies(self):
        """Returns a list of CIM instances corresponding to power supply profile"""
        lRet = []
        lPSNames = self.__lGetPwrSupplyNames()
        for oPSInstanceName in lPSNames:
            lRet.append(self.oConn.GetInstance(oPSInstanceName))
        return lRet

    def _iGetPwrSuppliesAmount(self):
        """returns amount of power supplies in this system"""
        return len(self.__lGetPwrSupplyNames())

if __name__ == "__main__":
    # access for a test system
    from access import vmexchsrv01 as tsys
    # from access import demobl460_host as tsys

    # logging setup
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)

    # for d in ld:
    #     print("\n".join([str(t) for t in d.items()]))
    # oMem = WBEM_Memory(tsys.sHostLong, tsys.sUser, tsys.sPass, sVCenter=tsys.sVCenter)
    # print("\n".join([str(d.items()) for d in oMem._ldGetInfo()]))

    # oProc = WBEM_CPU(tsys.sHostLong, tsys.sUser, tsys.sPass , sVCenter=tsys.sVCenter)
    # print("\n".join([str(d.items()) for d in oProc._ldGetInfo()]))

    # oAdapters = WBEM_PCI_Adapters(tsys.sHostLong, tsys.sUser, tsys.sPass, sVCenter=tsys.sVCenter)
    # oAdapters = WBEM_HBAs(tsys.sHostLong, tsys.sUser, tsys.sPass)
    # print("\n".join([str(d.items()) for d in oAdapters._ldGetInfo()]))

    # oAdapters = WBEM_HBAs(tsys.sHostLong, tsys.sUser, tsys.sPass, sVCenter=tsys.sVCenter)
    # oSys = WBEM_System(tsys.sHostIP, tsys.sUser, tsys.sPass, sVCenter=tsys.sVCenter)
    # print("\n".join(oSys._ssGetNameSpaces()))
    # print(oSys._sGetHBANameSpace())
    # for i in oSys._loGetHBAInstances():
    #     print("\n".join([str(k) + "=" + str(v) for k, v in i.items()]))

    # print(oSys._loGetIntegratedDiskControllers())

    # oPS = WBEM_PowerSupplySet(tsys.sHostLong, tsys.sUser, tsys.sPass, sVCenter=tsys.sVCenter)
    # print(oPS._iGetPwrSuppliesAmount())
 
    # oDisksWBEM = WBEM_Disks(tsys.sName, tsys.sUser, tsys.sPass, sVCenter=tsys.sVCenter)
    # ldDisks = oDisksWBEM._ldReportDisks()
    # print(str(ldDisks))

    oHBAs = WBEM_HBAs(tsys.sName, tsys.sUser, tsys.sPass, sVCenter=tsys.sVCenter)
    ldHBAs = oHBAs._ldReportAdapters()
    print(str(ldHBAs))

# vim: expandtab:tabstop=4:softtabstop=4:shiftwidth=4
