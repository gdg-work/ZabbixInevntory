#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A pair to discovery_info: scheduled/daemon module to get connection info from Redis,
fill in data fields and pass these fields to Zabbix via API
"""

import redis
import logging
import json
import argparse as ap
import hpeva_sssu as eva
import hp3Par
import MySSH
import ibm_FAStT as ibmds
import ibm_FlashSystem_SW as IbmFS
import ibm_XIV as xiv
import random
import string
import re
from inventoryLogger import dLoggingConfig
import zabbixInterface as zi
from pathlib import Path

# for debugging
import traceback

# ============================== CONSTANTS ==============================
REDIS_PREFIX =    "ArraysDiscovery."
ZBX_CONNECT_PFX = ""
QUERY_PFX =       ""
REDIS_ENCODING =  "UTF-8"
D_KEYS = {'ctrl-names':    'LIST_OF_CONTROLLER_NAMES',
          'shelf-names':   'LIST OF DISK ENCLOSURE NAMES',
          'disk-names':    'LIST OF DISK NAMES',
          "node-names":    'LIST OF NODE NAMES',
          "ups-names":     'LIST OF UPSes',
          "switch-names":  'LIST OF SWITCHES'}
RANDOM_ID_CHARS = string.ascii_uppercase + string.ascii_lowercase + string.digits
RE_DISK = re.compile(r'^Drive\s+')
RE_ENCLOSURE = re.compile(r'^DiskShelf\s+')
RE_CONTROLLER = re.compile(r'^Controller\s+')
RE_SYSTEM = re.compile(r'^System\s*$')


def _sRandomString(size=8, chars=RANDOM_ID_CHARS):
    return ''.join(random.choice(chars) for x in range(size))


def _sListOfStringsToJSON(lsStrings):
    """Converts list of strings to JSON data for Zabbix"""
    ID = '{#ID}'
    lRetList = [{ID: n} for n in lsStrings]
    dRetDict = {"data": lRetList}
    return json.dumps(dRetDict)


def _oConnect2Redis(sConnInfo):
    """
    connect to Redis DB.
    Parameters:
    sConnInfo: a string, one of 2 variants: 'host:port' or '/path/to/socket'
    returns: object of type redis:StrictRedis
    """
    bSocketConnect = False
    if sConnInfo[0] == '/' and Path(sConnInfo).is_socket():
        bSocketConnect = True
    elif sConnInfo.find(':') > 0 and sConnInfo.split(':', maxsplit=1)[1].isnumeric():
        sHost, sPort = sConnInfo.split(':', maxsplit=1)
        iPort = int(sPort)
    else:
        oLog.error("_oConnect2Redis: Invalid Redis connection parameters")
        oRedis = None
        raise redis.RedisError

    if bSocketConnect:
        oRedis = redis.StrictRedis(unix_socket_path=sConnInfo)
        oRedis.ping()
    else:
        oRedis = redis.StrictRedis(host=sHost, port=iPort)
        oRedis.ping()
    return oRedis


def _dGetZabbixConnectionInfo(oRedis):
    """Try to get Zabbix connection information from Redis database
    Parameter: Redis connection
    Returns: dictionary with Zabbix connection information. Dictionary keys:
    'zabbix_user', 'zabbix_passwd':, 'zabbix_IP':, 'zabbix_port'.
    if the data isn't found, returns {}
    """
    ZABBIX_PFX = REDIS_PREFIX + "ZabbixAccess"
    dRet = {}
    sJson = oRedis.get(ZABBIX_PFX)
    if sJson:
        dRet = json.loads(sJson.decode(REDIS_ENCODING))
    else:
        # no data in Redis
        oLog.info("No Zabbix connection data in Redis")
    return dRet


def _dGetArrayInfo(oRedis):
    """
    Try to get arrays connection information from Redis database
    Parameter: Redis connection
    Returns: a dictionary of dictionaries (one for each array).
    Dictionary key: array name
    Dictionary data: dict of {type, ip, access record}
    if the data isn't found, returns {}
    """
    ACCESS_PFX = REDIS_PREFIX + "ArrayAccess"
    lRet = {}
    lArrayInfoDictNames = [b.decode(REDIS_ENCODING) for b in oRedis.hkeys(ACCESS_PFX)]
    oLog.debug("*DBG* Arrays defined: {}".format(lArrayInfoDictNames))
    for sArrName in lArrayInfoDictNames:
        sJson = oRedis.hget(ACCESS_PFX, sArrName)
        if sJson:
            lRet[sArrName] = json.loads(sJson.decode(REDIS_ENCODING))
        else:
            # no data in Redis
            oLog.info("No arrays connection data in Redis")
    return lRet


def _oEvaConnect(dArrayInfo, oRedis):
    """Connect to HP EVA"""
    ip = dArrayInfo['ip']
    user = dArrayInfo['access']['user']
    password = dArrayInfo['access']['pass']
    sysname = dArrayInfo['access']['system']
    return eva.HP_EVA_Class(ip, user, password, sysname, oRedisConn=oRedis)


def _o3ParConnect(dArrayInfo, oRedis):
    """Connect to HP 3Par array"""
    ip = dArrayInfo['ip']
    user = dArrayInfo['access']['user']
    password = dArrayInfo['access']['pass']
    oAuth = MySSH.AuthData(user, bUseKey=False, sPasswd=password)
    sysname = dArrayInfo['access']['system']
    return hp3Par.HP3Par(ip, oAuth, sysname, oRedisConn=oRedis)


def _oIBM_DS_Connect(dArrayInfo):
    sIp = dArrayInfo['ip']
    return ibmds.IBM_DS(sIp)


def _oIBM_FlashSys_Connect(dArrayInfo, oRedis):
    ip = dArrayInfo['ip']
    user = dArrayInfo['access']['user']
    password = dArrayInfo['access']['pass']
    sysname = dArrayInfo['access']['system']
    oAuth = MySSH.AuthData(user, bUseKey=False, sPasswd=password)
    oLog.debug('creating IBM FlashSystem object for array {}'.format(sysname))
    return IbmFS.IBMFlashSystem(ip, oAuth, sysname, oRedis)


def _oIBM_XIV_Connect(dArrayInfo, oRedis):
    ip = dArrayInfo['ip'],
    user = dArrayInfo['access']['user']
    password = dArrayInfo['access']['pass']
    sysname = dArrayInfo['access']['system']
    oLog.debug('Creating XIV object for array {}'.format(ip))
    return xiv.IBM_XIV_Storage(ip, user, password, oRedis, sysname)


def _oConnect2Array(sArrayName, dArrayInfo, oRedis):
    """make a connection to array, returns array object"""
    oRet = None
    if dArrayInfo['type'] == 'EVA':
        oRet = _oEvaConnect(dArrayInfo, oRedis)
    elif dArrayInfo['type'] == '3Par':
        oRet = _o3ParConnect(dArrayInfo, oRedis)
    elif dArrayInfo['type'] == 'IBM_DS':
        oRet = _oIBM_DS_Connect(dArrayInfo)
    elif dArrayInfo['type'] == 'FlashSys':
        oRet = _oIBM_FlashSys_Connect(dArrayInfo, oRedis)
    elif dArrayInfo['type'] == 'XIV':
        oRet = _oIBM_XIV_Connect(dArrayInfo, oRedis)
    else:
        oLog.error('Array type {} is unsupported yet'.format(dArrayInfo['type']))
    return oRet


def _fPrepareZbxConnection(sFunctionName, sArrayName, dZbxInfo):
    return sFunctionName(sArrayName, dZbxInfo['zabbix_IP'],
                         dZbxInfo['zabbix_port'], dZbxInfo['zabbix_user'],
                         dZbxInfo['zabbix_passwd'])


def _lGetListOfDisks(sArrayName, oArray, dZbxInfo):
    oArZabCon = _fPrepareZbxConnection(zi.DisksToZabbix, sArrayName, dZbxInfo)
    lRet = oArray.dQueries['disk-names']()
    ldDisksInfo = oArray._ldGetDisksAsDicts()
    oArZabCon.__fillApplications__(RE_DISK)
    oArZabCon.sendDiskInfoToZabbix(sArrayName, ldDisksInfo)
    return lRet


def _lGetListOfControllers(sArrayName, oArray, dZbxInfo):
    oArZabCon = _fPrepareZbxConnection(zi.CtrlsToZabbix, sArrayName, dZbxInfo)
    lRet = oArray.dQueries['ctrl-names']()
    ldCtrlInfo = oArray._ldGetControllersInfoAsDict()
    oArZabCon.__fillApplications__(RE_CONTROLLER)
    oArZabCon._SendCtrlsToZabbix(sArrayName, ldCtrlInfo)
    return lRet


def _lGetListOfShelves(sArrayName, oArray, dZbxInfo):
    oArZabCon = _fPrepareZbxConnection(zi.EnclosureToZabbix, sArrayName, dZbxInfo)
    lRet = oArray.dQueries['shelf-names']()
    ldShelvesInfo = oArray._ldGetShelvesAsDicts()
    oLog.debug('_lGetListOfShelves: ldShelvesInfo = ' + str(ldShelvesInfo))
    oArZabCon.__fillApplications__(RE_ENCLOSURE)
    oArZabCon._SendEnclInfoToZabbix(sArrayName, ldShelvesInfo)
    return lRet


def _GetArrayParameters(sArrayName, oArray, dZbxInfo):
    ssItemsToRemove = set(['disk-names', 'ctrl-names', 'shelf-names', 'node-names'])
    oArZabCon = _fPrepareZbxConnection(zi.ArrayToZabbix, sArrayName, dZbxInfo)
    ssKeys = set(oArray.dQueries.keys())
    # make a difference of the sets
    ssKeys = ssKeys.difference(ssItemsToRemove)
    # oLog.debug('_GetArrayParameters: keys are: ' + str(ssKeys))
    dArrayInfo = oArray._dGetArrayInfoAsDict(ssKeys)
    oArZabCon.__fillApplications__(RE_SYSTEM)
    oArZabCon._SendArrayToZabbix(sArrayName, dArrayInfo)
    oLog.debug('_GetArrayParameters: Array info is {}'.format(str(dArrayInfo)))
    return


def _GetArrayData(sArrName, oArray, oRedis, dZbxParams):
    # XXX проверка на запрос узлов, UPS-ов и свитчей XXX
    sRedisArrInfoHashName = REDIS_PREFIX + "ArrayKeys"
    sArrayKey = REDIS_PREFIX + sArrName + "." + _sRandomString(8)
    oRedis.hset(sRedisArrInfoHashName, sArrName, sArrayKey)
    oLog.debug("Key {}, subkey {} is set to {}".format(sRedisArrInfoHashName, sArrName, sArrayKey))
    oRedis.expire(sRedisArrInfoHashName, oRedis.cacheTime)
    # create a new hash and set its expire time
    oRedis.hset(sArrayKey, 'NAME', sArrName)
    oRedis.expire(sArrayKey, oRedis.cacheTime)

    # get parameters describing a whole array and pass these parameters to Zabbix
    _GetArrayParameters(sArrName, oArray, dZbxParams)

    # get list of controllers and push it to Redis
    lCtrls = _lGetListOfControllers(sArrName, oArray, dZbxParams)
    oRedis.hset(sArrayKey, D_KEYS['ctrl-names'], _sListOfStringsToJSON(lCtrls))

    # get list of disk enclosures and push to Redis
    lEnclosures = _lGetListOfShelves(sArrName, oArray, dZbxParams)
    oRedis.hset(sArrayKey, D_KEYS['shelf-names'], _sListOfStringsToJSON(lEnclosures))

    # and finally list of disks
    lDisks = _lGetListOfDisks(sArrName, oArray, dZbxParams)
    oRedis.hset(sArrayKey, D_KEYS['disk-names'], _sListOfStringsToJSON(lDisks))
    # test data in Redis
    oLog.debug("Array hash name is {}".format(sArrayKey))
    for sKey in oRedis.hkeys(sArrayKey):
        oLog.debug('*DBG* stored key: {0}, value: {1}'.format(
                   sKey, oRedis.hget(sArrayKey, sKey)
                   ))
    return


def _ProcessArgs(oArgs, oLog):
    """ Process the CLI arguments and connect to Redis """
    oRedis = _oConnect2Redis(oArgs.redis)
    oRedis.cacheTime = oArgs.redis_ttl

    dZbxInfo = _dGetZabbixConnectionInfo(oRedis)
    dArrayInfo = _dGetArrayInfo(oRedis)
    for sArrName in dArrayInfo:
        dArrParams = dArrayInfo[sArrName]
        try:
            oArray = _oConnect2Array(sArrName, dArrParams, oRedis)
            _GetArrayData(sArrName, oArray, oRedis, dZbxInfo)
        except Exception as e:
            oLog.error('Exception when processing array: ' + sArrName)
            oLog.error(str(e))
            traceback.print_exc()
            continue
    return


def _oGetCLIParser():
    oParser = ap.ArgumentParser(description="Storage Array-Zabbix interface program")
    oParser.add_argument('-r', '--redis', help="Redis database host:port or socket, default=localhost:6379",
                         default='localhost:6379', type=str, required=False)
    oParser.add_argument('-t', '--redis-ttl', help="TTL of Redis-cached data", type=int,
                         default=900, required=False)
    return (oParser.parse_args())


if __name__ == "__main__":
    iRetCode = 0
    try:
        logging.config.dictConfig(dLoggingConfig)
        oLog = logging.getLogger('FeedData')
        oLog.info('Starting Zabbix-Feeder program')
        oParser = _oGetCLIParser()
        _ProcessArgs(oParser, oLog)
    except Exception as e:
        oLog.error("Fatal error: {}".format(str(e)))
        traceback.print_exc()
        iRetCode = 1
    oLog.info('Zabbix-Feeder: End of job')
    exit(iRetCode)
