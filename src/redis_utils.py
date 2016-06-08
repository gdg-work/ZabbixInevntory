#!/usr/bin/env python3
# -*- coding: utf8 -*-
"""
Utilities for working with Redis in-memory database. Moved to separate module for
elimination of replicated code
"""

import redis
import string
import random
import logging
from pathlib import Path


oLog = logging.getLogger(__name__)


RANDOM_ID_CHARS = string.ascii_uppercase + string.ascii_lowercase + string.digits


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
