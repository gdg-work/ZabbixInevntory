#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
import pexpect
import re
import os
import time

# -- Constants --
asVDisks = "\\Virtual Disks\\"   # Название "фолдера" с виртуальными дисками в CV/EVA
aiVDLen = len(asVDisks)          # Используется при сравнениях

class SSSU_Error(Exception):
    """Error when running SSSU"""
    def __init__(self,sStr):
        """Initialize the error with a message given as a string"""
        self.sErrMsg=sStr
        return

    def __str__(self):
        """Converts an error to string for printing"""
        return repr(self.sErrMsg)


class SSSU_Iface:
    def __init__(self, sSSSU_Path, sMgmtIP, sLogin, sPasswd, 
                 sWorkDir, sSystemName,  _Debug, _Error):
        """Инициализация класса. Параметры:
        sSSSU_Path = полное имя программы 'sssu'
        sMgmtIP    = IP адрес сервера управления (SMA)
        sLogin     = имя пользователя для входа на SMA
        sPasswd    = пароль для входа
        sWorkDir   = рабочий каталог программы (должен быть доступен на запись)
        sSystemName = имя машины (EVA) в Command View
        fDbg       = функция, записывающая в лог отладочное сообщение
        fError     = функция, записывающая в лог сообщение об ошибке.
        """
        self.SSSU = sSSSU_Path
        self._Dbg = _Debug
        self._Err = _Error
        self.sPrompt = sSystemName + ">"
        self._Dbg("Initializing SSSU connection")
        self.sSystemName = ''
        if os.path.isdir(sWorkDir):
            os.chdir(sWorkDir)
            try:
                self.pSSSU = pexpect.spawn(self.SSSU)
                self.pSSSU.expect("Manager:")
                self.pSSSU.send(sMgmtIP + "\n")
                self.pSSSU.expect("Username:")
                self.pSSSU.send(sLogin + "\n")
                self.pSSSU.expect("Password:")
                self.pSSSU.send(sPasswd + "\n")
                iIdx = self.pSSSU.expect(["NoSystemSelected>", "Error opening https connection"],5)
                if iIdx == 0:
                    # Залогинились успешно.
                    self._Dbg("Logged into SSSU")
                    self.pSSSU.send("LS SYSTEM\n")
                    self.pSSSU.expect("NoSystemSelected")
                    # В списке систем пропускаем первые два элемента, а из остальных строк удаляем
                    # пробелы и берём непустую часть
                    lSystemNames = [l.strip() for l in self.pSSSU.before.decode('utf-8').split("\r\n")[3:] 
                                    if l.strip() != ""]
                    self._Dbg("Systems available: " + ", ".join(lSystemNames))
                    if sSystemName in lSystemNames:
                        self.pSSSU.send("SELECT SYSTEM %s\n" % sSystemName) # good, continue our work
                        self.pSSSU.expect_exact(self.sPrompt)
                        # self.pSSSU.expect(r'\r\n[^<].*>\r\n')
                        # здесь ничего не должно случиться, мы уже проверили, что такое имя существует
                        self.pSSSU.send("SET OPTIONS on_error=Continue display_status noretries display_width=500\n")
                        self.pSSSU.expect("Status : 0")
                        self.pSSSU.expect_exact(self.sPrompt)
                        self.sSystemName = sSystemName
                        self._Dbg("Name of system and options are set")
                    else:
                        self._Err("The array name '%s' is unknown to Command View on the management server %s" %
                                  (sSystemName,sMgmtIP))
                        raise SSSU_Error("Unknown or invalid array name %s" % sSystemName)
                else:
                    # Не смогли создать соединение с Command View
                    self._Err("Cannot connect to Command View with credentials used")
                    raise SSSU_Error("Cannot log in Command View. Are the credentials valid?")
            except pexpect.TIMEOUT:
                raise SSSU_Error("__init__: Cannot connect to Command View (timeout)")
            except pexpect.EOF:
                raise SSSU_Error("__init__: Cannot read data from SSSU utility (EOF)")

        else:
            raise SSSU_Error("__init__: Invalid or unexistent working directory %s" % sWorkDir)
        return

    def _sGetSysName(self): return self.sSystemName

    def _sRunCommand(self, sCommand, sSeparator=""):
        """Runs a command on SSSU, returns the command's output as a string. Raises
        SSSU_Error on error conditions such as timeout or loss of connection"""
        self._Dbg("_sRunCommand called with command: '%s'" % sCommand)
        lReturn = []
        reDots = re.compile(r" \.+: ")
        try:
            self.pSSSU.send("\n")
            self.pSSSU.expect(self.sPrompt)
            self.pSSSU.send(sCommand + "\n")
            self.pSSSU.expect(sCommand)
            self.pSSSU.expect(self.sPrompt)
            lReturn = [s.strip() for s in self.pSSSU.before.decode('utf-8').split("\r\n")]
        except pexpect.TIMEOUT:
            raise SSSU_Error("_sRunCommand(): Connection to SSSU lost (timeout)")
        except pexpect.EOF:
            raise SSSU_Error("_sRunCommand(): Connection to SSSU lost (EOF while reading")
        return sSeparator.join(lReturn)

    def _dGetVDInfo(self, sVDisk, lsParameters):
        """Выдаёт информацию о VDisk-е (или другом объекте), которую возвращает команда SSSU "LS
        VDISK "<sName>" | grep "<sParameter>"', для всех заданных параметров.  Параметры: имя диска
        (проверяется его существование), список параметров.  Возвращает: словарь вида
        {имя_параметра:значение_параметра} Если такого параметра нет - в словаре не будет
        соответствующей пары.  Если VDisk не существует или не содержит ни одного из заданных
        параметров - возвращается пустой словарь
        """
        self._Dbg("_dGetVDInfo called with params: vDisk='%s', parameters: %s" % 
                  (sVDisk, ", ".join(lsParameters)))
        dReturn = {}
        reDots = re.compile(r" \.+: ")
        if self._bNameUsed(sVDisk):
            try:
                self.pSSSU.send("\n")
                self.pSSSU.expect(self.sPrompt)
                for sParam in lsParameters:
                    sCmd = 'ls vdisk "%s" | grep "%s"' % (sVDisk, sParam)
                    self.pSSSU.send(sCmd + "\n")
                    self.pSSSU.expect(sCmd)
                    self.pSSSU.expect(self.sPrompt)
                    sLines = [s.strip() for s in self.pSSSU.before.split("\r\n")]
                    for sParamLine in sLines:
                        if reDots.search(sParamLine):
                            sName, sValue = reDots.split(sParamLine)
                            dReturn[sName] = sValue
                        else:
                            pass
            except pexpect.TIMEOUT:
                raise SSSU_Error("_dGetVDInfo(): Connection to SSSU lost (timeout)")
            except pexpect.EOF:
                raise SSSU_Error("_dGetVDInfo(): Connection to SSSU lost (EOF while reading")
        else:
            self._Err("Object not found: <%s>" % sVDisk)
        return dReturn

