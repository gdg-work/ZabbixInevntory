#!/usr/bin/env python3
# -*- coding: utf-8 -*-

""" Prints to STDOUT list of servers in JSON format used by Zabbix """
import logging
import argparse as ap
from inventoryLogger import dLoggingConfig
from zabbixInterface import _sListOfStringsToJSON
from redis import StrictRedis, RedisError
import ibm_Power_AIX as aix
import json
from local import REDIS_ENCODING

# Constants
SERVERS_SUPPORTED = ['power_aix']
OPERATIONS_SUPPORTED = ['server-name']
REDIS_PREFIX = "ServersDiscovery."

dObjects = {'power_aix': aix.PowerHostClass}


class IncorrectServerType(Exception):
    pass


def _sProcessArgs(oParser, oRedis):
    sRet = _sListOfStringsToJSON([oParser.name])
    _PushConnectionInfo(oParser, oRedis)
    return(sRet)


def _sGetServerData(oRedis, oArgs):
    SERVER_HASHNAME = REDIS_PREFIX + "ServerKeys"
    sRet = ''
    try:
        sServerKey = oRedis.hget(SERVER_HASHNAME, oArgs.system)
        sJson = oRedis.hget(sServerKey, oArgs.query).decode(REDIS_ENCODING)
        # print("*DBG* JSON from Redis: {}".format(sJson))
        if sJson == "None":
            pass
        elif sJson is None:
            pass        # <-- Will we be here anyhow?
        else:
            oLog.debug('JSon from Redis: {}'.format(sJson))
            sRet = sJson
    except AttributeError:
        # no data in Redis, nothing to decode
        pass
    except TypeError:
        # no data in Redis
        pass
    except Exception as e:
        oLog.error(str(e))
        pass
    return sRet


def _PushConnectionInfo(oParser, oRedis):
    ACCESS_PFX = REDIS_PREFIX + "ServersAccess"
    ZABBIX_PFX = REDIS_PREFIX + "ZabbixAccess"
    dZabbixAccess = {'zabbix_user': oParser.zabbixuser,
                     'zabbix_passwd': oParser.zabbixpassword,
                     'zabbix_IP': oParser.zabbixip,
                     'zabbix_port': oParser.zabbixport}
    if oParser.type == "power_aix":
        dConnectionInfo = {'type': 'power_aix',
                           'sp-type': 'HMC',
                           'user': oParser.user,
                           'password': oParser.password,
                           'sp-user': oParser.sp_user,
                           'sp-pass': oParser.sp_password,
                           'srv-ip': oParser.server_ip,
                           'sp-ip': oParser.sp_ip}
        # oServer = aix.PowerHostClass(dConnectionInfo)
    else:
        oLog.error('Unsupported type of server')
        raise(IncorrectServerType)
    oLog.error('Prefix for Zabbix info: "{}"'.format(ZABBIX_PFX))
    oLog.error('Prefix for servers access: "{}"'.format(ACCESS_PFX))
    try:
        oRedis.set(ZABBIX_PFX, json.dumps(dZabbixAccess), oParser.redis_ttl)
        oRedis.hset(ACCESS_PFX, oParser.name, json.dumps(dConnectionInfo))
        oRedis.expire(ACCESS_PFX, oParser.redis_ttl)
    except RedisError:
        oLog.error('Cannot connect to Redis and set information')
        raise RedisError
    return


def _oGetCLIParser():
    """parse CLI arguments, returns argparse.ArgumentParser object"""
    oParser = ap.ArgumentParser(description="Make servers list for Zabbix")
    oParser.add_argument('-t', '--type', help="Server type", required=True,
                         choices=SERVERS_SUPPORTED)
    oParser.add_argument('-q', '--query', choices=OPERATIONS_SUPPORTED, default="server-name")
    oParser.add_argument('-n', '--name', help='Server name (required for AIX)')

    oParser.add_argument('-i', '--server-ip', help="Server interface IP or FQDN", type=str, required=True)
    oParser.add_argument('-I', '--sp-ip', help="Service processor (iLO, HMC, IMM) interface IP or FQDN",
                         type=str, required=False)
    oParser.add_argument('-u', '--user', help="Host login", type=str, required=True)
    oParser.add_argument('-p', '--password', help="Host password", type=str, required=False)
    oParser.add_argument('-U', '--sp-user', help="Service processor login", type=str, required=False)
    oParser.add_argument('-P', '--sp-password', help="Service processor password", type=str, required=False)
    oParser.add_argument('-k', '--key', help="SSH key to authenticate to host", type=str, required=False)
    oParser.add_argument('-K', '--sp-key', help="SSH key to authenticate to SP", type=str, required=False)
    oParser.add_argument('-r', '--redis', help="Redis database host:port or socket, default=localhost:6379",
                         default='localhost:6379', type=str, required=False)
    oParser.add_argument('--redis-ttl', help="TTL of Redis-cached data", type=int,
                         default=900, required=False)
    oParser.add_argument('-z', '--zabbixip', help="IP of Zabbix server", type=str,
                         default='127.0.0.1', required=False)
    oParser.add_argument('--zabbixport', help="Port for sending data to Zabbix server",
                         type=int, default=10051, required=False)
    oParser.add_argument('--zabbixuser', help="Zabbix server user name",
                         default='Admin', required=False)
    oParser.add_argument('--zabbixpassword', help="Zabbix server password",
                         default='zabbix', required=False)
    return (oParser.parse_args())


#
# == main ==
#
if __name__ == '__main__':
    logging.config.dictConfig(dLoggingConfig)
    oLog = logging.getLogger('Srv.Discovery')
    oLog.debug('Starting Discovery-info program')
    # oLog.debug(" ".join(sys.argv))
    oParser = _oGetCLIParser()
    sRet = "Not implemented yet"
    iRetCode = -1
    try:
        oRedis = StrictRedis()
        sRet = _sProcessArgs(oParser, oRedis)
        print(sRet)
        iRetCode = 0
    except RedisError:
        oLog.error('Cannot connect to Redis DB')
        iErrCode = 2
#     except Exception as e:
#         oLog.error("Exception at top-level {}".format(str(e)))
#         iRetCode = 1
    exit(iRetCode)

# vim: expandtab : softtabstop=4 : tabstop=4 : shiftwidth=4 : autoindent
