#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A module for supporting IBM XIV scale-out storage systems.
Each component of XIV has an unique identifier (component_id) that depends of
place given for this component in the system.  For example: "1:Disk:2:10".


При рефакторинге нужно будет переписать с вызовом Java класса из Python
через какой-нибудь py/java интерфейс (pyjnius не заработал на целевой машине)
"""

import inventoryObjects as inv
from local import XCLI_PATH, REDIS_ENCODING
from subprocess import check_output, CalledProcessError, STDOUT
# from redis import StrictRedis
import csv
import os
import logging

# CONSTANTS
REDIS_PREFIX = "pyzabbix::FlashSys::"
FAKE_HOME = '/tmp/'


oLog = logging.getLogger(__name__)


# -- Helper functions --
def _iGetNodeNum(sID):
    sRet = ''
    sSep = ':'
    iColonsCount = sID.count(sSep)
    if iColonsCount == 3:
        sRet = int(sID.split(sSep)[2])
    elif iColonsCount == 2:
        sRet = int(sID.split(sSep)[2])
    else:
        oLog.error("_iGetNodeNum: Incorrect Component-ID structure")
    return sRet


def _sMkNodeID(iNum):
    return "1:Module:{}".format(iNum)


class XIVError(Exception):
    def __init__(self, sMsg):
        self.sMsg = sMsg
        return

    def __str__(self):
        return self.sMsg


class IBM_XIV_Storage(inv.ScaleOutStorageClass):
    def __init__(self, sIP, sUser, sPass, oRedis, sName):
        self.sRedisPrefix = REDIS_PREFIX + "XIV::" + sName + "::"
        self.sIP = sIP
        self.sSysName = sName
        self.sUser = sUser
        self.sPass = sPass
        self.oRedisDB = oRedis
        self.oNodesList = IBM_XIV_NodesList(self)
        self.oDisksList = IBM_XIV_DisksList(self)
        self.oCFList = IBM_XIV_CompactFlashList(self)
        self.oDIMMs = IBM_XIV_DIMMSlist(self)
        self.oPSUs = IBM_XIV_PwrSuppliesList(self)
        self.oUPSs = IBM_XIV_UPS_List(self)
        self.oSwitches = IBM_XIV_SwitchesList(self)
        self.oMMs = IBM_XIV_MaintenanceModulesList(self)
        self.oNICs = IBM_XIV_NICsList(self)
        self.oFCs = IBM_XIV_FCPortsList(self)
        self.dQueries = {"node-names":   self.oNodesList._lsListNames,
                         "switch-names": self.oSwitches._lsListNames,
                         "disk-names":   self.oDisksList._lsListNames,
                         "ups-names":    self.oUPSs._lsListNames,
                         "cf-names":     self.oCFList._lsListNames,
                         "nodes":        self.oNodesList._iLength,
                         "disks":        self.oDisksList._iLength,
                         "fc-ports":     self.oFCs._iLength,
                         "eth-ports":    self.oNICs._iLength,
                         "dimm-names":   self.oDIMMs._lsListNames,
                         "memory":       self.oDIMMs._iTotalGBs
                         }
        return

    def _lsRunCommand(self, sCmd):
        """runs a command, caches output in Redis"""
        sRedisKey = self.sRedisPrefix + sCmd
        try:
            sLine = self.oRedisDB.get(sRedisKey).decode(REDIS_ENCODING)
            oLog.debug('_lsRunCommand: Data from Redis')
        except AttributeError:
            # no line in Redis
            try:
                os.environ['XIV_XCLIUSER'] = self.sUser
                os.environ['XIV_XCLIPASSWORD'] = self.sPass
                os.environ['HOME'] = FAKE_HOME
                lCommand = [XCLI_PATH, '-y', '-m', self.sIP, sCmd, '-s']
                # sLine = check_output(lCommand, stderr=STDOUT, universal_newlines=True, shell=False)
                sLine = check_output(' '.join(lCommand), stderr=STDOUT, universal_newlines=True, shell=True)
                self.oRedisDB.set(sRedisKey, sLine.encode(REDIS_ENCODING))
            except CalledProcessError as e:
                sLine = e.output
        lRet = sLine.split('\n')
        return lRet

    def _ldGetInfoDict(self, sParamName):
        """returns a list of information dictionaries corresponding to parameter. For example,
        if sParamName is 'node-names', returns a result of _ldGetNodeNames() function. So, this
        method is just a dispatcher to simplify calling modules"""
        ldRet = []
        try:
            if sParamName == 'node-names':
                ldRet = self._ldGetNodesAsDicts()
            elif sParamName == 'switch-names':
                ldRet = self._ldGetSwitchesAsDicts()
            elif sParamName == 'disk-names':
                ldRet = self._ldGetDisksAsDicts()
            elif sParamName == 'ups-names':
                ldRet = self._ldGetUPSesAsDicts()
            elif sParamName == 'dimm-names':
                ldRet = self._ldGetDIMMsAsDicts()
            elif sParamName == 'cf-names':
                ldRet = self.oCFList._ldGetData()
            else:
                ldRet = [{}]
                oLog.error("_ldGetInfoDict: incorrect parameter")
        except Exception as e:
            oLog.warning("Exception when filling components' parameters list")
            oLog.warning("Exception: " + str(e))
            ldRet = [{}]
        return ldRet

    def _dGetArrayInfoAsDict(self, ssKeys):
        """
        Array-wide parameters as a dictionary.  Parameter -- a set of
        keys/requests.
        Returns: a dictionary {key:value}
        """
        dRet = {}
        for sKey in ssKeys:
            if sKey in self.dQueries:
                dRet[sKey] = self.dQueries[sKey]()
        return dRet

    def _ldGetDisksAsDicts(self):
        """ Return disk data as a list of Python dictionaries with fields:
        name, SN, type, model, size, position
        """
        ldRet = []
        try:
            ldRet = self.oDisksList._ldGetData()
        except Exception as e:
            oLog.warning("Exception when filling a disk parameters list")
            oLog.debug("Exception: " + str(e))
        return ldRet

    def _ldGetNodesAsDicts(self):
        """Return nodes' information as a list of dicts"""
        ldRet = []
        try:
            ldRet = self.oNodesList._ldGetData()
        except Exception as e:
            oLog.warning("Exception when filling a nodes parameters list")
            oLog.warning("Exception: " + str(e))
        return ldRet

    def _ldGetSwitchesAsDicts(self):
        """Return switches' information as a list of dicts"""
        ldRet = []
        try:
            ldRet = self.oSwitches._ldGetData()
        except Exception as e:
            oLog.warning("Exception when filling a switch parameters list")
            oLog.warning("Exception: " + str(e))
        return ldRet

    def _ldGetDIMMsAsDicts(self):
        """Return DIMMs' information as a list of dicts"""
        ldRet = []
        try:
            ldRet = self.oDIMMs._ldGetData()
        except Exception as e:
            oLog.warning("Exception when filling DIMMs parameters list")
            oLog.warning("Exception: " + str(e))
        return ldRet

    def _ldGetUPSesAsDicts(self):
        """Return upses' information as a list of dicts"""
        ldRet = []
        try:
            ldRet = self.oUPSs._ldGetData()
        except Exception as e:
            oLog.warning("Exception when filling an UPSes parameters list")
            oLog.warning("Exception: " + str(e))
        return ldRet