#    def _lGetVDisksList(self):
#        """Возвращает список VDisk-ов, определённых на этом массиве. 
#        Вызывается из init-а и далее по требованию"""
#        asVdisksTitle = "Vdisks available on this Cell:"
#        self._Dbg("Searching array for VDisks")
#        self.pSSSU.send("LS VDISK\n")
#        iIdx = self.pSSSU.expect(["Status : 0",pexpect.EOF, pexpect.TIMEOUT])
#        if iIdx == 0:
#            lVDisks = [l.strip() for l in self.pSSSU.before.split("\r\n")]
#            # list element looks like: '\Virtual Disks\AZK_Cluster\AZK11\azk11_eva64c1_bindisk\ACTIVE'
#            # Предпоследний элемент - имя диска, если в последнем элементе ACTIVE - это обычный VDisk.
#            # WARNING: большое количество VDISK-ов со снэпшотами может переполнить буфер pexpect-а.
#            # search the list for first element:
#            if not asVdisksTitle in lVDisks[0:5]:
#                self._Err("Strange, cannot find vdisk list header")
#            # Удаляем из списка все строки, в которых второе поле (разделённое '\') не 'Virtual Disks'.
#            lShortNames = []
#            for sVdsk in lVDisks:
#                lFields = sVdsk.split('\\')
#                if len(lFields) > 2 and lFields[1] == "Virtual Disks":
#                    lShortNames.append(lFields[-2])
#                else:
#                    lVDisks.remove(sVdsk)
#        else:
#            self._Err("Cannot receive Vdisks list")
#        self.pSSSU.expect(self.sPrompt)
#        return lVDisks
#
#    def _RefreshVDisksList(self):
#        """ Обновляет спискок VDisk-ов """
#        self.lsVDisks_List = self._lGetVDisksList()
#        return
#
#    def _lGetShortVDNamesList(self):
#        """Возвращает список коротких имён дисков, которые образуются из длинных выделением 2-го
#        поля с конца"""
#        lShortNames = [ str.split('\\')[-2] for str in self.lsVDisks_List ]
#        return lShortNames

    def _sExpandObjectName(self,sName,sType):
        raise SSSU_Error("_sExpandObjectName: not implemented yet")
        return ""

