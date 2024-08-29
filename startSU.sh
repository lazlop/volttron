export VOLTTRON_HOME=~/.openbosSU

source env/bin/activate
if [ "$1" = '--start' ]; then
    volttron -vv -l openbosSU.log > openbosSU.log 2>&1 &
else
    vctl status
fi

tail -f openbosSU.log
