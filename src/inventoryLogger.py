#!/usr/bin/env python3
import sys
import logging
import logging.config
import yaml

#
# Logging configuration. You can modify this code as you need
dLoggingConfig = yaml.load("""
    version: 1
    formatters:
        simple:
            format: '%(asctime)s: %(name)s - %(levelname)s - %(message)s'
        brief:
            format: '%(name)s:  %(levelname)s - %(message)s'
    handlers:
      console:
        class : logging.StreamHandler
        formatter: brief
        level   : WARNING
        stream  : ext://sys.stderr
      logfile:
        class : logging.handlers.RotatingFileHandler
        formatter: simple
        encoding: utf8
        level: DEBUG
        filename: /tmp/New-zabinventory.log
        # Max log file size: 1 MB, then the file will be rotated
        maxBytes: 1048576
        backupCount: 1
    root:
        level: INFO
    loggers:
        __main__:
            level: INFO
            handlers: [ console, logfile ]
        hp3Par:
            level: INFO
            handlers: [ console, logfile ]
        hpeva_sssu:
            level: DEBUG
            handlers: [ console, logfile ]
        ibm_FlashSystem_SW:
            level: INFO
            handlers: [ console, logfile ]
        ibm_FAStT:
            level: INFO
            handlers: [ console, logfile ]
        zabbixInterface:
            level: DEBUG
            handlers: [ console, logfile ]
        Discovery:
            level: INFO
            handlers: [ console, logfile ]
        srv.Discovery:
            level: DEBUG
            handlers: [ console, logfile ]
        Servers_Feed_Data:
            level: DEBUG
            handlers: [ console, logfile ]
        ibm_Power_AIX:
            level: INFO
            handlers: [ console, logfile ]
        ibm_BladeCenter_AMM:
            level: INFO
            handlers: [ console, logfile ]
        FeedData:
            level: INFO
            handlers: [ console, logfile ]
        MySSH:
            level: INFO
            handlers: [ console, logfile ]
        WBEM_vmware:
            level: INFO
            hadnlers: [console, logfile ]
        ESXi_WBEM_host:
            level: DEBUG
            hadnlers: [console, logfile ]
    """)


if __name__ == "__main__":
    print("This is a library, not an executable!")
    # test me
    logging.config.dictConfig(dLoggingConfig)

    oLog = logging.getLogger(__name__)
    oLog.error('Error Msg')
    oLog.info('Information 1')
    oLog.info('Information 2')
    oLog.debug('Debug info 1')
    oLog.debug('Debug info 2')
    oLog.debug('Debug info 3')
    oLog.debug('Debug info 4')
    sys.exit(-1)

# vim: expandtab:tabstop=4:softtabstop=4:shiftwidth=4