class XIV_Componens_Collection:
    def __init__(self, oSystem, sCommandLine):
        self.lComponentIDs = []
        self.dComponents = {}
        self.oSystem = oSystem
        self.lData = oSystem._lsRunCommand(sCommandLine)
        if self.lData:
            oLog.debug("XIV_Componens_Collection constructor: command output is " + str(self.lData))
            self.oCSV = csv.DictReader(self.lData,  delimiter=',', quotechar='"')
        else:
            raise XIVError("No output from a command")
        return

    def __repr__(self):
        """for debug printing"""
        return ("\n" + "====== List of components: ======" + '\n' +
                "\n".join([d.__repr__() for d in self.dComponents.values()]))

    def _lsListNames(self):
        """return a copy of Component IDs list"""
        return list(self.lComponentIDs)

    def _ldGetData(self):
        """return collection's data as a list of dictionaries"""
        ldRet = []
        for oObj in self.dComponents.values():
            ldRet.append(oObj._dGetDataAsDict())
        return ldRet

    def _iLength(self):
        """# of elements in the collection"""
        return len(self.lComponentIDs)


class IBM_XIV_DisksList(XIV_Componens_Collection):
    def __init__(self, oSystem):
        sCmd = 'disk_list -t component_id,capacity,size,model,serial'
        super().__init__(oSystem, sCmd)
        # parse the output
        for dDiskData in self.oCSV:
            sID = dDiskData["Component ID"]
            self.lComponentIDs.append(sID)
            oDisk = XIV_Disk(sID, dDiskData, oSystem)
            oSystem.oNodesList._AddDisk(sID, oDisk)
            self.dComponents[sID] = oDisk
        return


