#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A pair to discovery_info: scheduled/daemon module to get connection info from Redis,
fill in data fields and pass these fields to Zabbix via API
"""

# import redis
import logging
import json
import argparse as ap
# import random
# import string
# === host types ===
import ibm_Power_AIX as aix
import ibm_BladeCenter_AMM as amm
import ESXi_WBEMM_host as esxi
# --- end of host types
from inventoryLogger import dLoggingConfig
# from pathlib import Path
# from servers_discovery import SERVERS_SUPPORTED, OPERATIONS_SUPPORTED, REDIS_PREFIX
from servers_discovery import REDIS_PREFIX
from local import REDIS_ENCODING
from pyzabbix.api import ZabbixAPI          # ZabbixAPIException
from pyzabbix.sender import ZabbixSender    # ZabbixMetric
from redis_utils import _oConnect2Redis

# for debugging
import traceback

# ============================== CONSTANTS ==============================

# ZBX_CONNECT_PFX = ""
# QUERY_PFX =       ""

oLog = logging.getLogger(__name__)


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


def _CollectInfoFromServer(sSrvName, dSrvParams, oZbxAPI, oZbxSender):
    oZbxHost = None
    sSrvType = dSrvParams['type']
    oLog.debug("_oCollectInfoFromServer called for server {}, type {}".format(dSrvParams['srv-ip'], sSrvType))
    if sSrvType == 'aix_hmc':
        assert(dSrvParams['sp-type'] == 'HMC')
        oZbxHost = aix.PowerHostClass(sSrvName, IP=dSrvParams['srv-ip'],
                                      HMC_IP=dSrvParams['sp-ip'],
                                      User=dSrvParams['user'],
                                      Pass=dSrvParams['password'],
                                      SP_User=dSrvParams['sp-user'],
                                      SP_Pass=dSrvParams['sp-pass'],
                                      SP_Type=dSrvParams['sp-type']
                                      )
        # print(oZbxHost)
    elif sSrvType == "esxi_amm":
        assert(dSrvParams['sp-type'] == 'AMM')
        oZbxHost = amm.ESXiWithAMM(sSrvName, IP=dSrvParams['srv-ip'],
                                   User=dSrvParams['user'],
                                   Pass=dSrvParams['password'],
                                   vCenter=dSrvParams['vcenter'],
                                   AMM_IP=dSrvParams['sp-ip'],
                                   SP_User=dSrvParams['sp-user'],
                                   SP_Pass=dSrvParams['sp-pass'],
                                   SP_Type=dSrvParams['sp-type']
                                   )
        print(oZbxHost)
    elif sSrvType == "esxi":
        oZbxHost = esxi.ESXi_WBEM_Host(
            sFQDN=sSrvName,
            sUser=dSrvParams['user'],
            sPass=dSrvParams['password'],
            sVCenter=dSrvParams['vcenter'],
            IP=dSrvParams['srv-ip'])
        print(oZbxHost)
    else:
        oLog.error("Host type is not supported yet!")
    # connect to server, retrieve information from it
    oZbxHost._Connect2Zabbix(oZbxAPI, oZbxSender)
    oZbxHost._MakeAppsItems()
    return oZbxHost


def _ProcessArgs(oArgs, oLog):
    """ Process the CLI arguments and connect to Redis """
    oRedis = _oConnect2Redis(oArgs.redis)
    oRedis.cacheTime = oArgs.redis_ttl

    dZbxInfo = _dGetZabbixConnectionInfo(oRedis)
    dServersInfo = _dGetServersInfo(oRedis)
    sZbxURL = "http://{}/zabbix/".format(dZbxInfo['zabbix_IP'])
    oZbxAPI = ZabbixAPI(url=sZbxURL, user=dZbxInfo['zabbix_user'], password=dZbxInfo['zabbix_passwd'])
    oZbxSender = ZabbixSender(zabbix_server=dZbxInfo['zabbix_IP'], zabbix_port=dZbxInfo['zabbix_port'])
    for sSrvName, dSrvParams in dServersInfo.items():
        try:
            # 'zabbix_user', 'zabbix_passwd':, 'zabbix_IP':, 'zabbix_port'
            oLog.info("Processing server {}".format(sSrvName))
            _CollectInfoFromServer(sSrvName, dSrvParams, oZbxAPI, oZbxSender)
            # oZbxInterface._SendDataToZabbix(oServer)
        except Exception as e:
            oLog.error('Exception when processing server: ' + sSrvName)
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
