#!/usr/bin/env python3

import itertools
from collections import OrderedDict
import zabbixInterface as zi


#
# NetObject: a base class of Zabbix-controlled objects
#
class NetObjectClass:
    def __init__(self, sIP: str, sType: str):
        self.sIP = sIP
        self.sType = sType

    def getType(self) ->str:
        return self.sType

    def getIP(self) ->str:
        return self.sIP


#
# A base class for storage systems
#
class StorageClass(NetObjectClass):
    def __init__(self, sIP: str, sType: str):
        super().__init__(sIP, sType)

    def getSN(self):
        return "Not implemented at super-class"


class ClassicArrayClass(StorageClass):
    """A classic disk array with 1..8 controllers, some amount of disk shelves and disks"""
    def __init__(self, sIP: str, sType: str):
        super().__init__(sIP, sType)
        self.lControllers = []    # list of ControllerClass
        self.lShelves = []  # list of 'DiskShelfClass'
        self.lDisks = []    # list of DiskClass
        self.dQueries = {          # possible queries
            "ctrls": self.getControllersAmount,
            "disks": self.getDisksAmount}

    def getControllersAmount(self):
        return len(self.lControllers)

    def getShelvesAmount(self):
        return len(self.lShelves)

    def getDisksAmount(self):
        return len(self.lDisks)

    def getControllersSN(self):
        """returns serial numbers of controllers"""
        lRet = []
        for oCtr in self.lControllers:
            lRet.append(oCtr.getSN())
        return lRet

    def getShelvesSN(self):
        """returns a list of disk shelves' serial numbers"""
        lRet = [s.getSN() for s in self.lShelves]
        return lRet

    def getDisksSN(self):
        """returns a list of disk drives' serial numbers"""
        lRet = [s.getSN() for s in self.lDisks]
        return lRet

    def _dGetArrayInfoAsDict(self, ssKeys):
        """
        Array-wide parameters as a dictionary.
        Parameter -- a set of keys/requests
        Returns: a dictionary {key:value}
        """
        dRet = {}
        for sKey in ssKeys:
            if sKey in self.dQueries:
                dRet[sKey] = self.dQueries[sKey]()
        return dRet


class ScaleOutStorageClass(StorageClass):
    def __init__(self, sIP: str, sType: str):
        super().__init__(sIP, sType)
        self.sType = ""
        self.lNodes = []

    def getType(self) ->str:
        return self.sType

    def getNodesAmount(self) ->int:
        return len(self.lNodes)

    def getDisksAmount(self) ->int:
        return sum([n.getDisksAmount() for n in self.lNodes])

    def getPortsAmount(self) ->int:
        return sum([n.getPortsAmount() for n in self.lNodes])

    def getDisksSN(self) -> list:
        lSNs = list(itertools.chain.from_iterable(    # flatten a list
            [n.getDisksSN for n in self.lNodes]))
        return lSNs


#
# --- Array and servers components ---
#
class ComponentClass:
    """ Common properties of storage system's components """
    def __init__(self, sID: str, sSN=""):
        self.sID = sID
        self.sSN = sSN
        self.oTriggers = None    # for Zabbix triggers
        self.dQueries = {"name": self.getID,
                         "sn":   self.getSN}

    def getID(self) -> str:
        return self.sID

    def getSN(self) -> str:
        return self.sSN

    def _dGetDataAsDict(self):
        # name, type, model, SN, position, RPM, size
        dRet = {}
        for name, fun in self.dQueries.items():
            dRet[name] = fun()
        return dRet

    def _ConnectTriggerFactory(self, oTriggersFactory):
        self.oTriggers = oTriggersFactory
        return


class ControllerClass(ComponentClass):
    """Classic array's disk controller"""
    def __init__(self, sID: str, sSN=""):
        super().__init__(sID, sSN)
        self.sType = ""
        self.sModel = ""
        self.lUpstreamPorts = ""
        self.sIP = ""

    def getType(self):
        return (self.sType)

    def getModel(self):
        return self.sModel

    def getPortsAmount(self):
        return len(self.lUpstreamPorts)

    def getPortIDs(self):
        lRet = [p.getID() for p in self.lUpstreamPorts]
        return lRet

    def getIP(self):
        return self.sIP


class DiskShelfClass(ComponentClass):
    def __init__(self, sID: str, sSN=""):
        super().__init__(sID, sSN)
        self.sType = ""
        self.sModel = ""
        self.lPwrSupplies = []
        self.lDisks = []

    def getType(self):
        return (self.sType)

    def getModel(self):
        return self.sModel

    def getPwrSupplyAmount(self):
        return len(self.lPwrSupplies)

    def getDisksAmount(self):
        return len(self.lDisks)

    def getDisksVolume(self):
        return sum([d.getSize() for d in self.lDisks])


class DASD_Class(ComponentClass):
    def __init__(self, sID: str, sSN=""):
        super().__init__(sID, sSN)
        self.sType = ""
        self.sModel = ""
        self.rSize = ""   # disk size in GB's
        self.sPosition = ""

    def getType(self) ->str:
        return self.sType

    def getModel(self) ->str:
        return self.sModel

    def getSize(self) ->int:
        return self.iSize

    def getPosition(self) ->str:
        return self.sPosition


