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
import MySSH
import random
import string
# import re
import ibm_Power_AIX as aix
from inventoryLogger import dLoggingConfig
import zabbixInterface as zi
from pathlib import Path
from servers_discovery import SERVERS_SUPPORTED, OPERATIONS_SUPPORTED, REDIS_PREFIX
from local import REDIS_ENCODING

# for debugging
import traceback

# ============================== CONSTANTS ==============================

# ZBX_CONNECT_PFX = ""
# QUERY_PFX =       ""
D_KEYS = {'ctrl-names':   'LIST_OF_CONTROLLER_NAMES',
          'shelf-names':  'LIST OF DISK ENCLOSURE NAMES',
          'disk-names':   'LIST OF DISK NAMES',
          "node-names":   'LIST OF NODE NAMES',
          "ups-names":    'LIST OF UPSes',
          "dimm-names":   'LIST OF RAM MODULES',
          "cf-names":     'LIST OF COMPACT FLASH MODULES',
          "switch-names": 'LIST OF SWITCHES'}
RANDOM_ID_CHARS = string.ascii_uppercase + string.ascii_lowercase + string.digits

oLog = logging.getLogger(__name__)


def _sRandomString(size=8, chars=RANDOM_ID_CHARS):
    return ''.join(random.choice(chars) for x in range(size))


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


def _dGetServersInfo(oRedis):
    """
    Try to get server connection information from Redis database
    Parameter: Redis connection
    Returns: a dictionary of dictionaries (one for each server).
    Dictionary key: server name
    Dictionary data: dict of {type, ip, sp-ip, user, sp-user, pass, sp-pass, ...}
    not all fields are mandatory
    if the data isn't found, returns {}
    """
    ACCESS_PFX = REDIS_PREFIX + "ServersAccess"
    lRet = {}
    lInfoDictNames = [b.decode(REDIS_ENCODING) for b in oRedis.hkeys(ACCESS_PFX)]
    # oLog.debug("*DBG* Arrays defined: {}".format(lArrayInfoDictNames))
    for sSrvName in lInfoDictNames:
        sJson = oRedis.hget(ACCESS_PFX, sSrvName)
        if sJson:
            lRet[sSrvName] = json.loads(sJson.decode(REDIS_ENCODING))
        else:
            # no data in Redis
            oLog.info("No arrays connection data in Redis")
    return lRet


def _oCollectInfoFromServer(sSrvName, dSrvParams):
    oZbxHost = None
    sSrvType = dSrvParams['type']
    oLog.debug("_oCollectInfoFromServer called for server {}, type {}".format(dSrvParams['srv-ip'], sSrvType))
    if sSrvType == 'power_aix':
        oZbxHost = aix.PowerHostClass(sSrvName, IP=dSrvParams['srv-ip'],
                                      HMC_IP=dSrvParams['sp-ip'],
                                      User=dSrvParams['user'],
                                      Pass=dSrvParams['password'],
                                      SP_User=dSrvParams['sp-user'],
                                      SP_Pass=dSrvParams['sp-pass'],
                                      SP_Type=dSrvParams['sp-type']
                                      )
        print(oZbxHost)
        assert(dSrvParams['sp-type'] == 'HMC')
    else:
        oLog.error("Host type is not supported yet!")
    # connect to server, retrieve information from it
    # connect to HMC, retrieve information
    # make object and return it
    return oZbxHost


def _ProcessArgs(oArgs, oLog):
    """ Process the CLI arguments and connect to Redis """
    oRedis = _oConnect2Redis(oArgs.redis)
    oRedis.cacheTime = oArgs.redis_ttl

    dZbxInfo = _dGetZabbixConnectionInfo(oRedis)
    dServersInfo = _dGetServersInfo(oRedis)
    for sSrvName, dSrvParams in dServersInfo.items():
        try:
            # 'zabbix_user', 'zabbix_passwd':, 'zabbix_IP':, 'zabbix_port'
            oLog.info("Processing server {}".format(sSrvName))
            oServer = _oCollectInfoFromServer(sSrvName, dSrvParams)
            oZbxInterface = zi.Server_for_Zabbix(sSrvName, dZbxInfo['zabbix_IP'],
                                                 dZbxInfo['zabbix_port'],
                                                 dZbxInfo['zabbix_user'],
                                                 dZbxInfo['zabbix_passwd'])
            oZbxInterface._SendDataToZabbix(oServer)
        except Exception as e:
            oLog.error('Exception when processing array: ' + sSrvName)
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
        oLog = logging.getLogger('Servers_Feed_Data')
        oLog.info('Starting Servers information Feeder program')
        oParser = _oGetCLIParser()
        _ProcessArgs(oParser, oLog)
    except Exception as e:
        oLog.error("Fatal error: {}".format(str(e)))
        traceback.print_exc()
        iRetCode = 1
    oLog.info('Zabbix-Feeder: End of job')
    exit(iRetCode)
