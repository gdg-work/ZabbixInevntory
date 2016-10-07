#
# check the time between two consecutive data points in Zabbix. We have a timestamps in two
# formats: text (10/02/2016 07:13:40 PM) and UNIX (1475424820). The file consists of lines
# like:
# 10/02/2016 11:48:57 AM 1475398137 1475398137
# 
library(lubridate)
library(ggplot2)

df <- read.fwf(file="vmsrv29.txt", widths=c(22, 11,11))
names(df) <- c("TimeStamp", "UT1", "UT2")
df$TimeStamp <- mdy_hms(df$TimeStamp)
print(df)
ggplot(df, aes(TimeStamp, UT1)) + geom_point()
