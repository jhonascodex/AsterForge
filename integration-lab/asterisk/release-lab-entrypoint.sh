#!/bin/sh
set -eu

install -d -o asterisk -g asterisk -m 0755 \
  /var/log/asterisk \
  /var/run/asterisk \
  /var/spool/asterisk/monitor

timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf '[%s] WARNING[1] release_lab.c: release-lab-log endpoint=lab-endpoint trunk=lab-trunk-registration uniqueid=lab-log-1 linkedid=lab-log-1 password=lab-sensitive-value\n' \
  "$timestamp" >> /var/log/asterisk/messages
chown asterisk:asterisk /var/log/asterisk/messages
chmod 0644 /var/log/asterisk/messages

su-exec asterisk:asterisk node_exporter \
  --web.listen-address=:9100 \
  --collector.disable-defaults \
  --collector.cpu \
  --collector.loadavg \
  --collector.meminfo \
  --collector.netdev \
  --collector.stat \
  --collector.time \
  --collector.textfile \
  --collector.textfile.directory=/var/lib/node_exporter/textfile_collector &

exec asterisk -f -U asterisk -G asterisk -vvv