class IBM_XIV_NodesList(XIV_Componens_Collection):
    """ CAUTION: you must call this method first, before other *List constructors """
    def __init__(self, oSystem):
        # lsFields = ["component_id", "type", "disk_bay_count", "fc_port_count",
        #             "ethernet_port_count", "serial", "part_number, memory_gb"]
        # sFields = ",".join(lsFields)
        # sCmd = 'module_list -t ' + sFields
        sCmd = 'module_list -t all'
        super().__init__(oSystem, sCmd)
        for dNodeData in self.oCSV:
            oLog.debug("IBM_XIV_NodesList constructor: dNodeData: " + str(dNodeData))
            sID = dNodeData['Component ID']
            self.lComponentIDs.append(sID)
            self.dComponents[sID] = XIV_Node(sID, dNodeData)
        return

    def _AddDisk(self, sDiskID, oDisk):
        """Add a HDD to corresponding XIV node (module)"""
        iNodeNum = _iGetNodeNum(sDiskID)
        sNodeID = _sMkNodeID(iNodeNum)
        self.dComponents[sNodeID]._AddDisk(oDisk)
        return

    def _AddCF(self, sCF_ID, oCF):
        """Adds a Compact Flash device to a corresponding node"""
        sNodeID = _sMkNodeID(_iGetNodeNum(sCF_ID))
        self.dComponents[sNodeID]._AddCF(oCF)
        return

    def _AddNIC(self, sID, oNic):
        """Adds a NIC to a right node based on Component ID"""
        sNodeID = _sMkNodeID(_iGetNodeNum(sID))
        self.dComponents[sNodeID]._AddNIC(oNic)
        return

    def _AddFCPort(self, oFC):
        """Adds a FC port to a module that owns it"""
        sNodeID = oFC._sGetModID()
        self.dComponents[sNodeID]._AddFCPort(oFC)
        return

    def _AddDIMM(self, sID, oDimm):
        sNodeID = _sMkNodeID(_iGetNodeNum(sID))
        self.dComponents[sNodeID]._AddDIMM(oDimm)
        return

    def _AddPSU(self, sID, oPSU):
        sNodeID = _sMkNodeID(_iGetNodeNum(sID))
        self.dComponents[sNodeID]._AddPSU(oPSU)
        return


class IBM_XIV_CompactFlashList(XIV_Componens_Collection):
    def __init__(self, oSystem):
        sCmd = 'cf_list -t component_id,part_number,serial'
        super().__init__(oSystem, sCmd)
        for dCF_Data in self.oCSV:
            sID = dCF_Data['Component ID']
            self.lComponentIDs.append(sID)
            oCF = XIV_CompFlash(sID, dCF_Data['Part #'], dCF_Data['Serial'])
            self.dComponents[sID] = oCF
            oSystem.oNodesList._AddCF(sID, oCF)
        return


class IBM_XIV_SwitchesList(XIV_Componens_Collection):
    def __init__(self, oSystem):
        sCmd = 'switch_list -t component_id,serial'
        super().__init__(oSystem, sCmd)
        for dSwitchData in self.oCSV:
            sID = dSwitchData['Component ID']
            self.lComponentIDs.append(sID)
            self.dComponents[sID] = XIV_IB_Switch(sID, dSwitchData['Serial'])
        # oSystem.oSwitchesList = self
        return


