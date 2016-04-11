#!/usr/bin/env python3
""" main module of the storage system Zabbix inventory program """

import argparse as ap
import logging
import json
from redis import StrictRedis, RedisError
from pathlib import Path
# array-dependent modules
import hpeva_sssu as eva
from zabbixInterface import DisksToZabbix, ZabInterfaceException
from inventoryLogger import dLoggingConfig

logging.config.dictConfig(dLoggingConfig)
oLog = logging.getLogger(__name__)


COMPONENT_OPS = set([  # Valid operations of array's components
    "sn",            # serial number
    "name",          # name or other identifier
    "type",          # type, such as HSV300
    "model",         # vendor model, f.e. AG638A
    "cpu-cores",     # number of controller's CPU cores
    "disk-names",    # names of included disks (for disk shelves)
    "ports",         # number of ports
    "disk-slots",    # number of disk slots
    "port-names",    # names (list) of ports (for controllers)
    "disks",         # number of disks
    "disk-rpm",      # RPM speed (for disks)
    "disk-size",     # size (for disks)
    "disk-pos",      # Position (Shelf/slot) for disks
    "ps-amount",     # number of power supplies (for controllers & shelves)
])

STORAGE_OPS = set([   # Valid operations of storage arrays
    "sn",            # Serial number
    "wwn",           # WWN
    "type",          # type of array 
    "model",         # vendor model
    "ctrls",         # number of controllers
    "shelves",       # number of disk enclosures
    "disks",         # number of disks
    "ctrl-names",    # list of controllers' names
    "shelf-names",   # list of disk enclosures' names
    "disk-names",    # list of disks' names (ID's)
    "ps-amount",     # number of power supplies in controller enclosure
    ])

def _sListOfStringsToJSON(lsStrings: list):
    ID = '{#ID}'
    lRetList = [{ID: n} for n in lsStrings]
    dRetDict = {"data": lRetList}
    return json.dumps(dRetDict)


def _oConnect2Redis(sConnInfo: str):
    """connect to Redis DB. 
    Parameters: sConnInfo: a string, one of 2 variants: 'host:port' or '/path/to/socket'
    returns: object of type redis:StrictRedis"""
    bSocketConnect = False
    if sConnInfo[0] == '/' and Path(sConnInfo).is_socket():
        bSocketConnect = True
    elif sConnInfo.find(':') >0 and sConnInfo.split(':', maxsplit=1)[1].isnumeric():
        sHost, sPort = sConnInfo.split(':', maxsplit=1)
        iPort = int(sPort)
    else:
        oLog.error("_oConnect2Redis: Invalid Redis connection parameters")
        oRedis = None
        raise RedisError

    if bSocketConnect:
        oRedis = StrictRedis(unix_socket_path=sConnInfo)
        oRedis.ping()
    else:
        oRedis = StrictRedis(host=sHost, port=iPort)
        oRedis.ping()
    return oRedis


def _sGetComponentInfo(oStorageObject, sComponentName: str, sQuery: str):
    """Вызов метода компонента по имени через определённый в компоненте словарь"""
    sRet = "Not Implemented"
    oComponent = oStorageObject.getComponent(sComponentName)
    if oComponent:
        if sQuery in oComponent.dQueries:
            sRet = oComponent.dQueries[sQuery]()
        else:
            sRet = "N/A"
    else:
        sRet = "Error when querying a component"
    return (sRet)