#    def _bHaveVDisk(self, sVolName):
#        """Checks if the volume given as a parameter exists.
#        Parameters: volume name (full)
#        Returns: True if volume exists, False othervise"""
#        bRet = False
#        self._Dbg("Checking existence of volume <%s>" % sVolName)
#        self._RefreshVDisksList()
#        bRet = ( sVolName in self.lsVDisks_List )
#        return bRet

    def _sExpandShortVDiskName(self, sShortName):
        """ Проверяет наличие диска по _короткому_ имени. Если диск существует, возвращается его
        полное имя, если нет - возвращается пустая строка."""
        sRet = ""
        self._Dbg("_sExpandShortVDiskName(): Trying to expand short VDisk name '%s'" % sShortName)
        try:
            self.pSSSU.send("\n")
            self.pSSSU.expect(self.sPrompt)
            self.pSSSU.send('ls vdisk "%s"\n' % sShortName)
            if ( self.pSSSU.expect(["Status : 0", "Status : 1"]) == 0 ):
                # диск, вероятно, существует
                dDiskInfo = self._dGetVDInfo(sShortName, ["objectname", "objecttype"])
                if dDiskInfo["objecttype"] == "virtualdisk":
                    sRet = dDiskInfo["objectname"]
                    self._Dbg("FOUND long name '%s' for short vDisk name '%s'" % (sRet, sShortName))
                else:
                    self._Dbg("The object we found ('%s') isn't a correct VDisk" % sRet)
                    sRet=""
            else:
                self._Dbg("VDisk named '%s' not found" % sShortName)
                sRet = ""
        except pexpect.TIMEOUT:
            raise SSSU_Error("_sExpandShortVDiskName(): Connection to SSSU lost (timeout)")
        except pexpect.EOF:
            raise SSSU_Error("_sExpandShortVDiskName(): Connection to SSSU lost (EOF while reading")
        return sRet
    
#    def _sCacheMode(self, sVDisk):
#        """Возвращает 'writethrough' или 'writeback' в зависимости от того, в каком режиме кэша
#        работает VDisk."""
#        sRet = "writethrough"
#        self.pSSSU.send("\n")
#        if (self.pSSSU.expect([self.sPrompt, pexpect.EOF, pexpect.TIMEOUT]) == 0):
#            self.pSSSU.send('ls vdisk "%s"\n' % sVDisk)
#            if (self.pSSSU.expect(["Status : 0", "Error",   pexpect.EOF, pexpect.TIMEOUT]) == 0):
#                lLines = [l.strip() for l in self.pSSSU.before.split("\r\n")]  # list of returned strings
#                for sLine in lLines:
#                    if len(sLine) > 10 and sLine[0:10] == "writecache":
#                        sRet=sLine.split(": ")[1]
#                        break  # другие строки нас не интересуют
#            else:
#                self.pSSSU.expect(pexpect.TIMEOUT,timeout=0.1)                # get an error string
#                self._Err("_sCacheMode: Cannon get status of VDisk '%s', messages from SSSU are '%s'" %
#                          (sVDisk, self.pSSSU.before))
#                raise SSSU_Error("Cannot get cache status of VDisk")
#        else:
#            self._Err("_sCacheMode: lost connection to SSSU")
#            raise SSSU_Error("Connection to SSSU lost")
#        return sRet
#
    def _CacheToWriteThrough(self, sVDisk):
        """ Переключает кэш Vдиска, заданного параметром sVDisk, в режим  Write-Through"""
        try:
            self.pSSSU.send("\n")
            self.pSSSU.expect(self.sPrompt)
            if sVDisk[0:aiVDLen] != "\\Virtual Disks\\":
                sVDisk = self._sExpandShortVDiskName(sVDisk)
            if sVDisk:                                 # т.е. строка не пуста