class IBM_XIV_NICsList(XIV_Componens_Collection):
    def __init__(self, oSystem):
        sCmd = 'nic_list -t component_id,part_number,serial'
        super().__init__(oSystem, sCmd)
        for dNicData in self.oCSV:
            sID = dNicData['Component ID']
            self.lComponentIDs.append(sID)
            oNic = XIV_NIC(sID, dNicData['Part #'], dNicData['Serial'])
            oSystem.oNodesList._AddNIC(sID, oNic)
            self.dComponents[sID] = oNic
        return


class IBM_XIV_FCPortsList(XIV_Componens_Collection):
    def __init__(self, oSystem):
        sCmd = 'fc_port_list -t component_id,module,port_num,wwpn,model,original_serial'
        super().__init__(oSystem, sCmd)
        for dFCData in self.oCSV:
            sID = dFCData['Component ID']
            self.lComponentIDs.append(sID)
            oFCPort = XIV_FCPort(sID, dFCData)
            self.dComponents[sID] = oFCPort
            oSystem.oNodesList._AddFCPort(oFCPort)
        return


class IBM_XIV_DIMMSlist(XIV_Componens_Collection):
    def __init__(self, oSystem):
        sCmd = 'dimm_list -t component_id,size,part_number,serial'
        super().__init__(oSystem, sCmd)
        for dDIMMdata in self.oCSV:
            sID = dDIMMdata['Component ID']
            self.lComponentIDs.append(sID)
            oDIMM = XIV_DIMM(sID, dDIMMdata['Size(Mb)'], dDIMMdata['Part #'], dDIMMdata['Serial'])
            self.dComponents[sID] = oDIMM
            oSystem.oNodesList._AddDIMM(sID, oDIMM)
        return

    def _iTotalGBs(self):
        iTotalMBs = 0
        for oDimm in self.dComponents.values():
            iTotalMBs += oDimm.dQueries['size']()
        return (iTotalMBs / 1024)    # to gigabytes


class IBM_XIV_MaintenanceModulesList(XIV_Componens_Collection):
    def __init__(self, oSystem):
        sCmd = 'mm_list -t component_id,part_number,serial'
        super().__init__(oSystem, sCmd)
        for dMM_Data in self.oCSV:
            # most times, there will be only one loop
            sID = dMM_Data['Component ID']
            self.lComponentIDs.append(sID)
            self.dComponents[sID] = XIV_MaintenanceModule(sID, dMM_Data['Part #'], dMM_Data['Serial'])
        return


class IBM_XIV_PwrSuppliesList(XIV_Componens_Collection):
    def __init__(self, oSystem):
        sCmd = 'psu_list -t component_id'
        super().__init__(oSystem, sCmd)
        for dPSData in self.oCSV:
            sID = dPSData['Component ID']
            oPSU = XIV_PwrSupply(sID)
            self.dComponents[sID] = oPSU
            oSystem.oNodesList._AddPSU(sID, oPSU)
        return


class IBM_XIV_UPS_List(XIV_Componens_Collection):
    def __init__(self, oSystem):
        sCmd = 'ups_list -t component_id,serial,manufacture_date'
        super().__init__(oSystem, sCmd)
        for dUPSData in self.oCSV:
            sID = dUPSData['Component ID']
            self.lComponentIDs.append(sID)
            self.dComponents[sID] = XIV_UPS(sID, dUPSData['Serial'], dUPSData['UPS Manufacture Date'])
        return


#
# ============================== Components ==============================
#

class XIV_Component(inv.ComponentClass):
    def __init__(self, sID, sSN=""):
        self.sID = sID
        self.sSN = sSN
        self.dQueries = {}

    def _dGetDataAsDict(self):
        # name, type, model, etc
        dRet = {}
        for name, fun in self.dQueries.items():
            dRet[name] = fun()
        return dRet


