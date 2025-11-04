export VOLTTRON_HOME=~/.bethel

source env/bin/activate
if [ "$1" = '--start' ]; then
    volttron -vv -l bethel.log > bethel.log 2>&1 &
else
    vctl status
fi

tail -f bethel.log
