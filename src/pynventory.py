#!/usr/bin/env python3

import paramiko
from binascii import hexlify
import socket
import yaml
import sys
import traceback

oData = yaml.load ("""
        name: gdgbook
        type: server
        access: ssh
        # IP: 192.168.135.133:ssh
        # IP:Port pairs must be quoted when in [] and CAN be unquoted in an itemized list
        IP: 
            - 192.168.34.2
            - 192.168.34.254:222
            - 192.168.135.133:2200
            - 127.0.0.1:2200
            - 192.168.40.66:2200
        sshMethod:
            login: dgolub
            ssh-key: /home/dgolub/.ssh/gdg@home.key
            KnownHostsFile: /home/dgolub/.ssh/known_hosts
        """)

print (oData)

class MySSHConnection:
    def __init__(self, ltIP_Port_Pairs:list, dssParams: dict):
        self.oSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bConnected = False
        iDefaultPort = 22
        for tIP_Port in ltIP_Port_Pairs:
            try:
                oSocket.connect(tIP_Port)
                bConnected=True
                break
            except Exception as e:
                pass
        if bConnected:
            try:
                self.oClient = paramiko.SSHClient()
                self.oClient.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
                self.oClient.load_system_host_keys()
                self.oClient.load_host_keys(dssParams['KnownHostsFile'])
                oClient.connect(hostname=sIP, port=iPort, username=dssParams['login'],
                key_filename=dssParams["ssh-key"],sock=oSocket)
            except Exception as e:
                pass
        return

    def close(self):
        try:
            self.oClient.close()
            self.oSocket.close()
        except Exception:
            pass
        return

    def fsRunCmd(self, sCmd: str) -> str:
        return ""

def ftSplitIPandPort(sIpPort: str) -> tuple:
    if ':' in sIpPort:
        sIP, sPort = sIpPort.split(":",1)
        if sPort.isnumeric(): iPort = int(sPort)
        else: iPort=22
    else:
        sIP = sIpPort
        iPort = 22
    return ((sIP, iPort))

oSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# connect with different IPs until success
bConnected=False
sIP = ''
iPort = 22
lsIPs = []
if isinstance(oData['IP'],str):
    lsIPs = [ oData['IP'] ]
elif isinstance(oData['IP'],list):
    lsIPs = oData['IP']
else:
    print("*DBG* Unknown type of oData['IP']: %s" % type(oData['IP']))
    raise Exception()

ltHostPortPairs = [ ftSplitIPandPort(s) for s in lsIPs ]
for tIpPort in ltHostPortPairs:
    try:
        # cut out the port number
        print("*DBG* Trying host %s, port %d" % tIpPort)
        oSocket.connect(tIpPort)
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
    oClient.load_host_keys(oData['sshMethod']['KnownHostsFile'])
    oClient.connect(hostname=sIP, port=iPort, username=oData['sshMethod']["login"],
            key_filename=oData['sshMethod']["ssh-key"],sock=oSocket)
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