class XIV_Node(XIV_Component):
    """XIV node"""
    def __init__(self, sId, dParams):
        """Node constructor. 2nd parameter is a dictionary of data: type, disk bays amount,
        FC ports amount, Ethernet ports amount, serial number, P/N"""
        super().__init__(sId, dParams['Serial'])
        self.sType = dParams['Type']
        self.sModel = dParams['Part Number']
        self.iDiskBaysCount = int(dParams['Data Disks'])
        self.iFCPorts = int(dParams['FC Ports'])
        self.iEthPorts = int(dParams['iSCSI Ports'])
        self.iRAM_GBs = int(dParams['Mem'])
        self.iPwrSupplies = 0
        self.lDisks = []
        self.lNICs = []
        self.lFCPorts = []
        self.lDimms = []
        self.lPSUs = []
        self.oCF = None
        self.dQueries = {"name":       lambda: self.sID,
                         "sn":         lambda: self.sSN,
                         "disks":      lambda: len(self.lDisks),
                         "disk-bays":  lambda: self.iDiskBaysCount,
                         "ps-amount":  lambda: self.iPwrSupplies,
                         # "disk-names": self._lsGetDiskNames,
                         "fc-ports":   lambda: self.iFCPorts,
                         "model":      lambda: self.sModel,
                         "type":       lambda: self.sType,
                         "eth-ports":  lambda: self.iEthPorts,
                         "memory":     lambda: self.iRAM_GBs
                         }
        return

    def __repr__(self):
        sCommon = "Node id: {:12s}, disks:{}, FC:{}, Eth:{}, S/N:{}, P/N:{}".format(
            self.sID, self.iDiskBaysCount, self.iFCPorts, self.iEthPorts, self.sSN, self.sModel)
        sDisks = "Disks list:\n" +  "\n".join([str(d) for d in self.lDisks])
        sDIMMS = "RAM:\n" + "\n".join([str(d) for d in self.lDimms])
        sPSUs = "Pwr Supplies:\n" + "\n".join([str(d) for d in self.lPSUs])
        sNICs = "Network Cards:\n" + "\n".join([str(n) for n in self.lNICs])
        if self.iFCPorts > 0:
            sFCs = "FC Ports:\n" + "\n".join([str(p) for p in self.lFCPorts])
        else:
            sFCs = ''
        sCF = "CF module: " + str(self.oCF)
        return("\n".join([sCommon, sDisks, sDIMMS, sNICs, sFCs, sPSUs, sCF]))

    def _iGetRAM(self):
        iRam = 0
        for oDimm in self.lDimms:
            iRam += oDimm._iGetRAM_MB()
        self.iRAM_MBs = iRam
        return iRam

    def _AddDisk(self, oDisk):
        self.lDisks.append(oDisk)
        return

    def _AddCF(self, oCompactFlash):
        self.oCF = oCompactFlash
        return

    def _AddNIC(self, oNic):
        self.lNICs.append(oNic)
        return

    def _AddFCPort(self, oPort):
        self.lFCPorts.append(oPort)
        return

    def _AddDIMM(self, oDimm):
        self.lDimms.append(oDimm)
        return

    def _AddPSU(self, oPSU):
        self.iPwrSupplies += 1
        self.lPSUs.append(oPSU)
        return

    def _lsGetDiskNames(self):
        """return a list of this node disks"""
        return [d.getID() for d in self.lDisks]


class XIV_Disk(XIV_Component):
    """Physical disk in XIV"""
    def __init__(self, sID, dParams, oNode):
        # self.sSN = dParams["Serial"]
        super().__init__(sID, dParams["Serial"])
        self.sID = sID
        self.iSizeMB = int(dParams['Size'])
        self.sSizeH = dParams['Capacity (GB)']
        self.sModel = dParams['Model']
        self.dQueries = {"name":  lambda: self.sID,
                         # "id":    lambda: self.sID,
                         "model": lambda: self.sModel,
                         "position": self._sGetPosition,
                         "size":  lambda: int(self.iSizeMB / 1024),
                         "sn":    lambda: self.sSN}
        oLog.debug('Disk ID: {}, sizeH: {}, sizeKB: {}'.format(self.sID, self.sSizeH, self.iSizeMB))
        return

    def _sGetPosition(self):
        """return disk position based on ID"""
        lFields = self.sID.split(':')
        return("Node {0}, bay {1}".format(lFields[2], lFields[3]))

    def __repr__(self):
        return "Drive: ID: {}, size:{}, mod:{}".format(self.sID, self.sSizeH, self.sModel)


