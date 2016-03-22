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
        IP: [ 192.168.34.2, 192.168.135.133, 127.0.0.1, 192.168.40.66 ]
        # IP: 192.168.135.133
        login: dgolub
        ssh-key: /home/dgolub/.ssh/gdg@home.key
        """)

print (oData)

class MySSHConnection:
    def __init__(self, lIPList, ):
        pass

    def close(self):
        pass

    def fsRunCmd(self, sCmd: str) -> str:
        return ""



oSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# connect with different IPs until success
bConnected=False
sIP = ''
lsIPs = []
if isinstance(oData['IP'],str):
    lsIPs = [ oData['IP'] ]
elif isinstance(oData['IP'],list):
    lsIPs = oData['IP']
else:
    print("*DBG* Unknown type of oData['IP']: %s" % type(oData['IP']))
    raise Exception()

for sIP in lsIPs:
    try:
        print("*DBG* Trying IP: %s" % sIP)
        oSocket.connect((sIP, oData['port']))
        bConnected=True
        break
    except Exception as e:
        pass
# check if any connection was successful
if not bConnected:
    traceback.print_exc()
    sys.exit(1)

try:
    oClient = paramiko.SSHClient()
    oClient.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
    oClient.load_system_host_keys()
    oClient.load_host_keys("/home/dgolub/.ssh/known_hosts")
    oClient.connect(hostname=oData['name'], port=oData['port'], username=oData["login"],
            key_filename=oData["ssh-key"],sock=oSocket)
    stdin, stdout, stderr = oClient.exec_command('ls -l')
    for sLine in stdout:
        print (sLine.strip())
    stdin, stdout, stderr = oClient.exec_command('hostname')
    for sLine in stdout:
        print (sLine.strip())
    oClient.close()

except Exception as e:
    print('*CRIT* Caught exception: ' + str(e.__class__) + ': ' + str(e))
    traceback.print_exc()
    try:
        oClient.close()
    except:
        pass
    sys.exit(1)

# vim: expandtab:tabstop=4:softtabstop=4
