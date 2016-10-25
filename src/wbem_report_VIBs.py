#!/usr/bin/env python3
import re
import pywbem
import logging
import atexit
import ssl
from pyVim import connect
from pyVmomi import vmodl


SSL_PROTO = ssl.PROTOCOL_SSLv23
SSL_VERIFY_MODE = ssl.CERT_NONE    # <--- XXX don't verify anything!
RE_PHYS_DISK_CLASS = re.compile(r'PhysicalDrive$')
oLog = logging.getLogger('test-wbem')


# Helper function
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


class exVCenterError(Exception):
    pass


def _sGet_CIM_Ticket(sVCenterHost, sUser, sPass, sTargetHost, iPort=443):
    """Retrieves CIM session ticket from vCenter
    Parameters:
      sVCenterHost: VCenter hostname or IP,
      sUser, sPass: user credentials for vCenter,
      sTargetHost: ESXi host for CIM access
    Returns:
      Session ID (UUID) as a string
    """
    # reIPv4_Format = re.compile(r'\d{1-3}.\d{1-3}.\d{1-3}.\d{1-3}')
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
        oHostObj = oSContent.searchIndex.FindByDnsName(dnsName=sTargetHost, vmSearch=False)
        if not oHostObj:
            sRet = ''
            raise exVCenterError("Cannot access a host <" + sTargetHost +
                                 "> object with given name/password")

        oTicket = oHostObj.AcquireCimServicesTicket()
        if oTicket:
            sRet = str(oTicket.sessionId)
        else:
            sRet = ''
            raise exVCenterError('Cannot receive a ticket from VCenter')
    except vmodl.MethodFault as e:
        sRet = ''
        raise exVCenterError('VMODL fault: ' + e.msg)
    return sRet


def oMakeConnection(sHost, sUser, sPass, sVCenter, iPort=5989):
    sUrl = 'https://{}:{}'.format(sHost, iPort)
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
        oConn = pywbem.WBEMConnection(sUrl, tsCreds, no_verification=True)
    except pywbem.ConnectionError:
        oConn = None
        raise WBEM_Exception(
            'Cannot connect to WBEM server {} with credentials supplied!'.format(sHost))
    return oConn


def lsGetSoftwareIdentityList(oConn):
    """"
    Parameters: 1 -- connection
    returns: list of version strings for installed software identities.
    """
    lInstalledSW = []
    # sGenericSWClass = 'CIM_SoftwareIdentity'
    sVendorSWClass = 'VMware_ElementSoftwareIdentity'
    NAMESPACE = 'root/cimv2'
    INSTALLED = 6
    loVendorSWNames = oConn.EnumerateInstanceNames(namespace=NAMESPACE, ClassName=sVendorSWClass)
    sFormat = "{0:<20}, {1:<30}, {2:<30}"
    print(sFormat.format("Name", "Description", "Version"))
    print(sFormat.format("-" * 20, "-" * 30, "-" * 30))
    for oEl in loVendorSWNames[0:8]:    # XXX for debugging, not so much data
        # print(str(oEl))
        oInst = oConn.GetInstance(oEl)
        # oSIName = oConn.References(oInst, ResultClass='VMware_ElementSoftwareIdentity')
        # print(str(oSIName))
        oStatus = oInst.properties['ElementSoftwareStatus']
        if oStatus.value[0] == INSTALLED:
            # print("Value: <{}>".format(oStatus.value[0]))
            oAntecedentName = oInst.properties['Antecedent'].value
            # print(str(oAntecedentName['InstanceID']))
            oAntecendent = oConn.GetInstance(oAntecedentName)
            sID = oAntecendent['InstanceID']
            sDescr = oAntecendent['Description']
            sVer = oAntecendent['VersionString']
            sCap = oAntecendent['Caption']
            print(sFormat.format(sCap, sDescr, sVer))

        # sSW_ID = oEl.get('InstanceID', '')
    return lInstalledSW


def _sGetClassName(oConnection):
    """return class name/instance name for top-level object"""
    FIRSTNS = 'root/interop'
    lsClasses = oConnection.EnumerateClassNames(namespace=FIRSTNS)
    print(lsClasses)
    return


def _MainFunction(oAccess):
    """oAccess is an instance of server class from 'access.py' module"""
    # from access import demobl460_host as tsys

    # logging setup
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    # oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)

    oConn = oMakeConnection(oAccess.sHostLong, oAccess.sUser, oAccess.sPass, sVCenter=oAccess.sVCenter)
    lsGetSoftwareIdentityList(oConn)
    return

if __name__ == '__main__':
    from access import demohs21_host as sys1
    _MainFunction(sys1)
