---
title: "Журналирование событий в наборе программ инвентаризации оборудования с помощью Zabbix"
output:
    html_document:
	toc: false    
---

Программы используют общую библиотеку «logging» и общие настройки журналирования. Эти
настройки содержатся в файле ```inventoryLogger.py``` каталога ```/usr/lib/zabbix/externalscripts``` в виде YAML строки. 
Настройки журналирования можно изменить редактированием этой строки.

Конфигурация журналирования на текущий момент (2016-08-30):

```{python}
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
        filename: /tmp/zabinventory.log
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
            level: INFO
            handlers: [ console, logfile ]
        ibm_FlashSystem_SW:
            level: INFO
            handlers: [ console, logfile ]
        ibm_FAStT:
            level: INFO
            handlers: [ console, logfile ]
        zabbixInterface:
            level: INFO
            handlers: [ console, logfile ]
        Discovery:
            level: INFO
            handlers: [ console, logfile ]
        Srv.Discovery:
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
```

Разберём эту конфигурацию по шагам:

# Раздел «formatters»

```{yaml}
formatters:
  simple:
    format: '%(asctime)s: %(name)s - %(levelname)s - %(message)s'
  brief:
    format: '%(name)s:  %(levelname)s - %(message)s'
```

Здесь определяется формат сообщений, то есть какие поля и в каком порядке будут выведены в поток журнала (консоль или лог-файл, см. раздел «handlers»).
Доступные поля перечислены в документации на модуль logging: [Formatters](https://docs.python.org/3.4/library/logging.html#logging.Formatter) и
[Attributes](https://docs.python.org/3.4/library/logging.html#logrecord-attributes).  При текущей настройке вывод в файл отличается от консольного наличием меток
времени.

## Раздел «handlers»

В этом разделе определяется, что происходит с сообщениями. Определены два потока: «console» и «logfile», с каждым из которых связан форматтер (из
предыдущего раздела) и минимальный уровень важности сообщения, необходимый для выдачи сообщения в этот поток. Уровни определены в документации
на модуль logging: [Logging Levels](https://docs.python.org/3.4/library/logging.html#logging-levels).


### Поток «console»

```{yaml}
console:
    class : logging.StreamHandler
    formatter: brief
    level   : WARNING
    stream  : ext://sys.stderr

```

Имеет смысл изменение только минимального уровня важности сообщений для вывода в поток.  Этот поток связан со стандартным потоком для сообщений
об ошибках в операционной системе (```/dev/stderr``` или ```/dev/fd/2```)

### Поток «logfile»

```{yaml}
logfile:
  class: logging.handlers.RotatingFileHandler
  formatter: simple
  encoding: utf8
  level: DEBUG
  filename: /tmp/zabinventory.log
  # Max log file size: 1 MB, then the file will be rotated
  maxBytes: 1048576
  backupCount: 1
```

Первая строка определяет поведение потока как запись сообщений в файл, который по достижении заданного
объёма «ротируется», то есть переименовывается с расширением «.1», «.2» и т. п.  Сохраняется некоторое 
заданное число таких переименованных файлов (определяется параметром ```backupcount```).

Важные параметры:

 - level — определяет минимальный уровень важности сообщений для записи в файл;

 - filename — определяет имя файла с журналом. Файл должен принадлежать тому пользователю, от имени которого
   запускаются программы, в среде заказчика это пользователь "zabbix" из группы "zabbix".  По крайней мере, нужен
   доступ к файлу и к каталогу, в котором он находится, на запись (доступ к каталогу необходим для ротации файлов);

 - maxBytes — предельный размер файла с журналом, по достижении этого размера файл будет ротирован;

 - backupCount — количество ротированных файлов, которое должно сохраняться (в текущей конфигурации — один).

## Раздел «root»

```{yaml}
root:
    level: INFO
```

Этот раздел работает, когда модуль, из которого приходит сообщение, не определён (например, его нет в списке «loggers»).

## Раздел «loggers»

```{yaml}
loggers:
    __main__:
	level: INFO
	handlers: [ console, logfile ]
    hp3Par:
	level: INFO
	handlers: [ console, logfile ]
    hpeva_sssu:
	level: INFO
	handlers: [ console, logfile ]
    ibm_FlashSystem_SW:
	level: INFO
	handlers: [ console, logfile ]
    .....
```

Этот раздел позволяет настроить различную обработку сообщений из различных модулей программного комплекса.
В приведённом фрагменте настраивается выдача сообщений из модулей для работы с HP EVA (```hpeva_sssu```), HP 3Par (```hp3Par```) и
IBM FlashSystem FW9000 (```ibm_FlashSystem_SW```). Для  модулей, не перечисленных в разделе «loggers», действует настройка из 
раздела «root».

Для каждого модуля указываются два параметра: потоки, куда выводятся сообщения (консоль и/или журнальный файл) и минимальный 
уровень важности обрабатываемых сообщений.

Список уровней важности приведён в документации на модуль «logging»: [Logging Levels](https://docs.python.org/3.4/library/logging.html#logging-levels).

Уровни важности, определяемые в разделе «loggers», взаимодействуют с теми, которые 
описаны в разделе «handlers» следующим образом: сообщение поступает на обработку, если
его уровень равен или выше указанного в «loggers», и попадает в поток, если значение
параметра ```level``` у потока ниже или равно уровню сообщения. Для примера, если выставить
для логгера ```hpeva_sssu``` уровень DEBUG, то при существующих настройках потоков (level=WARNING
для консоли и DEBUG для файла с журналом) обработка сообщений различных уровней будет происходить
так:

  - NOTSET — не будет обрабатываться;

  - DEBUG — будет выведено в файл;

  - INFO — будет выведено в файл;

  - WARNING — будет выведено в файл _и_ на консоль.

  - ERROR, CRITICAL — будут обработаны аналогично WARNING.

Запись ```__main__``` используется при отладке отдельных модулей.

## Пример записей в лог-файле

```{text}
2016-08-30 03:08:04,337: Servers_Feed_Data - DEBUG - _oCollectInfoFromServer called for server vmsrv04.msk.protek.local, type esxi_amm
2016-08-30 03:08:17,752: ibm_BladeCenter_AMM - ERROR - WBEM error when collecting information: vmsrv04.msk.protek.local
2016-08-30 03:08:17,753: ibm_BladeCenter_AMM - ERROR - Error getting data from about disk subsystem via WBEM
2016-08-30 03:08:18,551: ibm_BladeCenter_AMM - INFO - Finished making Zabbix items and triggers
2016-08-30 03:08:18,552: Servers_Feed_Data - INFO - Processing server sus
2016-08-30 03:08:18,552: Servers_Feed_Data - DEBUG - _oCollectInfoFromServer called for server 10.44.12.2, type aix_hmc
2016-08-30 03:08:21,350: Servers_Feed_Data - INFO - Processing server ezakazdb02.protek.ru
2016-08-30 03:08:21,350: Servers_Feed_Data - DEBUG - _oCollectInfoFromServer called for server ezakazdb02.protek.ru, type esxi_amm
2016-08-30 03:08:24,406: ibm_BladeCenter_AMM - ERROR - WBEM error when initializing WBEM_Disks interface of server ezakazdb02.protek.ru, msg: (WBEM_Exception(('Invalid authentication data for vCenter ticket to host ezakazdb02.protek.ru',),),)
2016-08-30 03:08:24,407: ibm_BladeCenter_AMM - ERROR - Error getting data from about disk subsystem via WBEM
2016-08-30 03:08:24,905: ibm_BladeCenter_AMM - INFO - Finished making Zabbix items and triggers

```