class PortClass(ComponentClass):
    def __init__(self, sID: str, sSN=""):
        super().__init__(sID, sSN)
        self.sType = ""
        self.sSpeed = ""

    def getType(self) ->str:
        return self.sType

    def getSpeed(self) ->str:
        return self.sSpeed


class NodeClass(ComponentClass):
    def __init__(self, sID: str, sSN=""):
        super().__init__(sID, sSN)
        self.sType = ""
        self.sModel = ""
        self.lPwrSupplies = []
        self.lPorts = []
        self.lDisks = []


#
#  -- servers classes --
#
class GenericServer:
    """General server class. Don't use it directly, use subclasses"""
    def __init__(self, sName, **dFields):
        """Class constructor. The one parameter is name"""
        self.sName =          sName
        self.sIP =            dFields.get('IP')
        self.sModel =         dFields.get('Model')
        self.sType =          dFields.get('Type')
        self.sSerialNum =     dFields.get('SN')
        self.iSockets =       dFields.get('Sockets', 0)
        self.iCores =         dFields.get('Cores', 0)
        self.sProcType =      dFields.get('Processor')
        self.iPowerSupplies = dFields.get('PwrSupplyAmount')
        self.iRamGBs =        dFields.get('RAM GBs', 0)
        self.iRamModules =    dFields.get('DIMMS', 0)
        self.dQueries = {'name':        lambda: self.sName,
                         'model':       lambda: self.sModel,
                         'type':        lambda: self.sType,
                         'sn':          lambda: self.sSerialNum,
                         'sockets':     lambda: self.iSockets,
                         'cores':       lambda: self.iCores,
                         'proc-type':   lambda: self.sProcType,
                         'ps-amount':   lambda: self.iPowerSupplies,
                         'memory':      lambda: self.iRamGBs,
                         'dimms':       lambda: self.iRamModules}
        return

    def _dQueries(self):
        """returns a dictionary of possible queries"""
        return self.dQueries

    def __repr__(self):
        """for printing, debug and error messages etc"""
        return("Server name: {0}, type-model: {1}-{2}, SN: {3}".format(
               self.sName, self.sType, self.sModel, self.sSerialNum))

    def _Connect2Zabbix(self, oZbxAccess):
        self.oZbxAPI = oZbxAccess.api
        self.oZbxSender = oZbxAccess.sender
        self.oZbxHost = zi.ZabbixHost(self.sName, oZbxAccess)
        return



class ModularServer:
    """Modular servers like blade enclosures, HP Moonshot and Apollo servers"""
    def __init__(self, sName, **dFields):
        '''Constructor. One mandatory parameter is name, other are optional'''
        self.sName = sName
        self.sControlIP = dFields['IP']
        self.sType = dFields['Type']
        self.sModel = dFields['Model']
        self.sSerialNum = dFields['SN']
        self.oServers = ServersList([])
        self.oPowerSupplies = PowerSuppliesList([])
        self.oFans = FansList([])
        self.oInterconnects = InterConnectsList([])
        self.lMgmtModules = []
        self.dQueries = {'name':    lambda: self.sName,
                         'ip':      lambda: self.sControlIP,
                         'type':    lambda: self.sType,
                         'model':   lambda: self.sModel,
                         'sn':      lambda: self.sSerialNum,
                         'n-srvs': self.oServers._iGetAmount,
                         'srv-names': self.oServers._lGetNames,
                         'srv-params': self.oServers._ldGetParams,
                         }
        return

    def _AddComputingNode(self, oServer):
        self.oServers._add(oServer)

    def _AddPwerSupply(self, oPS):
        self.oPowerSupplies._add(oPS)


class Components_Collection(OrderedDict):
    def __init__(self, oContainer=None):
        if oContainer:
            super().__init__(oContainer)
        else:
            super().__init__()
        return

    def __repr__(self):
        """for debug printing"""
        return ("\n" + "====== List of components: ======" + '\n' +
                "\n".join([d.__repr__() for d in self.values()]))

    def _append(self, oObj):
        self[oObj._sGetName()] = oObj
        return

    def _lsListNames(self):
        """return a copy of Component IDs list"""
        return list(self.keys())

    def _ldGetData(self):
        """return collection's data as a list of dictionaries"""
        ldRet = []
        for oObj in self.values():
            ldRet.append(oObj._dGetDataAsDict())
        return ldRet


class ServersList(Components_Collection):
    def __init__(self, lObjects):
        if not (lObjects is None):
            self.lServers = lObjects
            self.dServers = super().__init__([(s._sGetName, s) for s in lObjects])
        else:
            super().__init__()
        return


class AdaptersList(Components_Collection):
    def __init__(self, lObjects=None):
        if lObjects:
            super().__init__(lObjects)
        else:
            super().__init__()
        return


class PowerSuppliesList(Components_Collection):
    def __init__(self, lObjects=None):
        super().__init__(lObjects)
        return


class FansList(Components_Collection):
    def __init__(self, lObjects=None):
        return


class InterConnectsList(Components_Collection):
    def __init__(self, lObjects=None):
        return


class DisksList(Components_Collection):
    def __init__(self, lObjects=None):
        return