def _sProcessArgs(oStorageObject, oArgs):
    """Apply query to a storage device """
    sRet = "N/A"
    oLog.debug("Request: {0}".format(oArgs.query))
    if oArgs.element:
        sRet = _sGetComponentInfo(oStorageObject, oArgs.element, oArgs.query)
    elif oArgs.query == 'disk-names':
        # XXX special case, we need to return disk names and then fill the disks info
        oRet = oStorageObject.dQueries[oArgs.query]()
        sRet = _sListOfStringsToJSON(oRet)
        # now make a call to array for returning disks information and send this info to an array
        ldDisksInfo = oStorageObject._ldGetDisksAsDicts()
        oLog.debug
        oLog.debug('Sending disks info to Zabbix by API')
        oArZabCon = DisksToZabbix(oArgs.system, oArgs.zabbixip, oArgs.zabbixport, 
                                  oArgs.zabbixuser, oArgs.zabbixpassword)
        oLog.debug('Zabbix connection {} initiated'.format(str(oArZabCon)))
        oArZabCon.__fillApplications__()
        oLog.debug('Applications info: {} filled'.format(str(oArZabCon.dApplicationNamesToIds)))
        oArZabCon.sendDiskInfoToZabbix(oArgs.system, ldDisksInfo)
        oLog.debug('Data sent to Zabbix')
    else:
        try:
            oRet = oStorageObject.dQueries[oArgs.query]()
            if isinstance(oRet, str):
                sRet = oRet
            elif isinstance(oRet, int):
                sRet = str(oRet)
            elif isinstance(oRet, list):
                sRet = _sListOfStringsToJSON(oRet)
            else:
                oLog.warning("Incorrect return type from storageObject method {}".format(oArgs.query))
        except AttributeError as e:
            oLog.error(e.args[0])
            oLog.info("Method {0} of storage device {1} isn't implemented".format(oArgs.query, oArgs.type))
            sRet = "N/A"
    return sRet


def _oEvaConnect(oArgs):
    """Connect to HP EVA"""
    ip = oArgs.control_ip
    user = oArgs.user
    password = oArgs.password
    sysname = oArgs.system
    oRedis = _oConnect2Redis(oArgs.redis)
    return eva.HP_EVA_Class(ip, user, password, sysname, oRedisConn=oRedis)


def _oGetCLIParser():
    """parse CLI arguments, returns argparse.ArgumentParser object"""
    lAllowedOps = list(COMPONENT_OPS.union(STORAGE_OPS))
    oParser = ap.ArgumentParser(description="Storage Array-Zabbix interface program")
    oParser.add_argument('-t', '--type', help="Storage device type", required=True,
                         choices=["EVA", "Storwize", "3Par", "XIV"])
    oParser.add_argument('-q', '--query', 
            help="Parameter to request. Not all combination of component and request are valid",
            choices=lAllowedOps, default="sn")
    oParser.add_argument('-e', '--element',
            help="Component of an array the query is making to, such as controller or disk shelf",
            type=str, required=False)
    oParser.add_argument('-c', '--control_ip', help="Array control IP or FQDN", type=str, required=True)
    oParser.add_argument('-u', '--user', help="Array/control host login", type=str, required=True)
    oParser.add_argument('-p', '--password', help="password", type=str, required=False)
    oParser.add_argument('--dummy', help="Dummy unique key (not used)", type=str, required=False)
    oParser.add_argument('-k', '--key', help="SSH private key to authenticate", type=str, required=False)
    oParser.add_argument('-s', '--system', help="HP EVA name in CV (EVA only)", type=str, required=False)
    oParser.add_argument('-r', '--redis', help="Redis database host:port or socket, default=localhost:6379", 
        default='localhost:6379', type=str, required=False)
    oParser.add_argument('--redis-ttl', help="TTL of Redis-cached data", type=int, default=900, required=False)
    oParser.add_argument('-z', '--zabbixip', help="IP of Zabbix server", type=str, default='127.0.0.1', required=False)
    oParser.add_argument('-S', '--zabbixport', help="Port for sending data to Zabbix server", 
                         type=int, default=10051, required=False)
    oParser.add_argument('-U', '--zabbixuser', help="Zabbix server user name", default='Admin', required=False)
    oParser.add_argument('-P', '--zabbixpassword', help="Zabbix server password", default='zabbix', required=False)
    return (oParser.parse_args())

if __name__ == '__main__':
    # set up logger
    oArgs = _oGetCLIParser()
    if oArgs.type == "EVA":
        oArrayConn = _oEvaConnect(oArgs)
    else:
        oLog.error("Not implemented yet!")
        exit(3)

    sResult = _sProcessArgs(oArrayConn, oArgs)
    oArrayConn._Close()
    print(sResult)
    exit(0)

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4 : autoindent
