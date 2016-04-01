#!/usr/bin/env python3
""" main module of the storage system Zabbix inventory program """

import argparse as ap
import logging
import json
from redis import StrictRedis,RedisError
from pathlib import Path
# array-dependent modules
import hpeva_sssu as eva
from inventoryLogger import dLoggingConfig

logging.config.dictConfig(dLoggingConfig)
oLog = logging.getLogger(__name__)


COMPONENT_OPS = set([  # Valid operations of array's components
    "sn",
    "name",
    "type",
    "model",
    "cpu-cores",
    "disk-names",
    "ports",
    "disk-slots",
    "port-names",
    "disks",
    "disk-rpm",
    "disk-size",
    "disk-pos",
    "ps-amount",
])

STORAGE_OPS = set([   # Valid operations of storage arrays
    "sn",
    "wwn",
    "type",
    "model",
    "ctrls",
    "shelves",
    "disks",
    "ctrl-names",
    "shelf-names",
    "disk-names",
    "ps-amount",
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
    elif sConnInfo.find(':') >0 and sConnInfo.split(':',maxsplit=1)[1].isnumeric():
        sHost,sPort = sConnInfo.split(':',maxsplit=1)
        iPort = int(sPort)
    else:
        oLog.error("_oConnect2Redis: Invalid Redis connection parameters")
        sRedisConn=""
        raise RedisError

    if bSocketConnect:
        oRedis = StrictRedis(unix_socket_path=sConnInfo)
        oRedis.ping()
    else:
        oRedis = StrictRedis(host=sHost, port=iPort)
        oRedis.ping()
    return oRedis



def _sGetComponentInfo(oStorageObject, sComponentName: str, sQuery:str):
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
    return eva.HP_EVA_Class(ip, user, password, sysname, oRedisConn = oRedis)


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
    print (sResult)
    exit(0)

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4 : autoindent
