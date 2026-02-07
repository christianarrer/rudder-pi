Nur Python-Venv/Deps:

python3 setup.py


System-Setup (PWM + Hostname + Avahi + MediaMTX + Config + Services):

python3 setup.py --system --install-pwm-export-service


Wenn du willst, dass er bei Bedarf selbst rebootet:

python3 setup.py --system --install-pwm-export-service --reboot