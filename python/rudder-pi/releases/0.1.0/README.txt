#sudo apt update
#sudo apt install -y pigpio python3-pigpio
#sudo systemctl enable --now pigpiod


sudo nano /boot/firmware/config.txt
dtoverlay=pwm-2chan

reboot

pwmchip0/pwm0 → GPIO18 (PWM0)
pwmchip0/pwm1

# Export PWM channels
echo 0 | sudo tee /sys/devices/platform/soc/fe20c000.pwm/pwm/pwmchip0/export
echo 1 | sudo tee /sys/devices/platform/soc/fe20c000.pwm/pwm/pwmchip0/export

# Check they exist now
ls -al /sys/devices/platform/soc/fe20c000.pwm/pwm/pwmchip0/

BASE=/sys/class/pwm/pwmchip0

# Disable first (important when changing period)
echo 0 | sudo tee $BASE/pwm0/enable
echo 0 | sudo tee $BASE/pwm1/enable

# 50 Hz
echo 20000000 | sudo tee $BASE/pwm0/period
echo 20000000 | sudo tee $BASE/pwm1/period

# 1.0ms pulse (ESC min/stop)
echo 1000000 | sudo tee $BASE/pwm0/duty_cycle
echo 1000000 | sudo tee $BASE/pwm1/duty_cycle

# Enable output
echo 1 | sudo tee $BASE/pwm0/enable
echo 1 | sudo tee $BASE/pwm1/enable


BASE=/sys/class/pwm/pwmchip0



# 1) Kanal exportiert?
ls $BASE/pwm0 || echo "pwm0 fehlt"

# 2) Disable bevor wir ändern
echo 0 | sudo tee $BASE/pwm0/enable

# 3) 50 Hz setzen (einmal)
echo 20000000 | sudo tee $BASE/pwm0/period

# 4) Enable
echo 1 | sudo tee $BASE/pwm0/enable



# ESC min / stop
echo 1000000 | sudo tee $BASE/pwm0/duty_cycle

# leicht über min
echo 1200000 | sudo tee $BASE/pwm0/duty_cycle

# noch etwas mehr
echo 1400000 | sudo tee $BASE/pwm0/duty_cycle



# ESC start:
echo 1000000 | sudo tee $BASE/pwm0/duty_cycle
sleep 2
echo 1200000 | sudo tee $BASE/pwm0/duty_cycle








probiert:

pi@RM031:~ $ echo 0 | sudo tee $BASE/pwm0/enable
0
pi@RM031:~ $ echo 20000000 | sudo tee $BASE/pwm0/period
20000000
pi@RM031:~ $ echo 1 | sudo tee $BASE/pwm0/enable
1
pi@RM031:~ $ echo 1000000 | sudo tee $BASE/pwm0/duty_cycle
1000000
pi@RM031:~ $ echo 1200000 | sudo tee $BASE/pwm0/duty_cycle
1200000
pi@RM031:~ $ echo 1400000 | sudo tee $BASE/pwm0/duty_cycle
1400000
pi@RM031:~ $ echo 1000000 | sudo tee $BASE/pwm0/duty_cycle
1000000
pi@RM031:~ $ sleep 2
pi@RM031:~ $ echo 1200000 | sudo tee $BASE/pwm0/duty_cycle
1200000
pi@RM031:~ $ 



sudo mount -t debugfs debugfs /sys/kernel/debug
sudo cat /sys/kernel/debug/pwm