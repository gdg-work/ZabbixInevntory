#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""" A program for extracting WBEM information from hosts """

import pywbem
import re
import enum
import logging

# reDisk = re.compile(r'\<Disk\>.*\<Drive\>')
RE_DISK = re.compile(r'Disk Drive')
RE_DISK_DRIVE_CLASS = re.compile(r'DiskDrive$')
RE_PHYS_DISK_CLASS = re.compile(r'PhysicalDrive$')

oLog = logging.getLogger(__name__)


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


class WBEM_Disk_Exception(Exception):
    def __init__(self, *lArgs):
        super().__init__(lArgs)


class enCtrl(enum.Enum):
    LSI = 1
    EMULEX = 2
    QLOGIC = 3
    SMARTARRAY = 4


def _sFindDisksNameSpace(oWBEM_Conn):
    """ Returns a namespace with disk controller's data """
    dDiskControllerREs = {enCtrl.LSI: re.compile(r'^lsiprovider-')}
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
        # -- rm -- reProvider = re.compile(r'^lsiprovider-')
        # now we need to list sub-namespaces under sTopNameSpace
        lTopNSs = oWBEM_Conn.EnumerateInstanceNames(namespace='', ClassName='__Namespace')
        for oNs in lTopNSs:
            sVal = oNs['Name']
            if dDiskControllerREs[enController].match(sVal):
                sTopNS = sVal
        lSubNSs = oWBEM_Conn.EnumerateInstanceNames(namespace=sTopNS, ClassName='__Namespace')
        # filter out the root namespace
        lSubNSs = [o for o in lSubNSs if o['Name'] != 'root']
        for oNS in lSubNSs:
            if oNS['Name'] != 'root':    # we don't need a root NS.. already
                lSubNSs = oWBEM_Conn.EnumerateInstanceNames(
                    namespace="{}/{}".format(sTopNS, oNS['Name']),
                    ClassName='__Namespace')
                if len(lSubNSs) == 1:   # must be only one namespace
                    sDiskNS = oNS['Name'] + '/' + lSubNSs[0]['Name']
        oLog.debug('Final namespace name: ' + sDiskNS)

    else:
        # unknown controller
        oLog.debug("*ERR* Unknown disk controller")
        sDiskNS = None
    return sDiskNS


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


def _ldGetDiskParametersFromWBEM(oConnection, sNS):
    lsClasses = oConnection.EnumerateClassNames(namespace=sNS)
    if ('CIM_ManagedElement' not in lsClasses) or ('CIM_Component' not in lsClasses):
        raise Exception
    # check if we have some HDDs. A disk drive is an instance of class CIM_ManagedElement
    sDiskClass = ''
    sPhysDiskClass = ''
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
            continue
    lDDrives = oConnection.EnumerateInstances(namespace=sNS, ClassName=sDiskClass)
    lPDisks = oConnection.EnumerateInstances(namespace=sNS, ClassName=sPhysDiskClass)
    ldDiskData = []
    assert (len(lDDrives) == len(lPDisks))
    for oDsk, oPhy in zip(lDDrives, lPDisks):
        # check if Tags of both objects are the same
        assert oDsk['Tag'] == oPhy['Tag']
        dData = _dMergeDicts(_dFilterNone(dict(oDsk)), _dFilterNone(dict(oPhy)))
        ldDiskData.append(dData)
    return ldDiskData


def _ldConnectAndReportDisks(sHost, sUser, sPass, iPort=5989):
    sUrl = 'https://{}:{}'.format(sHost, iPort)
    try:
        oConnection = pywbem.WBEMConnection(sUrl, creds=(sUser, sPass), no_verification=True)
        sDiskNS = _sFindDisksNameSpace2(oConnection)
        if sDiskNS[0:4] == 'lsi/':   # LSI Disk
            ldParameters = _ldGetDiskParametersFromWBEM(oConnection, sDiskNS)
        else:
            ldParameters = []
    except pywbem.ConnectionError:
        ldParameters = []
        raise WBEM_Disk_Exception('Cannot connect to WBEM on host {} and port {}'.format(sHost, iPort))
    return ldParameters

if __name__ == "__main__":
    # sHostIP = '10.1.128.231'    # vmsrv06.msk.protek.local'
    # sUser = 'cimuser'          # 'zabbix'
    # sPass = 'cimpassword'      # 'A3hHr88man01'
    sHostIP = '10.1.128.229'    # vmsrv06.msk.protek.local'
    sUser = 'root'          # 'zabbix'
    sPass = 'password'      # 'A3hHr88man01'
    iPort = 5989

    ld = _ldConnectAndReportDisks(sHostIP, sUser, sPass)
    for d in ld:
        print("\n".join([str(t) for t in d.items()]))

# vim: expandtab:tabstop=4:softtabstop=4:shiftwidth=4