#                sCacheMode = self._sCacheMode(sVDisk)
                # Мы уже проверили наличие диска, раскрыв его имя. Так что параметр "writecache" есть
                sCacheMode = self._dGetVDInfo(sVDisk,["writecache"])["writecache"]
                if sCacheMode == "writeback":
                    sCmd = 'SET VDISK "%s" WRITECACHE=WRITETHROUGH\n' %   sVDisk
                    self.pSSSU.send(sCmd)
                    iIdx = self.pSSSU.expect(["Status : 0", "Error:"])
                    if iIdx == 0:
                        self._Dbg("Write cache of disk '%s is set to Write-Through")
                    elif iIdx == 1:
                        self._Err("Error setting write cache mode to Write-Through")  # и что?
                elif sCacheMode == "writethrough":
                    self._Dbg("The cache of VDisk '%s' is already Write-Through" % sVDisk)
                else:
                    self._Err("UNKNOWN cache mode '%s' of VDisk '%s'" % (sCacheMode, sVDisk))
            else:      # if self._bHaveVDisk(sVDisk):
                self._Err("_CacheToWriteThrough: no such disk")
                raise SSSU_Error("Unknown VDisk name")
        except pexpect.TIMEOUT:
            raise SSSU_Error("_CacheToWriteThrough(): Connection to SSSU lost (timeout)")
        except pexpect.EOF:
            raise SSSU_Error("_CacheToWriteThrough(): Connection to SSSU lost (EOF while reading")
        return

    def _bNameUsed(self, sName):
        """Возвращает True, если имени sName соответствует какой-либо дискообразный
        объект и, следовательно, с этим именем нельзя создать диск или снэпшот."""
        self._Dbg("Searching for object named '%s'" % sName)
        bRet = False
        try:
            self.pSSSU.send("\n"); self.pSSSU.expect(self.sPrompt)
            sCmd = "LS VDISK"
            self.pSSSU.send(sCmd + "\n")
            self.pSSSU.expect(sCmd)
            self.pSSSU.expect(self.sPrompt)
            # Избавляемся от лишнего в выдаче - пробелов и всего, что НЕ имеет в имени компонент
            # "\Virtual Disks\"
            lObjects = [ s.strip() for s in self.pSSSU.before.split("\r\n")
                         if (s.strip()[0:15] == "\\Virtual Disks\\")]
            # Если нам дали имя, содержащее '\' - считаем его полным и откусываем компоненты.
            if sName.find("\\") >= 0:
                sVDCompare, sSSCompare = sName.split("\\")[-2:]
                if sSSCompare == "ACTIVE":
                    sSSCompare = ""
            else:
                # у нас краткое имя, и непонятно чего - диска или снэпшота
                sVDCompare = sSSCompare = sName
            # У VDisk-ов краткое имя в предпоследнем поле, у снэпшотов и контейнеров - в последнем
            for sLine in lObjects:
                lFields = sLine.split('\\')
                if lFields[-1] == sSSCompare or lFields[-2] == sVDCompare:
                    bRet = True
                    self._Dbg("_bNameUsed(): Name '%s' is used by object %s" % (sName, sLine))
                    break
                else:
                    pass
        except pexpect.TIMEOUT:
            raise SSSU_Error("_bNameUsed(): Connection to SSSU lost (timeout)")
        except pexpect.EOF:
            raise SSSU_Error("_bNameUsed(): Connection to SSSU lost (EOF while reading")
        return bRet

    def _bHaveSnapshot(self, sVDiskName, sSnapShotName):
        """ Проверяет наличие у диска sVDiskName снэпшота с именем sSnapShotName.
        Возвращает True, если такой снэпшот существует, False в противном случае."""
        self._Dbg("Searching for snapshot of VDisk '%s' named '%s'" % (sVDiskName, sSnapShotName))
        bRet = False
        # Is our snapshot name a full one? Then use a last component
        if sSnapShotName.find("\\") >= 0:
            sSnapShotName = sSnapShotName.split("\\")[-1]
        # Do we have a full name of VDisk?
        if sVDiskName.find("\\") < 0:   # short name of VDisk
            sSearchString = "\\" + sVDiskName + "\\" + sSnapShotName
        else:
            sSearchString = "\\".join(sVDiskName.split("\\")[0:-1])  # кроме последнего компонента
            sSearchString = sSearchString + "\\" + sSnapShotName
        sSearchLen = len(sSearchString)
        try:
            self.pSSSU.send("\n"); self.pSSSU.expect(self.sPrompt)
            sCmd = "LS SNAPSHOT"
            self.pSSSU.send(sCmd + "\n")
            self.pSSSU.expect(sCmd)
            self.pSSSU.expect(self.sPrompt)
            for sLine in [s.strip() for s in self.pSSSU.before.split("\r\n")]:
                self._Dbg("Searching for '%s' in '%s'" % (sSearchString, sLine))
                if sLine[-sSearchLen:] == sSearchString:
                    bRet = True
                    self._Dbg("Found snapshot of '%s' named '%s'" % (sVDiskName, sSnapShotName))
                    break
        except pexpect.TIMEOUT:
            raise SSSU_Error("_bHaveSnapshot(): Connection to SSSU lost (timeout)")
        except pexpect.EOF:
            raise SSSU_Error("_bHaveSnapshot(): Connection to SSSU lost (EOF while reading")
        return bRet

    def _MakeDASnapShot(self, sVDiskName, sSnapShotName, iRedundancy=5):
        """Создаёт Demand-Allocated снэпшот диска sVDiskName с именем sSnapShotName. Избыточность 
        указывается в виде цифры в параметре iRedundancy.  Предварительно проверяет наличие
        объекта с этим именем, если он есть, вызывает исключение."""
        aiSShot_Timeout = 5  # секунд
        self._Dbg("Creating snapshot '%s' of VDisk '%s'" % (sSnapShotName, sVDiskName))
        if not (iRedundancy in (0, 1, 5, 6)):
            self._Err("Unsupported VRAID level %d, changed to VRaid5" % iRedundancy)
            iRedundancy = 5
        if not self._bNameUsed(sSnapShotName):
            self._CacheToWriteThrough(sVDiskName)
            try:
                self.pSSSU.send("\n"); self.pSSSU.expect(self.sPrompt)
                sCmd = ('ADD SNAPSHOT "%s" VDISK="%s" ALLOCATION_POLICY=Demand REDUNDANCY=Vraid%d \n' % 
                        (sSnapShotName, sVDiskName, iRedundancy))
                self._Dbg("Command to run: <%s>" % sCmd)
                self.pSSSU.send(sCmd)
                iIdx = self.pSSSU.expect(["Status : 0", "Error:"], aiSShot_Timeout)
                if iIdx == 0:
                    # Успешное создание снэпшота
                    self.pSSSU.expect(self.sPrompt)
                    return self._bHaveSnapshot(sVDiskName, sSnapShotName)
                elif iIdx == 1:
                    # Ошибка при создании снэпшота, выдаём в лог всё, что нам сказал SSSU
                    self.pSSSU.expect(pexpect.TIMEOUT,0.5)
                    sMsg = self.pSSSU.before
                    self._Err("Cannot create snapshot, SSSU error: %s" % sMsg)
                    raise SSSU_Error("Cannot make snapshot of VDisk '%s' named '%s'" %
                                     (sVDiskName, sSnapShotName))
            except pexpect.TIMEOUT:
                sMsg = self.pSSSU.before
                self._Err("Cannot create snapshot, SSSU error: %s" % sMsg)
                raise SSSU_Error("Timeout making snapshot of VDisk '%s' named '%s'" %
                                 (sVDiskName, sSnapShotName))
            except pexpect.EOF:
                sMsg = self.pSSSU.before
                self._Err("Cannot create snapshot, SSSU error: %s" % sMsg)
                raise SSSU_Error("EOF while making snapshot of VDisk '%s' named '%s'" %
                                 (sVDiskName, sSnapShotName))
        else:
            # Объект с таким именем уже существует, поэтому создать его нельзя
            self._Err("Name '%s' ALREADY used by some object" % sSnapShotName)
            raise SSSU_Error("Name '%s' ALREADY used" % sSnapShotName)
        return

    def _DeleteSnapshot(self, sVDisk, sSnap):
        """Deletes the snapsnot of vDisk 'sVDisk' named 'sSnap'"""
        if self._bHaveSnapshot(sVDisk, sSnap):
            self._Dbg("_DeleteSnapshot: the snapshot '%s' of disk '%s' DOES exist" %
                      (sSnap, sVDisk))
            try:
                self.pSSSU.send("\n"); self.pSSSU.expect(self.sPrompt)
                sCmd = 'DELETE VDISK "%s" WAIT_FOR_COMPLETION' % sSnap
                self.pSSSU.send(sCmd + "\n")
                iIdx = self.pSSSU.expect(["Status : 0", "Error:", "Status : 1"])
                if iIdx == 0 and not self._bNameUsed(sSnap):
                    self._Dbg("Snapshot '%s' deleted successfully" % sSnap)
                else:
                    self._Err("I can not delete the snapshot '%s'" % sSnap)
                    self.expect(pexpect.TIMEOUT,0.1)
                    self._Err("Messages of SSSU: %s" % self.pSSSU.before )
            except pexpect.TIMEOUT:
                raise SSSU_Error("_DeleteSnapshot(): Connection to SSSU lost (timeout)")
            except pexpect.EOF:
                raise SSSU_Error("_DeleteSnapshot(): Connection to SSSU lost (EOF while reading")
        else:
            self._Err("_DeleteSnapshot: there isn't snapshot of vDisk '%s' named '%s'" % 
                      (sVDisk, sSnap))
        return

    def _lListSnapshots(self, sVDisk):
        """Returns a list of snapshots of given VDisk. Parameter: name of VDisk. Returns list of
        short snapshot names"""
        self._Dbg("_lListSnapShots called with parameter '%s'" % sVDisk)
        lSnaps = []
        sFullVDiskName = self._sExpandShortVDiskName(sVDisk)   # Получим короткое и длинное имена VDisk-а.
        if sFullVDiskName:
            sShortVDiskName = sFullVDiskName.split('\\')[-2]
            self._Dbg("Short name '%s', long name '%s'" % (sShortVDiskName, sFullVDiskName))
            sCmd = 'LS SNAPSHOT | grep "%s"' % sShortVDiskName
            # Мы можем обойтись коротким именем, так как оно уникально
            try:
                self.pSSSU.send("\n"); self.pSSSU.expect(self.sPrompt)
                self._Dbg("Sending command '%s'" % sCmd)
                self.pSSSU.send(sCmd + "\n")
                self.pSSSU.expect("grep.*$")  # move point after 'grep' call
                self.pSSSU.expect(self.sPrompt)
                lLongSnaps = [ l.strip() for l in self.pSSSU.before.split("\r\n") if len(l) > aiVDLen ]
                self._Dbg("Snapshots of disk '%s' list:\n%s" % (sVDisk, "\n".join(lLongSnaps)))
                for sSnapName in lLongSnaps:
                    lSnaps.append(sSnapName.split("\\")[-1])
            except pexpect.TIMEOUT:
                raise SSSU_Error("_lListSnapshots(): Connection to SSSU lost (timeout)")
            except pexpect.EOF:
                raise SSSU_Error("_lListSnapshots(): Connection to SSSU lost (EOF while reading")        
        else:
            self._Err("_lListSnapshots(): name '%s' cannot be expanded" % sVDisk)
        return lSnaps

    def _iGetCreationTime(self,sVDisk):
        """Возвращает время создания объекта (диска, снэпшота и т.д) 
        в секундах с 1970-01-01 00:00:00"""
        iRet = 0
        lTimes = list(self._dGetVDInfo(sVDisk, ["creationdatetime"]).values())
        if lTimes:     # список не пуст
            iRet = int(time.mktime(time.strptime(lTimes[0], "%d-%b-%Y %H:%M:%S")))
        else:
            self._Err("_iGetCreationTime(): Cannot receive creation date of %s" % sVDisk)
        return iRet

    def _Close(self):
        """Closes the interface"""
        self._Dbg("Closing SSSU connection")
        self.pSSSU.send("\n")
        self.pSSSU.sendline("exit")
        self.pSSSU.expect([ pexpect.EOF, pexpect.TIMEOUT ],10)
        if self.pSSSU.isalive():
            self.pSSSU.terminate()
            return

if __name__ == "__main__":
    print("This is a library, not executable")
    sys.exit(1)

# vim: expandtab:tabstop=4:softtabstop=4:shiftwidth=4
