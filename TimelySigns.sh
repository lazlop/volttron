export VOLTTRON_HOME=~/.TimelySigns-volttron

source env/bin/activate
if [ "$1" = '--start' ]; then
    volttron -vv -l TimelySigns-volttron.log > TimelySigns-volttron.log 2>&1 &
else
    vctl status
fi

tail -f TimelySigns-volttron.log
