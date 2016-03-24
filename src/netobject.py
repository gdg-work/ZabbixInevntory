#!/usr/bin/env python3

#
# NetObject: a base class of Zabbix-controlled objects
#

class NetObjectClass:
    def __init__(self, sIP :str, sType :str):
        self.sIP = sIP
        self.sType = sType

    def getType(self) ->str: return self.sType

    def getIP(self) ->str: return self.getIP


class StorageClass(NetObjectClass):
    def __init__(self, sIP: str, sType: str):
        # XXX Здесь не правильный вызов метода суперкласса
        NetObjectClass.__init__(sIP, sType)

    def getSN(self):
        return "Not implemented at super-class"
