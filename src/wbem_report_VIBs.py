#!/usr/bin/env python3
import re
import pywbem
import logging
import atexit
import ssl
import argparse as ap
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


def _dGetSoftwareIdentityList(oConn):
    """"
    Parameters: 1 -- connection
    returns: list of version strings for installed software identities.
    """
    dInstalledSW = {}  # {name:(description, version), ...}
    # sGenericSWClass = 'CIM_SoftwareIdentity'
    sVendorSWClass = 'VMware_ElementSoftwareIdentity'
    NAMESPACE = 'root/cimv2'
    INSTALLED = 6
    loVendorSWNames = oConn.EnumerateInstanceNames(namespace=NAMESPACE, ClassName=sVendorSWClass)
    for oEl in loVendorSWNames:
        oInst = oConn.GetInstance(oEl)
        # oSIName = oConn.References(oInst, ResultClass='VMware_ElementSoftwareIdentity')
        # print(str(oSIName))
        oStatus = oInst.properties['ElementSoftwareStatus']
        if oStatus.value[0] == INSTALLED:
            oAntecedentName = oInst.properties['Antecedent'].value
            oAntecendent = oConn.GetInstance(oAntecedentName)
            sID = oAntecendent['InstanceID']
            sDescr = oAntecendent['Description']
            sVer = oAntecendent['VersionString']
            sCap = oAntecendent['Caption']
            dInstalledSW[sCap] = (sDescr, sVer)
    return dInstalledSW


def _sGetClassName(oConnection):
    """return class name/instance name for top-level object"""
    FIRSTNS = 'root/interop'
    lsClasses = oConnection.EnumerateClassNames(namespace=FIRSTNS)
    print(lsClasses)
    return

def _PrintSoftwareList(dSW, oConf):
    # print a list of software in alphabetical order with an optional header
    sFormat = "{0:<20} {1:<40} {2:<1}"
    if oConf.header:
        print(sFormat.format("Name,", "Description,", "Version,"))
        print(sFormat.format(('#' + "-" * 19), "-" * 40, "-" * 30))
    # get list of keys and sort these keys
    lKeys = list(dSW.keys())
    lKeys.sort()
    for sKey in lKeys:
        sDescr, sVer = dSW[sKey]
        print(sFormat.format(sKey + ',', sDescr + ',', sVer))
    return


def _dListSWOnServer(sHostLong, sUser, sPass, sVCenter=''):
    """oAccess is an instance of server class from 'access.py' module"""
    try:
        if sVCenter:
            oConn = oMakeConnection(sHostLong, sUser, sPass, sVCenter=sVCenter)
        else:
            oConn = oMakeConnection(sHostLong, sUser, sPass)
    except pyVmomi.exVCenterError as e:
        oLog.error('Cannot connect to server with a given credentials')
        oLog.error('Message: ' + str(e))
        raise e
    return _dGetSoftwareIdentityList(oConn)


def _oGetCLIParser():
    oParser = ap.ArgumentParser(description="This program lists installed VIBs on VMware server")
    oParser.add_argument('-u', '--user', help="User name to access vCenter for a ticket",
                         type=str, required=True)
    oParser.add_argument('-p', '--password', help="Password to access vCenter for a ticket",
                         type=str, required=True)
    oParser.add_argument('-v', '--vcenter', help="vCenter server to get ticket from",
                         type=str, required=False, default='')
    oParser.add_argument('-H', '--header', help="DO NOT print a 2-line header in output",
                         action='store_false', required=False)
    oParser.add_argument('servers', help="TTL of Redis-cached data", type=str, nargs=ap.REMAINDER)
    return (oParser.parse_args())

def _MainFunction():
    oConf = _oGetCLIParser()
    for sHost in oConf.servers:
        print("Trying to connect to server {}".format(sHost))
        dSoft = _dListSWOnServer(sHost, oConf.user, oConf.password, oConf.vcenter)
        _PrintSoftwareList(dSoft, oConf)
    return


if __name__ == '__main__':
    # logging setup
    oLog.setLevel(logging.DEBUG)
    oConHdr = logging.StreamHandler()
    # oConHdr.setLevel(logging.DEBUG)
    oLog.addHandler(oConHdr)
    _MainFunction()
