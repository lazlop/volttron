export VOLTTRON_HOME=~/.cfhA1

source env/bin/activate
if [ "$1" = '--start' ]; then
    volttron -vv -l cfhA1.log > cfhA1.log 2>&1 &
else
    vctl status
fi

tail -f cfhA1.log
