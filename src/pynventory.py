#!/usr/bin/env python3

import paramiko
from binascii import hexlify
import socket
import yaml
import sys
import traceback

oData = yaml.load ("""
        name: gdgbook;
        type: server
        access: ssh
        port: 2200
        IP: [ 127.0.0.1, 192.168.40.66 ]
        login: dgolub
        ssh-key: /home/dgolub/.ssh/gdg@home.key
        """)

print (oData)


oConn = None
oSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    oSocket.connect((oData['IP'][0], oData['port']))
except Exception as e:
    print('*CRIT* Connect failed: ' + str(e))
    # traceback.print_exc()
    sys.exit(1)

sIp = oData['IP'][0]
print ("*DBG* IP addr is %s" % sIp)
try:
    oClient = paramiko.SSHClient()
    oClient.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
    oClient.load_system_host_keys()
    oClient.load_host_keys("/home/dgolub/.ssh/known_hosts")
    oClient.connect(sIp, oData["port"], username=oData["login"],
            key_filename=oData["ssh-key"],sock=oSocket)
    stdin, stdout, stderr = oClient.exec_command('ls -l')
    for sLine in stdout:
        print (sLine.strip())
    oClient.close()

except Exception as e:
    print('*** Caught exception: ' + str(e.__class__) + ': ' + str(e))
    traceback.print_exc()
    try:
        oClient.close()
    except:
        pass
    sys.exit(1)
