sudo cp *.service /etc/systemd/system/
sudo cp *.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now reconnect-star.timer
systemctl list-timers --all