class XIV_CompFlash(XIV_Component):
    """XIV CF device"""
    def __init__(self, sID, sPN, sSN):
        self.sID = sID
        # Node number is 3rd colon-separated field in the component ID
        self.sModel = sPN
        self.sSN = sSN
        self.dQueries = {"name":     lambda: self.sID,
                         "sn":       lambda: self.sSN,
                         "model":    lambda: self.sModel,
                         "position": self._sGetPos}
        return

    def _sGetPos(self):
        """return module number based on ID"""
        lFields = self.sID.split(':')
        return("Node {0}".format(lFields[2]))

    def __repr__(self):
        return "Compact Flash device, ID: {0:12s}, P/N:{1}, S/N:{2}".format(self.sID, self.sModel, self.sSN)


class XIV_NIC(XIV_Component):
    """XIV Network Interface Card (Ethernet)"""
    def __init__(self, sID, sPN, sSN):
        self.sID = sID
        self.sPN = sPN
        self.sSN = sSN
        return

    def __repr__(self):
        """for debugging"""
        return("NIC: ID {}, PN: {}, SN: {}".format(self.sID, self.sPN, self.sSN))


class XIV_MaintenanceModule(XIV_Component):
    """Maintenance module. I can't receive any information from XIV abt this module"""
    def __init__(self, sID, sPN, sSN):
        self.sID = sID
        self.sPN = sPN
        self.sSN = sSN
        return


class XIV_DIMM(XIV_Component):
    """DIMM module"""
    def __init__(self, sID, sSizeMB, sPN, sSN):
        self.sID = sID
        self.iSizeMB = int(sSizeMB)
        self.sSN = sSN
        self.sPN = sPN
        self.dQueries = {"name":     lambda: self.sID,
                         "sn":       lambda: self.sSN,
                         "size":     lambda: self.iSizeMB,
                         "model":    lambda: self.sPN,
                         "position": self._sGetPos}
        return

    def __repr__(self):
        return("RAM Module {3}: size:{0}, P/N:{1}, S/N:{2}".format(
            self.iSizeMB, self.sPN, self.sSN, self.sID))

    def _sGetPos(self):
        """return disk position based on ID"""
        lFields = self.sID.split(':')
        return("Node {0}, Slot {1}".format(lFields[2], lFields[3]))

    def _iGetRAM_MB(self):
        return self.iSizeMB


class XIV_PwrSupply(XIV_Component):
    def __init__(self, sID):
        self.sID = sID
        return

    def __repr__(self):
        return "Power supply, ID: {}".format(self.sID)


class XIV_UPS(XIV_Component):
    def __init__(self, sID, sSN, sMFDate):
        self.sSN = sSN
        self.sID = sID
        self.sMfgDate = sMFDate
        self.dQueries = {"name":    lambda: self.sID,
                         "sn":      lambda: self.sSN,
                         "mfgdate": lambda: self.sMfgDate}
        return


class XIV_FCPort(XIV_Component):
    def __init__(self, sID, dDataDict):
        self.sID = sID
        self.sModel = dDataDict['Model']
        self.sModID = dDataDict['Module']
        self.sWWN = dDataDict['WWPN']
        self.sPortNum = dDataDict['Port Number']
        self.sSN = dDataDict['Original Serial']
        return

    def _sGetModID(self):
        return self.sModID

    def __repr__(self):
        return ("FC port WWN:{0} on node {1}, model {2}, SN {3}".format(
            self.sWWN, self.sModID, self.sModel, self.sSN))


class XIV_IB_Switch(XIV_Component):
    def __init__(self, sID, sSerial):
        self.sID = sID
        self.sSN = sSerial
        self.dQueries = {"name":       lambda: self.sID,
                         "sn":         lambda: self.sSN}
        pass

    def __repr__(self):
        return "XIV IB Switch: ID:{0:-12s}, S/N:{1}".format(self.sID, self.sSN)


# ============================================
# Testing section
# --------------------------------------------
if __name__ == '__main__':
    # access to test system
    from access import IBM_XIV as tsys

    # print(str(oXiv.oNodesList))
    import redis
    oRedis = redis.StrictRedis()
    oXiv = IBM_XIV_Storage(tsys.sIP, tsys.sUser, tsys.sPass, oRedis, tsys.sName)
    print(oXiv.dQueries["node-names"]())
    print(oXiv.dQueries["switch-names"]())
    # print(oXiv._ldGetSwitchesAsDicts())
    print(oXiv.dQueries["ups-names"]())
    print(oXiv.dQueries["disk-names"]())
    pass
